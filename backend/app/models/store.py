"""Database rows for the domain models.

Kept apart from the domain models so that each file uses one library: Pydantic for the
documents, SQLModel here for storage. Each row keeps indexed scalar columns for the
lookups the API makes, plus the whole validated document in `doc`.

The ledger is the exception — `LedgerEntry` in app/models/ledger.py is itself a table,
because for the ledger the stored bytes are the evidence.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.models.cart import Cart
from app.models.common import utc_now
from app.models.dispute import DisputeCase
from app.models.mandate import IntentMandate
from app.models.verdict import StepUp, Verdict


def _json_col() -> Column:
    return Column(JSON, nullable=False)


class MandateRow(SQLModel, table=True):
    __tablename__ = "mandates"

    mandate_id: str = Field(primary_key=True)
    user_id: str = Field(index=True)
    language: str = "en"
    budget_total_paise: int = 0
    expires_at: datetime
    uses_consumed: int = Field(default=0, description="Checked against max_uses by L1 (M3).")
    signed: bool = Field(default=False, index=True)
    created_at: datetime = Field(default_factory=utc_now)
    doc: dict[str, Any] = Field(default_factory=dict, sa_column=_json_col())

    @classmethod
    def from_domain(cls, m: IntentMandate) -> MandateRow:
        return cls(
            mandate_id=m.mandate_id,
            user_id=m.user_id,
            language=m.language,
            budget_total_paise=m.budget_total_paise,
            expires_at=m.expires_at,
            signed=m.is_signed,
            created_at=m.created_at,
            doc=m.model_dump(mode="json"),
        )

    def to_domain(self) -> IntentMandate:
        return IntentMandate.model_validate(self.doc)


class CartRow(SQLModel, table=True):
    __tablename__ = "carts"

    cart_id: str = Field(primary_key=True)
    mandate_id: str = Field(index=True)
    merchant_id: str = Field(index=True)
    total_paise: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    doc: dict[str, Any] = Field(default_factory=dict, sa_column=_json_col())

    @classmethod
    def from_domain(cls, c: Cart) -> CartRow:
        return cls(
            cart_id=c.cart_id,
            mandate_id=c.mandate_id,
            merchant_id=c.merchant_id,
            total_paise=c.total_paise,
            created_at=c.created_at,
            doc=c.model_dump(mode="json"),
        )

    def to_domain(self) -> Cart:
        return Cart.model_validate(self.doc)


class VerdictRow(SQLModel, table=True):
    __tablename__ = "verdicts"

    verdict_id: str = Field(primary_key=True)
    mandate_id: str = Field(index=True)
    cart_id: str = Field(index=True)
    decision: str = Field(index=True)
    engine_version: str
    model_id: str | None = None
    prompt_hash: str | None = None
    latency_ms: int = 0
    created_at: datetime = Field(default_factory=utc_now)
    doc: dict[str, Any] = Field(default_factory=dict, sa_column=_json_col())

    @classmethod
    def from_domain(cls, v: Verdict) -> VerdictRow:
        return cls(
            verdict_id=v.verdict_id,
            mandate_id=v.mandate_id,
            cart_id=v.cart_id,
            decision=str(v.decision),
            engine_version=v.engine_version,
            model_id=v.model_id,
            prompt_hash=v.prompt_hash,
            latency_ms=v.latency_ms,
            created_at=v.created_at,
            doc=v.model_dump(mode="json"),
        )

    def to_domain(self) -> Verdict:
        return Verdict.model_validate(self.doc)


class StepUpRow(SQLModel, table=True):
    __tablename__ = "step_ups"

    verdict_id: str = Field(primary_key=True)
    question_text: str
    user_answer: str | None = Field(default=None, index=True)
    answered_at: datetime | None = None
    doc: dict[str, Any] = Field(default_factory=dict, sa_column=_json_col())

    @classmethod
    def from_domain(cls, s: StepUp) -> StepUpRow:
        return cls(
            verdict_id=s.verdict_id,
            question_text=s.question_text,
            user_answer=str(s.user_answer) if s.user_answer else None,
            answered_at=s.answered_at,
            doc=s.model_dump(mode="json"),
        )

    def to_domain(self) -> StepUp:
        return StepUp.model_validate(self.doc)


class DisputeRow(SQLModel, table=True):
    __tablename__ = "disputes"

    dispute_id: str = Field(primary_key=True)
    cart_id: str = Field(index=True)
    proposed_allocation: str | None = Field(default=None, index=True)
    visa_code: str | None = None
    mc_code: str | None = None
    pack_pdf_path: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
    doc: dict[str, Any] = Field(default_factory=dict, sa_column=_json_col())

    @classmethod
    def from_domain(cls, d: DisputeCase) -> DisputeRow:
        return cls(
            dispute_id=d.dispute_id,
            cart_id=d.cart_id,
            proposed_allocation=str(d.proposed_allocation) if d.proposed_allocation else None,
            visa_code=d.visa_code,
            mc_code=d.mc_code,
            pack_pdf_path=d.pack_pdf_path,
            created_at=d.created_at,
            doc=d.model_dump(mode="json"),
        )

    def to_domain(self) -> DisputeCase:
        return DisputeCase.model_validate(self.doc)
