"""Catalogue normalisation, the synthetic fallback, and the taxonomy."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.services.catalogue import (
    CATALOGUE_COLUMNS,
    CatalogueService,
    Product,
    normalise_csv,
    resolve_columns,
    rupees_to_paise,
    write_parquet,
)
from app.services.catalogue_synth import DEMO_PRODUCTS, synthetic_catalogue
from app.services.engine.taxonomy import get_taxonomy, leaf_of, normalise_term

# --- money ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("449.00", 44_900),
        ("₹4,499", 449_900),
        ("Rs.99", 9_900),
        ("1,234.56", 123_456),
        ("", 0),
        ("nan", 0),
        (None, 0),
        ("not a price", 0),
    ],
)
def test_rupees_to_paise(raw: object, expected: int) -> None:
    result = rupees_to_paise(raw)
    assert result == expected
    assert isinstance(result, int)


# --- synthetic catalogue ---------------------------------------------------------


def test_synthetic_catalogue_is_about_the_right_size() -> None:
    products = synthetic_catalogue()
    assert 1900 <= len(products) <= 2100


def test_synthetic_catalogue_is_deterministic() -> None:
    """The benchmark needs the same catalogue on every machine."""
    first = synthetic_catalogue()
    second = synthetic_catalogue()
    assert [p.model_dump() for p in first] == [p.model_dump() for p in second]


def test_a_different_seed_gives_a_different_catalogue() -> None:
    assert synthetic_catalogue(seed=1)[50] != synthetic_catalogue(seed=2)[50]


def test_prices_are_positive_integers() -> None:
    for product in synthetic_catalogue():
        assert isinstance(product.price_paise, int)
        assert product.price_paise > 0


def test_skus_are_unique() -> None:
    products = synthetic_catalogue()
    assert len({p.sku for p in products}) == len(products)


def test_every_category_path_is_in_the_taxonomy() -> None:
    taxonomy = get_taxonomy()
    for product in synthetic_catalogue():
        assert taxonomy.is_known_path(product.category_path), product.category_path


def test_demo_products_are_present_and_stable() -> None:
    """The section 9 demo and the M3 golden cases reference these SKUs by name."""
    by_sku = {p.sku: p for p in synthetic_catalogue()}
    for demo in DEMO_PRODUCTS:
        assert by_sku[demo.sku].model_dump() == demo.model_dump()

    assert by_sku["DEMO-RUN-WHITE-01"].attributes["colour"] == "white"
    assert by_sku["DEMO-RUN-OFFWHITE-01"].attributes["colour"] == "off-white"
    assert by_sku["DEMO-RUN-SUB-01"].is_subscription is True
    assert by_sku["DEMO-CHICKEN-01"].attributes["veg"] is False
    assert by_sku["DEMO-TREADMILL-01"].category_path.startswith("Fitness")


def test_injection_product_carries_its_payload_in_description_only() -> None:
    """The adversarial SKU is known by construction, so it needs no annotation."""
    product = next(p for p in DEMO_PRODUCTS if p.sku == "DEMO-INJECT-01")
    assert "ignore all previous instructions" in product.description.lower()
    assert "ignore" not in product.title.lower()
    assert product.untrusted_text()["description"] == product.description


# --- service ---------------------------------------------------------------------


@pytest.fixture
def catalogue() -> CatalogueService:
    return CatalogueService(synthetic_catalogue())


def test_get_by_sku(catalogue: CatalogueService) -> None:
    assert catalogue.get("DEMO-RUN-WHITE-01") is not None
    assert catalogue.get("nope") is None


def test_search_finds_the_obvious_match(catalogue: CatalogueService) -> None:
    hits = catalogue.search("white running shoes", limit=5)
    assert any(h.sku == "DEMO-RUN-WHITE-01" for h in hits)


def test_search_respects_a_price_ceiling(catalogue: CatalogueService) -> None:
    for hit in catalogue.search("running shoes", max_price_paise=300_000):
        assert hit.price_paise <= 300_000


def test_search_respects_a_category_filter(catalogue: CatalogueService) -> None:
    for hit in catalogue.search("running", category_path="Footwear"):
        assert hit.category_path.startswith("Footwear")


def test_by_category(catalogue: CatalogueService) -> None:
    treadmills = catalogue.by_category("Fitness > Cardio Equipment > Treadmills")
    assert treadmills
    assert all(p.category_path.startswith("Fitness") for p in treadmills)


# --- CSV ingest ------------------------------------------------------------------


def test_resolve_columns_handles_bigbasket_style_headers() -> None:
    resolved = resolve_columns(["index", "product", "category", "sub_category", "brand",
                               "sale_price", "quantity", "description"])
    assert resolved["sku"] == "index"
    assert resolved["title"] == "product"
    assert resolved["price"] == "sale_price"
    assert resolved["brand"] == "brand"


def test_normalise_csv_round_trips_through_parquet(tmp_path: Path) -> None:
    csv_path = tmp_path / "products.csv"
    pd.DataFrame(
        [
            {"index": 1, "product": "Chakki Atta", "brand": "Annapurna",
             "category": "Grocery", "sale_price": "325.00", "quantity": "5 kg",
             "description": "Whole wheat flour"},
            {"index": 2, "product": "Running Shoes", "brand": "Aeron",
             "category": "Footwear", "sale_price": "4,499", "quantity": "UK 9",
             "description": "Mesh upper"},
        ]
    ).to_csv(csv_path, index=False)

    frame = normalise_csv(csv_path)
    assert list(frame.columns) == CATALOGUE_COLUMNS
    assert frame.loc[0, "price_paise"] == 32_500
    assert frame.loc[1, "price_paise"] == 449_900

    parquet_path = write_parquet(frame, tmp_path / "catalogue.parquet")
    service = CatalogueService.from_parquet(parquet_path)
    assert len(service) == 2

    atta = service.get("1")
    assert atta is not None
    assert atta.attributes["pack_size"] == "5 kg"
    assert isinstance(atta.price_paise, int)


def test_normalise_csv_skips_rows_without_a_title(tmp_path: Path) -> None:
    csv_path = tmp_path / "messy.csv"
    pd.DataFrame(
        [{"product": "Real Item", "sale_price": "10"}, {"product": "", "sale_price": "20"}]
    ).to_csv(csv_path, index=False)
    assert len(normalise_csv(csv_path)) == 1


def test_normalise_csv_needs_a_title_column(tmp_path: Path) -> None:
    csv_path = tmp_path / "bad.csv"
    pd.DataFrame([{"nothing": 1}]).to_csv(csv_path, index=False)
    with pytest.raises(ValueError, match="product-title"):
        normalise_csv(csv_path)


def test_product_row_round_trip() -> None:
    product = Product(
        sku="X1", title="T", price_paise=100, attributes={"colour": "white", "veg": True}
    )
    assert Product.from_row(product.to_row()) == product


# --- taxonomy ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("term", "expected_path"),
    [
        ("running shoes", "Footwear > Sports > Running"),
        ("daudne wale shoes", "Footwear > Sports > Running"),
        ("treadmill", "Fitness > Cardio Equipment > Treadmills"),
        ("atta", "Grocery > Staples > Atta & Flours"),
        ("chakki atta", "Grocery > Staples > Atta & Flours"),
        ("chawal", "Grocery > Staples > Rice"),
        ("doodh", "Grocery > Dairy > Milk"),
        ("sarson ka tel", "Grocery > Edible Oils > Mustard Oil"),
    ],
)
def test_taxonomy_resolves_english_and_hinglish(term: str, expected_path: str) -> None:
    match = get_taxonomy().resolve(term)
    assert match is not None
    assert match.path == expected_path
    assert match.is_near_miss is False


@pytest.mark.parametrize("term", ["trainers", "sports shoes", "shoes", "trail shoes"])
def test_near_miss_terms_are_flagged_and_less_confident(term: str) -> None:
    match = get_taxonomy().resolve(term)
    assert match is not None
    assert match.is_near_miss is True
    assert match.confidence < 0.9
    assert match.alternatives


def test_longest_term_wins_in_free_text() -> None:
    """'running shoes' must beat the near-miss 'shoes', or every case goes ambiguous."""
    match = get_taxonomy().find_in("I want white running shoes under 5000")
    assert match is not None
    assert match.path == "Footwear > Sports > Running"
    assert match.is_near_miss is False


def test_find_in_returns_none_for_unknown_text() -> None:
    assert get_taxonomy().find_in("a quantum flux capacitor") is None


def test_a_path_is_a_synonym_for_itself() -> None:
    taxonomy = get_taxonomy()
    for path in taxonomy.paths:
        match = taxonomy.resolve(path)
        assert match is not None and match.path == path


def test_normalise_term_strips_punctuation_and_case() -> None:
    assert normalise_term("  Running Shoes!! ") == "running shoes"


def test_leaf_of() -> None:
    assert leaf_of("Footwear > Sports > Running") == "Running"
