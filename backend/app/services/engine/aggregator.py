"""The aggregator and the engine that drives the three layers.

Decision rule (spec section 5):

    if any hard constraint is violated with confidence >= T_VIOLATE  -> VIOLATES
    elif every hard constraint is satisfied with confidence >= T_CONFORM -> CONFORMS
    else -> AMBIGUOUS

Soft constraints never block. An unmet soft constraint is folded into the step-up
question instead, so a preference can inform the user without stopping their purchase.

The asymmetry is deliberate. CONFORMS requires *every* hard constraint to clear the bar;
anything less falls to AMBIGUOUS, which costs one tap. VIOLATES needs only one clear
breach. The expensive error is approving a purchase nobody authorised.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from pydantic import BaseModel

from app.canonical import payload_hash
from app.config import ENGINE_VERSION, settings
from app.models.cart import Cart
from app.models.common import ConstraintStatus, Decision, utc_now
from app.models.mandate import IntentMandate
from app.models.verdict import ConstraintResult, StepUp, Verdict
from app.services.engine import l1_rules, l2_attributes, l3_judge
from app.services.engine.l3_judge import JudgeStats
from app.services.engine.taxonomy import Taxonomy, get_taxonomy
from app.services.llm.client import LLMClient


@dataclass(frozen=True)
class Thresholds:
    violate: float = 0.70
    conform: float = 0.70

    @classmethod
    def from_settings(cls) -> Thresholds:
        return cls(violate=settings.t_violate, conform=settings.t_conform)


def aggregate(results: list[ConstraintResult], thresholds: Thresholds) -> Decision:
    hard = [r for r in results if r.hard]
    if not hard:
        return Decision.CONFORMS

    if any(
        r.status is ConstraintStatus.VIOLATED and r.confidence >= thresholds.violate
        for r in hard
    ):
        return Decision.VIOLATES

    if all(
        r.status is ConstraintStatus.SATISFIED and r.confidence >= thresholds.conform
        for r in hard
    ):
        return Decision.CONFORMS

    return Decision.AMBIGUOUS


def _money(paise: int) -> str:
    rupees, remainder = divmod(int(paise), 100)
    return f"₹{rupees:,}" if remainder == 0 else f"₹{rupees:,}.{remainder:02d}"


def build_step_up_question(
    mandate: IntentMandate, cart: Cart, results: list[ConstraintResult]
) -> str:
    """One short question, in the user's terms, answerable with a single tap.

    Kept to the specifics that are actually uncertain. A step-up that recites the whole
    mandate back teaches people to tap "approve" without reading.
    """
    by_id = {c.id: c for c in mandate.constraints}
    doubts: list[str] = []

    for r in results:
        if r.status is ConstraintStatus.UNCLEAR and r.hard:
            doubts.append(_doubt_phrase(by_id.get(r.constraint_id), r))
        elif not r.hard and r.status is not ConstraintStatus.SATISFIED:
            constraint = by_id.get(r.constraint_id)
            if constraint is not None:
                doubts.append(f"your preference ({_value_phrase(constraint)}) was not met")

    item = cart.items[0].title if cart.items else "this cart"
    total = _money(cart.computed_total_paise)
    extras = []
    if cart.shipping_paise:
        extras.append(f"{_money(cart.shipping_paise)} delivery")
    if cart.tax_paise:
        extras.append(f"{_money(cart.tax_paise)} tax")
    breakdown = f" (includes {' and '.join(extras)})" if extras else ""

    if not doubts:
        return f"The agent found {item} for {total}{breakdown}. Approve this purchase?"

    joined = "; ".join(dict.fromkeys(doubts))
    return f"The agent found {item} for {total}{breakdown}, but {joined}. Approve anyway?"


def _value_phrase(constraint: object) -> str:
    value = getattr(constraint, "value", "")
    if isinstance(value, dict) and "name" in value:
        return f"{value['name']} {value['value']}"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


def _doubt_phrase(constraint: object | None, result: ConstraintResult) -> str:
    if result.rationale:
        return result.rationale
    return f"{getattr(constraint, 'type', 'a constraint')} could not be confirmed"


class CheckOutcome(BaseModel):
    """Everything POST /check needs to answer with."""

    verdict: Verdict
    step_up: StepUp | None = None
    l3_call_rate: float = 0.0
    l3_calls: int = 0
    l3_failures: int = 0


class ConformanceEngine:
    """L1 → L2 → L3 → aggregate. Each layer only sees what the one before left open."""

    def __init__(
        self,
        thresholds: Thresholds | None = None,
        taxonomy: Taxonomy | None = None,
        llm_client: LLMClient | None = None,
        *,
        self_consistency: bool = False,
    ) -> None:
        self.thresholds = thresholds or Thresholds.from_settings()
        self.taxonomy = taxonomy or get_taxonomy()
        self.llm_client = llm_client
        self.self_consistency = self_consistency

    def check(
        self,
        mandate: IntentMandate,
        cart: Cart,
        *,
        uses_consumed: int = 0,
        signature_verified: bool | None = None,
    ) -> CheckOutcome:
        started = time.perf_counter()
        now = utc_now()

        decided: dict[str, ConstraintResult] = {}

        for result in l1_rules.evaluate(
            mandate,
            cart,
            now=now,
            uses_consumed=uses_consumed,
            signature_verified=signature_verified,
        ):
            decided[result.constraint_id] = result

        open_ids = self._open_ids(mandate, decided)
        for result in l2_attributes.evaluate(mandate, cart, open_ids, taxonomy=self.taxonomy):
            decided[result.constraint_id] = result

        stats = JudgeStats(constraints_seen=len(mandate.constraints))
        open_ids = self._open_ids(mandate, decided)
        l3_results, stats = l3_judge.evaluate(
            mandate,
            cart,
            open_ids,
            client=self.llm_client,
            stats=stats,
            self_consistency=self.self_consistency,
        )
        for result in l3_results:
            decided[result.constraint_id] = result

        results = list(decided.values())
        decision = aggregate(results, self.thresholds)
        latency_ms = int((time.perf_counter() - started) * 1000)

        verdict = Verdict(
            mandate_id=mandate.mandate_id,
            cart_id=cart.cart_id,
            decision=decision,
            results=results,
            engine_version=ENGINE_VERSION,
            model_id=stats.model_id or settings.llm_model_id,
            # Hash of the L3 prompts this verdict rests on. An empty list when no
            # judge call was needed, which is itself a reproducible fact about the run.
            prompt_hash=payload_hash(sorted(stats.prompt_hashes)),
            latency_ms=latency_ms,
        )

        step_up = (
            StepUp(
                verdict_id=verdict.verdict_id,
                question_text=build_step_up_question(mandate, cart, results),
            )
            if decision is Decision.AMBIGUOUS
            else None
        )

        return CheckOutcome(
            verdict=verdict,
            step_up=step_up,
            l3_call_rate=stats.call_rate,
            l3_calls=stats.calls_made,
            l3_failures=stats.failures,
        )

    @staticmethod
    def _open_ids(
        mandate: IntentMandate, decided: dict[str, ConstraintResult]
    ) -> set[str]:
        """Constraints no layer has settled yet."""
        return {
            c.id
            for c in mandate.constraints
            if c.id not in decided
            or decided[c.id].status is ConstraintStatus.NOT_DECIDABLE_HERE
        }


_engine: ConformanceEngine | None = None


def get_engine() -> ConformanceEngine:
    global _engine
    if _engine is None:
        _engine = ConformanceEngine()
    return _engine


def set_engine(engine: ConformanceEngine | None) -> None:
    global _engine
    _engine = engine
