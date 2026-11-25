"""Cart and CartItem — what the agent actually built.

Every text field on a CartItem (`title`, `description`, free-text attribute values) is
**untrusted data** from a merchant catalogue. Rule 3: it is never interpolated into an
instruction. The L3 judge receives it in a separate delimited field.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from app.models.common import Paise, new_id, utc_now

_COMPUTED_FIELDS = frozenset({"line_total_paise"})


class CartItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _drop_computed(cls, data: Any) -> Any:
        """Computed fields are serialised but must not be fed back in.

        Without this, a cart written to the ledger or the database cannot be read back:
        `extra="forbid"` rejects the very keys `model_dump()` emitted.
        """
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if k not in _COMPUTED_FIELDS}
        return data

    sku: str
    title: str
    description: str = ""
    category_path: str = ""
    brand: str = ""
    attributes: dict[str, Any] = Field(default_factory=dict)
    unit_price_paise: Paise
    qty: int = Field(default=1, ge=1)
    is_subscription: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def line_total_paise(self) -> int:
        return self.unit_price_paise * self.qty

    def untrusted_text(self) -> dict[str, str]:
        """The merchant-controlled fields, isolated for the judge's data block."""
        return {
            "title": self.title,
            "description": self.description,
            "brand": self.brand,
            "category_path": self.category_path,
        }


class Cart(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cart_id: str = Field(default_factory=lambda: new_id("crt"))
    mandate_id: str
    merchant_id: str
    items: list[CartItem] = Field(default_factory=list)
    shipping_paise: Paise = 0
    tax_paise: Paise = 0
    total_paise: Paise = 0
    delivery_eta: datetime | None = None
    created_at: datetime = Field(default_factory=utc_now)
    created_by_agent_id: str | None = None

    @property
    def items_subtotal_paise(self) -> int:
        return sum(item.line_total_paise for item in self.items)

    @property
    def computed_total_paise(self) -> int:
        """Total *including tax and shipping* — this is what the budget is checked against."""
        return self.items_subtotal_paise + self.shipping_paise + self.tax_paise

    def with_recomputed_total(self) -> Cart:
        return self.model_copy(update={"total_paise": self.computed_total_paise})

    @property
    def total_is_consistent(self) -> bool:
        """A mismatch here is itself evidence: the agent misreported what it was paying."""
        return self.total_paise == self.computed_total_paise
