"""B0 — numeric-only checks. What AP2-style verification does today.

Signature, expiry, use budget, currency, spending caps, quantity, merchant and delivery
date. Everything that can be settled by comparing numbers and dates.

What it cannot do is look at the item. A treadmill bought for "running shoes" passes B0
cleanly as long as it is inside the cap — which is exactly the gap ANUMATI exists to fill.
"""

from __future__ import annotations

from app.models.cart import Cart
from app.models.common import ConstraintStatus, ConstraintType, Decision
from app.models.mandate import IntentMandate
from app.services.engine import l1_rules

from bench.baselines import timed

NUMERIC_TYPES = frozenset(
    {
        ConstraintType.BUDGET_TOTAL,
        ConstraintType.PRICE_ITEM,
        ConstraintType.QUANTITY,
        ConstraintType.MERCHANT,
        ConstraintType.DELIVERY_BY,
    }
)


class B0Numeric:
    name = "B0 numeric"
    description = "Signature, expiry, caps, quantity, merchant, delivery date"

    @timed
    def predict(  # type: ignore[override]
        self, mandate: IntentMandate, cart: Cart
    ) -> tuple[Decision, float, bool, str]:
        results = l1_rules.evaluate(mandate, cart, signature_verified=True)
        by_id = {c.id: c for c in mandate.constraints}

        for result in results:
            constraint = by_id.get(result.constraint_id)
            if constraint is not None and constraint.type not in NUMERIC_TYPES:
                continue  # B0 does not look at the product
            if result.status is ConstraintStatus.VIOLATED:
                return Decision.VIOLATES, 0.95, False, result.rationale

        return Decision.CONFORMS, 0.80, False, "no numeric check failed"
