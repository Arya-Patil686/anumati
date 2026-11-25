"""Layer 3 — the LLM judge, for what L1 and L2 could not settle.

Three things make this safe enough to put in a payment path:

1. **It is called rarely.** Only constraints both earlier layers marked
   `NOT_DECIDABLE_HERE` get here. The call rate is measured and reported, because
   "we put an LLM in the checkout path" is a cost and latency claim we have to defend.
2. **It cannot be talked into a yes.** Product text goes in a delimited data block behind
   an injection guard, and anything the earlier layers already decided is never revisited.
3. **It fails safe.** If the judge is unreachable — replay cache miss, provider down,
   malformed JSON — the constraint becomes `unclear` at confidence 0, which routes to a
   user step-up. It never silently becomes `satisfied`.

Point 3 matters more than it looks. The failure mode we refuse to have is an engine that
approves a purchase because its judge was offline.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.models.cart import Cart
from app.models.common import ConstraintStatus
from app.models.mandate import Constraint, IntentMandate
from app.models.verdict import ConstraintResult
from app.services.llm.cache import CacheMiss
from app.services.llm.client import LLMClient, LLMError, LLMRequest, get_llm_client

PROMPT_PATH = Path(__file__).resolve().parents[1] / "llm" / "prompts" / "judge_constraint.txt"

SCHEMA_HINT = (
    '{"status": "satisfied|violated|unclear", "confidence": 0.0, '
    '"evidence_fields": ["field.name"], "rationale": "one sentence"}'
)

SELF_CONSISTENCY_BAND = (0.40, 0.70)
SELF_CONSISTENCY_SAMPLES = 3

_VALID = {
    "satisfied": ConstraintStatus.SATISFIED,
    "violated": ConstraintStatus.VIOLATED,
    "unclear": ConstraintStatus.UNCLEAR,
}


@dataclass
class JudgeStats:
    """What POST /check reports about its own LLM use."""

    constraints_seen: int = 0
    calls_made: int = 0
    failures: int = 0
    latency_ms: int = 0
    prompt_hashes: list[str] = field(default_factory=list)
    model_id: str | None = None

    @property
    def call_rate(self) -> float:
        return self.calls_made / self.constraints_seen if self.constraints_seen else 0.0


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8").strip()


def describe_constraint(constraint: Constraint) -> str:
    """The constraint, stated as a question, with the user's own words attached."""
    quoted = f' The user wrote: "{constraint.source_span}".' if constraint.source_span else ""
    return (
        f"Constraint type: {constraint.type}\n"
        f"Operator: {constraint.operator}\n"
        f"Required value: {constraint.value}\n"
        f"This is a {'hard requirement' if constraint.hard else 'preference'}.{quoted}"
    )


def build_request(
    constraint: Constraint, cart: Cart, mandate: IntentMandate
) -> LLMRequest:
    """Everything the judge sees. Product text lives only in `data`."""
    items: list[dict[str, Any]] = []
    for item in cart.items:
        entry = dict(item.untrusted_text())
        entry["sku"] = item.sku
        entry["structured_attributes"] = item.attributes
        items.append(entry)

    return LLMRequest(
        prompt_name="judge_constraint",
        system=_load_prompt(),
        instruction=(
            f"{describe_constraint(constraint)}\n\n"
            f"The user's full instruction, for context only, was: "
            f'"{mandate.natural_language}"\n\n'
            f"Decide whether the cart items in the data block meet this one constraint."
        ),
        data={"cart_items": items},
        schema_hint=SCHEMA_HINT,
        temperature=0.0,
    )


def _parse(payload: dict[str, Any]) -> tuple[ConstraintStatus, float, list[str], str]:
    status = _VALID.get(str(payload.get("status", "")).strip().lower())
    if status is None:
        raise LLMError(f"judge returned an unusable status: {payload.get('status')!r}")

    try:
        confidence = float(payload.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    evidence = [str(f) for f in payload.get("evidence_fields") or []]
    rationale = str(payload.get("rationale") or "").strip()
    return status, max(0.0, min(1.0, confidence)), evidence, rationale


def _unavailable(constraint: Constraint, why: str) -> ConstraintResult:
    """Fail safe: an unreachable judge asks the user, it never approves."""
    return ConstraintResult(
        constraint_id=constraint.id,
        layer=3,
        status=ConstraintStatus.UNCLEAR,
        confidence=0.0,
        evidence_fields=[],
        rationale=f"judge unavailable ({why}); routed to the user rather than assumed",
        hard=constraint.hard,
    )


def judge_one(
    constraint: Constraint,
    cart: Cart,
    mandate: IntentMandate,
    client: LLMClient,
    stats: JudgeStats,
    *,
    self_consistency: bool = False,
) -> ConstraintResult:
    request = build_request(constraint, cart, mandate)

    try:
        stats.calls_made += 1
        response = client.complete_json(request)
        stats.latency_ms += response.latency_ms
        stats.prompt_hashes.append(response.prompt_hash)
        stats.model_id = response.model_id
        status, confidence, evidence, rationale = _parse(response.content)
    except CacheMiss as exc:
        stats.failures += 1
        return _unavailable(constraint, str(exc).split(".")[0])
    except (LLMError, KeyError, ValueError) as exc:
        stats.failures += 1
        return _unavailable(constraint, str(exc)[:120])

    low, high = SELF_CONSISTENCY_BAND
    if self_consistency and low <= confidence <= high:
        status, confidence = _vote(
            constraint, cart, mandate, client, stats, first=(status, confidence)
        )

    return ConstraintResult(
        constraint_id=constraint.id,
        layer=3,
        status=status,
        confidence=confidence,
        evidence_fields=evidence,
        rationale=rationale,
        hard=constraint.hard,
    )


def _vote(
    constraint: Constraint,
    cart: Cart,
    mandate: IntentMandate,
    client: LLMClient,
    stats: JudgeStats,
    *,
    first: tuple[ConstraintStatus, float],
) -> tuple[ConstraintStatus, float]:
    """Majority of three when the first answer sat on the fence."""
    samples = [first]
    request = build_request(constraint, cart, mandate)

    for _ in range(SELF_CONSISTENCY_SAMPLES - 1):
        try:
            stats.calls_made += 1
            response = client.complete_json(request)
            stats.latency_ms += response.latency_ms
            status, confidence, _evidence, _rationale = _parse(response.content)
            samples.append((status, confidence))
        except (CacheMiss, LLMError, KeyError, ValueError):
            stats.failures += 1
            break

    winner, count = Counter(status for status, _ in samples).most_common(1)[0]
    agreeing = [c for s, c in samples if s == winner]
    return winner, (sum(agreeing) / len(agreeing)) * (count / len(samples))


def evaluate(
    mandate: IntentMandate,
    cart: Cart,
    unresolved: set[str],
    *,
    client: LLMClient | None = None,
    stats: JudgeStats | None = None,
    self_consistency: bool = False,
) -> tuple[list[ConstraintResult], JudgeStats]:
    stats = stats or JudgeStats()
    results: list[ConstraintResult] = []

    pending = [c for c in mandate.constraints if c.id in unresolved]
    if not pending:
        return results, stats

    client = client or get_llm_client()
    for constraint in pending:
        results.append(
            judge_one(
                constraint, cart, mandate, client, stats, self_consistency=self_consistency
            )
        )
    return results, stats
