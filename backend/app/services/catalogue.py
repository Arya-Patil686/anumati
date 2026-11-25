"""Product catalogue: normalised schema, ingest, lookup and search.

Schema is section 7 of docs/spec.pdf. Prices are integer paise — a Kaggle CSV's rupee
floats are converted once, here, and never seen again.

Every text field on a Product is merchant-controlled and therefore **untrusted**
(CLAUDE.md rule 3). `Product.untrusted_text()` is the only thing that should ever reach
a prompt, and only inside a delimited data block.

Run the ingest:

    python -m app.services.catalogue ingest            # CSV from .env, else synthetic
    python -m app.services.catalogue ingest --synthetic
"""

from __future__ import annotations

import argparse
import json
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
from pydantic import BaseModel, ConfigDict, Field

from app.config import settings
from app.models.common import Paise

CATALOGUE_COLUMNS = [
    "sku",
    "title",
    "description",
    "category_path",
    "brand",
    "price_paise",
    "attributes_json",
    "is_subscription",
    "merchant_id",
    "delivery_days",
]

# Column names seen in the BigBasket and Flipkart sample datasets, mapped onto ours.
# Matching is case-insensitive on the normalised header.
SOURCE_COLUMN_ALIASES: dict[str, tuple[str, ...]] = {
    "sku": ("sku", "index", "id", "product_id", "uniq_id", "pid"),
    "title": ("title", "product", "product_name", "name"),
    "description": ("description", "product_description", "about_product"),
    "category_path": ("category_path", "category", "product_category_tree", "sub_category"),
    "brand": ("brand", "brand_name", "manufacturer"),
    "price": ("price_paise", "sale_price", "discounted_price", "price", "retail_price"),
    "pack_size": ("pack_size", "quantity", "weight", "size"),
}


class Product(BaseModel):
    """One catalogue entry, normalised."""

    model_config = ConfigDict(extra="forbid")

    sku: str
    title: str
    description: str = ""
    category_path: str = ""
    brand: str = ""
    price_paise: Paise = 0
    attributes: dict[str, Any] = Field(default_factory=dict)
    is_subscription: bool = False
    merchant_id: str = "m_default"
    delivery_days: int = 3

    def untrusted_text(self) -> dict[str, str]:
        """The merchant-controlled fields. Never interpolate these into an instruction."""
        return {
            "title": self.title,
            "description": self.description,
            "brand": self.brand,
            "category_path": self.category_path,
        }

    def to_row(self) -> dict[str, Any]:
        return {
            "sku": self.sku,
            "title": self.title,
            "description": self.description,
            "category_path": self.category_path,
            "brand": self.brand,
            "price_paise": int(self.price_paise),
            "attributes_json": json.dumps(self.attributes, ensure_ascii=False, sort_keys=True),
            "is_subscription": bool(self.is_subscription),
            "merchant_id": self.merchant_id,
            "delivery_days": int(self.delivery_days),
        }

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Product:
        attributes = row.get("attributes_json") or "{}"
        return cls(
            sku=str(row["sku"]),
            title=str(row.get("title") or ""),
            description=str(row.get("description") or ""),
            category_path=str(row.get("category_path") or ""),
            brand=str(row.get("brand") or ""),
            price_paise=int(row.get("price_paise") or 0),
            attributes=json.loads(attributes) if isinstance(attributes, str) else attributes,
            is_subscription=bool(row.get("is_subscription")),
            merchant_id=str(row.get("merchant_id") or "m_default"),
            delivery_days=int(row.get("delivery_days") or 3),
        )


# --- money ---------------------------------------------------------------------


def rupees_to_paise(value: Any) -> int:
    """Convert a messy CSV price to integer paise. Rule 1: this is the only conversion."""
    if value is None:
        return 0
    text = str(value).strip().replace("₹", "").replace(",", "").replace("Rs.", "").strip()
    if not text or text.lower() in {"nan", "none", "-"}:
        return 0
    try:
        # Round half-up on the rupee value, then scale. Never keep the float.
        return round(float(text) * 100)
    except ValueError:
        return 0


# --- ingest --------------------------------------------------------------------


def _normalise_header(name: str) -> str:
    return str(name).strip().lower().replace(" ", "_").replace("-", "_")


def resolve_columns(columns: list[str]) -> dict[str, str]:
    """Map our field names onto whichever columns this CSV actually has."""
    lookup = {_normalise_header(c): c for c in columns}
    resolved: dict[str, str] = {}
    for field_name, aliases in SOURCE_COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in lookup:
                resolved[field_name] = lookup[alias]
                break
    return resolved


def normalise_csv(csv_path: Path) -> pd.DataFrame:
    """Read a product CSV and return the normalised catalogue frame."""
    raw = pd.read_csv(csv_path)
    cols = resolve_columns(list(raw.columns))
    if "title" not in cols:
        raise ValueError(f"{csv_path.name}: no recognisable product-title column")

    products: list[Product] = []
    for i, row in raw.iterrows():
        title = str(row[cols["title"]]).strip()
        if not title or title.lower() == "nan":
            continue

        attributes: dict[str, Any] = {}
        if "pack_size" in cols:
            pack = str(row[cols["pack_size"]]).strip()
            if pack and pack.lower() != "nan":
                attributes["pack_size"] = pack

        products.append(
            Product(
                sku=str(row[cols["sku"]]) if "sku" in cols else f"sku_{i:06d}",
                title=title,
                description=str(row.get(cols.get("description", ""), "") or ""),
                category_path=str(row.get(cols.get("category_path", ""), "") or ""),
                brand=str(row.get(cols.get("brand", ""), "") or ""),
                price_paise=rupees_to_paise(row.get(cols.get("price", ""))),
                attributes=attributes,
            )
        )

    return pd.DataFrame([p.to_row() for p in products], columns=CATALOGUE_COLUMNS)


def write_parquet(frame: pd.DataFrame, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path


def ingest(csv_path: Path | None = None, *, synthetic: bool = False) -> Path:
    """Build data/catalogue/catalogue.parquet from a CSV, or synthesise one."""
    from app.services.catalogue_synth import synthetic_catalogue

    out = settings.catalogue_parquet_path
    source = csv_path or settings.catalogue_csv_path

    if synthetic or not source.exists():
        frame = pd.DataFrame(
            [p.to_row() for p in synthetic_catalogue()], columns=CATALOGUE_COLUMNS
        )
        reason = "requested" if synthetic else f"{source} not found"
        print(f"synthetic catalogue ({reason}): {len(frame)} SKUs")
    else:
        frame = normalise_csv(source)
        print(f"normalised {source.name}: {len(frame)} SKUs")

    write_parquet(frame, out)
    print(f"wrote {out}")
    return out


# --- service -------------------------------------------------------------------


class CatalogueService:
    """Load once, then look up and search. No network, no LLM."""

    def __init__(self, products: list[Product]) -> None:
        self.products = products
        self._by_sku = {p.sku: p for p in products}

    @classmethod
    def from_parquet(cls, path: Path | None = None) -> CatalogueService:
        target = path or settings.catalogue_parquet_path
        frame = pd.read_parquet(target)
        return cls([Product.from_row(row) for row in frame.to_dict(orient="records")])

    @classmethod
    def synthetic(cls) -> CatalogueService:
        from app.services.catalogue_synth import synthetic_catalogue

        return cls(synthetic_catalogue())

    def __len__(self) -> int:
        return len(self.products)

    def get(self, sku: str) -> Product | None:
        return self._by_sku.get(sku)

    def by_category(self, path_prefix: str) -> list[Product]:
        return [p for p in self.products if p.category_path.startswith(path_prefix)]

    def search(
        self,
        query: str,
        *,
        limit: int = 20,
        category_path: str | None = None,
        max_price_paise: int | None = None,
    ) -> list[Product]:
        """Token-overlap search over title and brand. Deliberately dumb and offline.

        The agent's own weak search is part of what the benchmark exercises; a smarter
        retriever here would hide the mistakes ANUMATI is meant to catch.
        """
        terms = [t for t in query.lower().split() if t]
        scored: list[tuple[int, Product]] = []

        for product in self.products:
            if category_path and not product.category_path.startswith(category_path):
                continue
            if max_price_paise is not None and product.price_paise > max_price_paise:
                continue

            haystack = f"{product.title} {product.brand} {product.category_path}".lower()
            score = sum(1 for t in terms if t in haystack)
            if score:
                scored.append((score, product))

        scored.sort(key=lambda pair: (-pair[0], pair[1].price_paise))
        return [p for _, p in scored[:limit]]


@lru_cache
def get_catalogue() -> CatalogueService:
    """Parquet if it has been built, synthetic otherwise, so nothing blocks on ingest."""
    path = settings.catalogue_parquet_path
    if path.exists():
        return CatalogueService.from_parquet(path)
    return CatalogueService.synthetic()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="catalogue")
    sub = parser.add_subparsers(dest="command", required=True)
    ing = sub.add_parser("ingest", help="build data/catalogue/catalogue.parquet")
    ing.add_argument("--csv", type=Path, default=None)
    ing.add_argument("--synthetic", action="store_true")

    args = parser.parse_args(argv)
    if args.command == "ingest":
        ingest(args.csv, synthetic=args.synthetic)
    return 0


if __name__ == "__main__":
    sys.exit(main())
