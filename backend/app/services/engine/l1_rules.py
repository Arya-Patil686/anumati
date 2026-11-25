"""Layer 1 — deterministic rules. Fast, no ML, no network.

Everything here is arithmetic, dates and set membership over the mandate and the cart's
own metadata. If L1 can decide a constraint, nothing further is allowed to overturn it:
a model must never be able to talk the engine out of "this cart costs more than the
authorised limit".

Constraints that need the *product* looked at (category, colour, brand, dietary, pack
size) are explicitly `NOT_DECIDABLE_HERE` and fall through to L2.

Three mandate-level checks have no constraint in the mandate's own list — the signature,
the expiry and the use budget. They are reported under synthetic ids (`mandate.*`) so the
aggregator treats them exactly like any other hard constraint.
"""

from __future__ import annotations

from datetime import date, datetime

from app.models.cart import Cart
from app.models.common import ConstraintStatus, ConstraintType, Operator, Recurrence, utc_now
from app.models.mandate import Constraint, IntentMandate
from app.models.verdict import ConstraintResult

# Constraint types L1 owns outright. Anything else is left to L2/L3.
L1_TYPES = frozenset(
    {
        ConstraintType.BUDGET_TOTAL,
        ConstraintType.PRICE_ITEM,
        ConstraintType.QUANTITY,
        ConstraintType.DELIVERY_BY,
        ConstraintType.RECURRENCE,
        ConstraintType.MERCHANT,
    }
)

MANDATE_SIGNATURE = "mandate.signature"
MANDATE_EXPIRY = "mandate.expiry"
MANDATE_USES = "mandate.uses"
MANDATE_CURRENCY = "mandate.currency"
CART_TOTAL_CONSISTENCY = "cart.total_consistency"


def _result(
    constraint_id: str,
    status: ConstraintStatus,
    *,
    confidence: float = 1.0,
    evidence: list[str] | None = None,
    rationale: str = "",
    hard: bool = True,
) -> ConstraintResult:
    return ConstraintResult(
        constraint_id=constraint_id,
        layer=1,
        status=status,
        confidence=confidence,
        evidence_fields=evidence or [],
        rationale=rationale,
        hard=hard,
    )


def _undecidable(constraint: Constraint, why: str) -> ConstraintResult:
    return _result(
        constraint.id,
        ConstraintStatus.NOT_DECIDABLE_HERE,
        confidence=0.0,
        rationale=why,
        hard=constraint.hard,
    )


# --- mandate-level checks --------------------------------------------------------


def check_signature(mandate: IntentMandate, *, verified: bool | None) -> ConstraintResult:
    """`verified` is None when no key was supplied to check against."""
    if not mandate.is_signed:
        return _result(
            MANDATE_SIGNATURE,
            ConstraintStatus.VIOLATED,
            rationale="the mandate carries no signature",
            evidence=["signature_jws"],
        )
    if verified is None:
        return _result(
            MANDATE_SIGNATURE,
            ConstraintStatus.NOT_DECIDABLE_HERE,
            confidence=0.0,
            rationale="no verification key was supplied",
            evidence=["signature_jws"],
        )
    if not verified:
        return _result(
            MANDATE_SIGNATURE,
            ConstraintStatus.VIOLATED,
            rationale="the signature does not match the mandate contents",
            evidence=["signature_jws"],
        )
    return _result(
        MANDATE_SIGNATURE,
        ConstraintStatus.SATISFIED,
        rationale="signature verified",
        evidence=["signature_jws"],
    )


def check_expiry(mandate: IntentMandate, *, now: datetime) -> ConstraintResult:
    expires = mandate.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=now.tzinfo)

    if now > expires:
        return _result(
            MANDATE_EXPIRY,
            ConstraintStatus.VIOLATED,
            rationale=f"the mandate expired on {expires.date().isoformat()}",
            evidence=["expires_at"],
        )
    return _result(
        MANDATE_EXPIRY,
        ConstraintStatus.SATISFIED,
        rationale=f"valid until {expires.date().isoformat()}",
        evidence=["expires_at"],
    )


def check_uses(mandate: IntentMandate, *, uses_consumed: int) -> ConstraintResult:
    if uses_consumed >= mandate.max_uses:
        return _result(
            MANDATE_USES,
            ConstraintStatus.VIOLATED,
            rationale=f"already used {uses_consumed} of {mandate.max_uses} permitted times",
            evidence=["max_uses"],
        )
    return _result(
        MANDATE_USES,
        ConstraintStatus.SATISFIED,
        rationale=f"use {uses_consumed + 1} of {mandate.max_uses}",
        evidence=["max_uses"],
    )


def check_total_consistency(cart: Cart) -> ConstraintResult:
    """A cart whose stated total differs from its own lines is itself evidence."""
    if cart.total_is_consistent:
        return _result(
            CART_TOTAL_CONSISTENCY,
            ConstraintStatus.SATISFIED,
            rationale="the cart total matches its line items, tax and shipping",
            evidence=["total_paise"],
        )
    return _result(
        CART_TOTAL_CONSISTENCY,
        ConstraintStatus.VIOLATED,
        rationale=(
            f"the cart claims {cart.total_paise} paise but its lines, tax and shipping "
            f"come to {cart.computed_total_paise}"
        ),
        evidence=["total_paise", "items", "tax_paise", "shipping_paise"],
    )


def check_currency(mandate: IntentMandate) -> ConstraintResult:
    if mandate.currency != "INR":
        return _result(
            MANDATE_CURRENCY,
            ConstraintStatus.VIOLATED,
            rationale=f"only INR is supported, mandate says {mandate.currency}",
            evidence=["currency"],
        )
    return _result(MANDATE_CURRENCY, ConstraintStatus.SATISFIED, rationale="INR")


# --- per-constraint checks ---------------------------------------------------------


def check_budget_total(constraint: Constraint, cart: Cart) -> ConstraintResult:
    """The cap is on the total the user actually pays — tax and shipping included."""
    limit = int(constraint.value)
    actual = cart.computed_total_paise
    satisfied = actual <= limit
    return _result(
        constraint.id,
        ConstraintStatus.SATISFIED if satisfied else ConstraintStatus.VIOLATED,
        rationale=(
            f"total including tax and shipping is {actual} paise against a limit of {limit}"
        ),
        evidence=["items", "tax_paise", "shipping_paise"],
        hard=constraint.hard,
    )


def check_price_item(constraint: Constraint, cart: Cart) -> ConstraintResult:
    limit = int(constraint.value)
    over = [item for item in cart.items if item.unit_price_paise > limit]
    if over:
        names = ", ".join(f"{i.sku} at {i.unit_price_paise}" for i in over)
        return _result(
            constraint.id,
            ConstraintStatus.VIOLATED,
            rationale=f"over the per-item limit of {limit} paise: {names}",
            evidence=["unit_price_paise"],
            hard=constraint.hard,
        )
    return _result(
        constraint.id,
        ConstraintStatus.SATISFIED,
        rationale=f"every item is at or under {limit} paise",
        evidence=["unit_price_paise"],
        hard=constraint.hard,
    )


def check_quantity(constraint: Constraint, cart: Cart) -> ConstraintResult:
    wanted = int(constraint.value)
    actual = sum(item.qty for item in cart.items)
    operator_ok = {
        Operator.EQ: actual == wanted,
        Operator.LTE: actual <= wanted,
        Operator.GTE: actual >= wanted,
    }.get(constraint.operator, actual == wanted)

    return _result(
        constraint.id,
        ConstraintStatus.SATISFIED if operator_ok else ConstraintStatus.VIOLATED,
        rationale=f"cart holds {actual} unit(s), mandate says {constraint.operator} {wanted}",
        evidence=["qty"],
        hard=constraint.hard,
    )


def check_delivery_by(constraint: Constraint, cart: Cart) -> ConstraintResult:
    if cart.delivery_eta is None:
        return _undecidable(constraint, "the cart carries no delivery estimate")

    deadline = date.fromisoformat(str(constraint.value))
    eta = cart.delivery_eta.date()
    return _result(
        constraint.id,
        ConstraintStatus.SATISFIED if eta <= deadline else ConstraintStatus.VIOLATED,
        rationale=f"estimated delivery {eta.isoformat()} against a deadline of {deadline}",
        evidence=["delivery_eta"],
        hard=constraint.hard,
    )


def check_recurrence(constraint: Constraint, cart: Cart) -> ConstraintResult:
    """'Buy once' versus a subscription — the Visa 13.2 / MC 4841 dispute in miniature."""
    wanted = Recurrence(constraint.value)
    subscriptions = [item.sku for item in cart.items if item.is_subscription]

    if wanted is Recurrence.ONE_OFF and subscriptions:
        return _result(
            constraint.id,
            ConstraintStatus.VIOLATED,
            rationale=f"a one-off purchase was authorised but {', '.join(subscriptions)} "
            f"is a recurring subscription",
            evidence=["is_subscription"],
            hard=constraint.hard,
        )
    if wanted is Recurrence.RECURRING and not subscriptions:
        return _result(
            constraint.id,
            ConstraintStatus.VIOLATED,
            rationale="a recurring purchase was authorised but no item is a subscription",
            evidence=["is_subscription"],
            hard=constraint.hard,
        )
    return _result(
        constraint.id,
        ConstraintStatus.SATISFIED,
        rationale=f"cart matches the authorised recurrence ({wanted.value})",
        evidence=["is_subscription"],
        hard=constraint.hard,
    )


def check_merchant(constraint: Constraint, cart: Cart, mandate: IntentMandate) -> ConstraintResult:
    allowed = list(constraint.value) if constraint.operator == Operator.IN else []
    denied = list(constraint.value) if constraint.operator == Operator.NOT_IN else []

    if denied and cart.merchant_id in denied:
        return _result(
            constraint.id,
            ConstraintStatus.VIOLATED,
            rationale=f"{cart.merchant_id} is on the deny list",
            evidence=["merchant_id"],
            hard=constraint.hard,
        )
    if allowed and cart.merchant_id not in allowed:
        return _result(
            constraint.id,
            ConstraintStatus.VIOLATED,
            rationale=f"{cart.merchant_id} is not among {', '.join(allowed)}",
            evidence=["merchant_id"],
            hard=constraint.hard,
        )
    if mandate.merchants_deny and cart.merchant_id in mandate.merchants_deny:
        return _result(
            constraint.id,
            ConstraintStatus.VIOLATED,
            rationale=f"{cart.merchant_id} is denied by the mandate",
            evidence=["merchant_id"],
            hard=constraint.hard,
        )
    return _result(
        constraint.id,
        ConstraintStatus.SATISFIED,
        rationale=f"{cart.merchant_id} is permitted",
        evidence=["merchant_id"],
        hard=constraint.hard,
    )


# --- entry point ---------------------------------------------------------------------


def evaluate(
    mandate: IntentMandate,
    cart: Cart,
    *,
    now: datetime | None = None,
    uses_consumed: int = 0,
    signature_verified: bool | None = None,
) -> list[ConstraintResult]:
    """Every L1 answer for this (mandate, cart), including the mandate-level checks."""
    now = now or utc_now()

    results = [
        check_signature(mandate, verified=signature_verified),
        check_expiry(mandate, now=now),
        check_uses(mandate, uses_consumed=uses_consumed),
        check_currency(mandate),
        check_total_consistency(cart),
    ]

    for constraint in mandate.constraints:
        if constraint.type not in L1_TYPES:
            results.append(_undecidable(constraint, "needs the product looked at"))
            continue

        match constraint.type:
            case ConstraintType.BUDGET_TOTAL:
                results.append(check_budget_total(constraint, cart))
            case ConstraintType.PRICE_ITEM:
                results.append(check_price_item(constraint, cart))
            case ConstraintType.QUANTITY:
                results.append(check_quantity(constraint, cart))
            case ConstraintType.DELIVERY_BY:
                results.append(check_delivery_by(constraint, cart))
            case ConstraintType.RECURRENCE:
                results.append(check_recurrence(constraint, cart))
            case ConstraintType.MERCHANT:
                results.append(check_merchant(constraint, cart, mandate))

    return results
