"""The four tools a shopping agent is given.

Deliberately the same shape a tool-calling LLM would get: a name, typed arguments, a JSON
result. Keeping them this way means swapping the rule-based planner for a model later
changes the planner only, not the surface the agent acts through.

Every call is recorded on the `ToolLog`, which is what the console shows and what a
dispute pack quotes when it has to say what the agent actually did.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from app.models.cart import Cart, CartItem
from app.models.common import utc_now
from app.services.catalogue import CatalogueService, Product, cart_item_from_product


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    result_summary: str


@dataclass
class ToolLog:
    calls: list[ToolCall] = field(default_factory=list)

    def record(self, name: str, arguments: dict[str, Any], summary: str) -> None:
        self.calls.append(ToolCall(name=name, arguments=arguments, result_summary=summary))

    def as_dicts(self) -> list[dict[str, Any]]:
        return [
            {"tool": c.name, "arguments": c.arguments, "result": c.result_summary}
            for c in self.calls
        ]


class ShoppingTools:
    """What the agent can do. It cannot invent a product or a price."""

    def __init__(self, catalogue: CatalogueService, log: ToolLog | None = None) -> None:
        self.catalogue = catalogue
        self.log = log or ToolLog()
        self._lines: list[CartItem] = []

    # --- tools ---------------------------------------------------------------

    def search_catalogue(
        self,
        query: str,
        *,
        category_path: str | None = None,
        max_price_paise: int | None = None,
        limit: int = 10,
    ) -> list[Product]:
        hits = self.catalogue.search(
            query, limit=limit, category_path=category_path, max_price_paise=max_price_paise
        )
        self.log.record(
            "search_catalogue",
            {"query": query, "category_path": category_path,
             "max_price_paise": max_price_paise},
            f"{len(hits)} result(s)" + (f", top {hits[0].sku}" if hits else ""),
        )
        return hits

    def get_product(self, sku: str) -> Product | None:
        product = self.catalogue.get(sku)
        self.log.record(
            "get_product", {"sku": sku},
            f"{product.title} at {product.price_paise} paise" if product else "not found",
        )
        return product

    def add_to_cart(self, sku: str, qty: int = 1) -> CartItem | None:
        product = self.catalogue.get(sku)
        if product is None:
            self.log.record("add_to_cart", {"sku": sku, "qty": qty}, "unknown sku")
            return None

        line = cart_item_from_product(product, qty=qty)
        self._lines.append(line)
        self.log.record(
            "add_to_cart", {"sku": sku, "qty": qty},
            f"added {qty} x {product.title} ({line.line_total_paise} paise)",
        )
        return line

    def build_cart(
        self,
        mandate_id: str,
        *,
        merchant_id: str | None = None,
        shipping_paise: int = 0,
        tax_paise: int = 0,
        delivery_days: int | None = None,
        agent_id: str = "agent_demo",
    ) -> Cart:
        merchant = merchant_id
        if merchant is None and self._lines:
            product = self.catalogue.get(self._lines[0].sku)
            merchant = product.merchant_id if product else "m_unknown"

        eta = None
        if delivery_days is None and self._lines:
            product = self.catalogue.get(self._lines[0].sku)
            delivery_days = product.delivery_days if product else None
        if delivery_days is not None:
            eta = utc_now() + timedelta(days=delivery_days)

        cart = Cart(
            mandate_id=mandate_id,
            merchant_id=merchant or "m_unknown",
            items=list(self._lines),
            shipping_paise=shipping_paise,
            tax_paise=tax_paise,
            delivery_eta=eta,
            created_by_agent_id=agent_id,
        )
        return cart.with_recomputed_total()

    def checkout(self, cart: Cart) -> dict[str, Any]:
        """Hands the cart to ANUMATI. The agent does not decide whether it may pay."""
        self.log.record(
            "checkout",
            {"cart_id": cart.cart_id, "total_paise": cart.computed_total_paise},
            f"submitted {len(cart.items)} line(s) for conformance check",
        )
        return {"cart_id": cart.cart_id, "total_paise": cart.computed_total_paise}

    def reset(self) -> None:
        self._lines = []
