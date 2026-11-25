"""IntentMandate — what the user actually authorised.

Naming mirrors AP2 (IntentMandate / CartMandate / PaymentMandate) where it fits.
The AP2 GitHub samples are a reference only; we do not vendor the SDK.

Domain models here are plain Pydantic. Their database rows live in app/models/store.py.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.common import (
    ConstraintType,
    Operator,
    Paise,
    Recurrence,
    new_id,
    utc_now,
)


class Constraint(BaseModel):
    """One checkable condition lifted out of the user's instruction.

    `source_span` is the slice of the original instruction this came from, so the
    read-back and any dispute evidence can quote the user's own words.
    """

    model_config = ConfigDict(extra="forbid")

    id: str = Field(default_factory=lambda: new_id("c"))
    type: ConstraintType
    operator: Operator
    value: Any
    hard: bool = True
    source_span: str | None = None

    @field_validator("value")
    @classmethod
    def _no_floats(cls, v: Any) -> Any:
        """Rule 1: money never arrives as a float, and nor does anything else."""
        if isinstance(v, float):
            raise ValueError("float values are not allowed; use integer paise or a string")
        return v


class IntentMandate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mandate_id: str = Field(default_factory=lambda: new_id("mnd"))
    user_id: str
    natural_language: str
    language: str = "en"
    constraints: list[Constraint] = Field(default_factory=list)
    budget_total_paise: Paise
    currency: str = "INR"
    expires_at: datetime
    merchants_allow: list[str] = Field(default_factory=list)
    merchants_deny: list[str] = Field(default_factory=list)
    recurrence: Recurrence = Recurrence.ONE_OFF
    max_uses: int = Field(default=1, ge=1)
    user_cart_confirmation_required: bool = False
    created_at: datetime = Field(default_factory=utc_now)
    signature_jws: str | None = None

    @property
    def is_signed(self) -> bool:
        return bool(self.signature_jws)

    def hard_constraints(self) -> list[Constraint]:
        return [c for c in self.constraints if c.hard]

    def soft_constraints(self) -> list[Constraint]:
        return [c for c in self.constraints if not c.hard]

    def signing_payload(self) -> dict[str, Any]:
        """Everything the signature covers — i.e. the mandate without its own signature."""
        return self.model_dump(mode="json", exclude={"signature_jws"})
