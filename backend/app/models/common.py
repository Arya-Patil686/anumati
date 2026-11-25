"""Shared enums and primitives.

Money is always an integer number of paise (rule 1). There is no float anywhere in
this package, and `Paise` exists so that a reviewer can grep for it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from pydantic import Field

Paise = Annotated[int, Field(ge=0, description="Integer paise. Never a float.")]


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


class ConstraintType(StrEnum):
    CATEGORY = "CATEGORY"
    ATTRIBUTE = "ATTRIBUTE"
    PRICE_ITEM = "PRICE_ITEM"
    BUDGET_TOTAL = "BUDGET_TOTAL"
    QUANTITY = "QUANTITY"
    BRAND_IN = "BRAND_IN"
    BRAND_OUT = "BRAND_OUT"
    MERCHANT = "MERCHANT"
    DELIVERY_BY = "DELIVERY_BY"
    RECURRENCE = "RECURRENCE"
    SUBSTITUTION = "SUBSTITUTION"
    DIETARY = "DIETARY"
    PACK_SIZE = "PACK_SIZE"
    REFUNDABLE = "REFUNDABLE"


class Operator(StrEnum):
    EQ = "eq"
    NEQ = "neq"
    LTE = "lte"
    GTE = "gte"
    IN = "in"
    NOT_IN = "not_in"
    MATCHES = "matches"
    IS_TRUE = "is_true"
    IS_FALSE = "is_false"


class ConstraintStatus(StrEnum):
    """Per-constraint outcome from one engine layer."""

    SATISFIED = "satisfied"
    VIOLATED = "violated"
    UNCLEAR = "unclear"
    NOT_DECIDABLE_HERE = "not_decidable_here"


class Decision(StrEnum):
    CONFORMS = "CONFORMS"
    VIOLATES = "VIOLATES"
    AMBIGUOUS = "AMBIGUOUS"


class Recurrence(StrEnum):
    ONE_OFF = "one_off"
    RECURRING = "recurring"


class StepUpAnswer(StrEnum):
    APPROVE = "APPROVE"
    REJECT = "REJECT"


class LedgerKind(StrEnum):
    """What a ledger entry records. The chain mixes all of these in one sequence."""

    MANDATE_SIGNED = "mandate_signed"
    CART_CREATED = "cart_created"
    VERDICT = "verdict"
    STEP_UP = "step_up"
    PAYMENT = "payment"
    DISPUTE_OPENED = "dispute_opened"


class Allocation(StrEnum):
    """Proposed liability allocation. A proposal only — card-scheme rules govern."""

    USER = "user"
    AGENT_OPERATOR = "agent_operator"
    MERCHANT = "merchant"
    ISSUER_FRAUD = "issuer_fraud"
