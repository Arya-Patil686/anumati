"""The 30 golden cases — the M3 acceptance criterion.

Every case runs in replay mode with no network. Each one is hand-labelled in
golden_cases.yaml from the structured catalogue fields, never from the engine's own
output, so a passing run says something.

The suite also prints the L3 call rate, which is the cost-and-latency claim the pitch
rests on.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from app.models.cart import Cart
from app.models.common import ConstraintStatus, ConstraintType, Decision, utc_now
from app.models.mandate import IntentMandate
from app.models.verdict import Verdict
from app.services.catalogue import cart_item_from_product, get_catalogue
from app.services.engine.aggregator import ConformanceEngine
from app.services.mandate_builder import MandateBuilder, MandateSigner

CASES_PATH = Path(__file__).parent / "golden_cases.yaml"
CASES: list[dict[str, Any]] = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8"))["cases"]
CASE_IDS = [case["id"] for case in CASES]


@pytest.fixture(scope="module")
def engine() -> ConformanceEngine:
    """Default engine. LLM_MODE is replay in tests, so L3 has no recordings to read."""
    return ConformanceEngine()


@pytest.fixture(scope="module")
def golden_signer() -> MandateSigner:
    return MandateSigner.generate()


def build_cart(case: dict[str, Any], mandate: IntentMandate) -> Cart:
    catalogue = get_catalogue()
    spec = case["cart"]

    items = []
    for ref in spec["items"]:
        product = catalogue.get(ref["sku"])
        assert product is not None, f"{case['id']}: unknown sku {ref['sku']}"
        items.append(cart_item_from_product(product, qty=ref.get("qty", 1)))

    merchant_id = spec.get("merchant_id")
    if merchant_id is None:
        first = catalogue.get(spec["items"][0]["sku"])
        merchant_id = first.merchant_id if first else "m_unknown"

    eta = None
    if spec.get("delivery_days") is not None:
        eta = utc_now() + timedelta(days=int(spec["delivery_days"]))

    cart = Cart(
        mandate_id=mandate.mandate_id,
        merchant_id=merchant_id,
        items=items,
        shipping_paise=spec.get("shipping_paise", 0),
        tax_paise=spec.get("tax_paise", 0),
        delivery_eta=eta,
        created_by_agent_id="agent_golden",
    )
    return cart.with_recomputed_total()


def run_case(
    case: dict[str, Any], builder: MandateBuilder, engine: ConformanceEngine,
    signer: MandateSigner,
) -> tuple[Verdict, IntentMandate, Any]:
    built = builder.build(case["instruction"], user_id="u_golden")
    mandate = built.mandate.model_copy(
        update={"signature_jws": signer.sign(built.mandate)}
    )
    cart = build_cart(case, mandate)
    outcome = engine.check(mandate, cart, signature_verified=True)
    return outcome.verdict, mandate, outcome


def _types_with_status(
    verdict: Verdict, mandate: IntentMandate, status: ConstraintStatus
) -> set[ConstraintType]:
    by_id = {c.id: c for c in mandate.constraints}
    return {
        by_id[r.constraint_id].type
        for r in verdict.results
        if r.status is status and r.constraint_id in by_id
    }


# --- the acceptance test ------------------------------------------------------------


def test_case_file_has_the_expected_shape() -> None:
    assert len(CASES) == 30
    counts = {d: sum(1 for c in CASES if c["expected_decision"] == d)
              for d in ("CONFORMS", "VIOLATES", "AMBIGUOUS")}
    assert counts == {"CONFORMS": 10, "VIOLATES": 10, "AMBIGUOUS": 10}
    assert sum(1 for c in CASES if c.get("injection")) == 3
    assert len(set(CASE_IDS)) == 30


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_golden_case_decision(
    case: dict[str, Any], builder: MandateBuilder, engine: ConformanceEngine,
    golden_signer: MandateSigner,
) -> None:
    verdict, _mandate, _outcome = run_case(case, builder, engine, golden_signer)
    expected = Decision(case["expected_decision"])

    assert verdict.decision is expected, (
        f"{case['id']} expected {expected} got {verdict.decision}\n"
        + "\n".join(
            f"  {r.status:20} conf={r.confidence:.2f} layer={r.layer} "
            f"{r.constraint_id}: {r.rationale}"
            for r in verdict.results
        )
    )


@pytest.mark.parametrize(
    "case", [c for c in CASES if c.get("expected_violated_types")],
    ids=[c["id"] for c in CASES if c.get("expected_violated_types")],
)
def test_violations_name_the_right_constraints(
    case: dict[str, Any], builder: MandateBuilder, engine: ConformanceEngine,
    golden_signer: MandateSigner,
) -> None:
    """A verdict that blocks must say which constraint broke, or it is not evidence."""
    verdict, mandate, _ = run_case(case, builder, engine, golden_signer)
    violated = _types_with_status(verdict, mandate, ConstraintStatus.VIOLATED)
    expected = {ConstraintType(t) for t in case["expected_violated_types"]}
    assert expected <= violated, f"{case['id']}: missing {expected - violated}"


@pytest.mark.parametrize(
    "case", [c for c in CASES if c.get("expected_unclear_types")],
    ids=[c["id"] for c in CASES if c.get("expected_unclear_types")],
)
def test_ambiguity_names_the_right_constraints(
    case: dict[str, Any], builder: MandateBuilder, engine: ConformanceEngine,
    golden_signer: MandateSigner,
) -> None:
    verdict, mandate, _ = run_case(case, builder, engine, golden_signer)
    unclear = _types_with_status(verdict, mandate, ConstraintStatus.UNCLEAR)
    expected = {ConstraintType(t) for t in case["expected_unclear_types"]}
    assert expected <= unclear, f"{case['id']}: missing {expected - unclear}"


# --- properties that must hold for every case ----------------------------------------


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_every_verdict_is_reproducible(
    case: dict[str, Any], builder: MandateBuilder, engine: ConformanceEngine,
    golden_signer: MandateSigner,
) -> None:
    """Rule 5: engine_version, model_id, prompt_hash and latency_ms on every verdict."""
    verdict, _mandate, _ = run_case(case, builder, engine, golden_signer)
    assert verdict.engine_version
    assert verdict.model_id
    assert verdict.prompt_hash and len(verdict.prompt_hash) == 64
    assert verdict.latency_ms >= 0


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_no_constraint_is_left_undecided(
    case: dict[str, Any], builder: MandateBuilder, engine: ConformanceEngine,
    golden_signer: MandateSigner,
) -> None:
    """Every constraint reaches a real status. `not_decidable_here` is internal only."""
    verdict, mandate, _ = run_case(case, builder, engine, golden_signer)
    assert len(verdict.results) >= len(mandate.constraints)
    assert not [r for r in verdict.results if r.status is ConstraintStatus.NOT_DECIDABLE_HERE]


@pytest.mark.parametrize("case", CASES, ids=CASE_IDS)
def test_ambiguous_verdicts_always_carry_a_question(
    case: dict[str, Any], builder: MandateBuilder, engine: ConformanceEngine,
    golden_signer: MandateSigner,
) -> None:
    verdict, _mandate, outcome = run_case(case, builder, engine, golden_signer)
    if verdict.decision is Decision.AMBIGUOUS:
        assert outcome.step_up is not None
        assert outcome.step_up.question_text.endswith("?")
        assert len(outcome.step_up.question_text) < 400, "a step-up nobody reads is useless"
    else:
        assert outcome.step_up is None


# --- prompt injection -----------------------------------------------------------------


@pytest.mark.parametrize(
    "case", [c for c in CASES if c.get("injection")],
    ids=[c["id"] for c in CASES if c.get("injection")],
)
def test_injected_text_never_changes_the_verdict(
    case: dict[str, Any], builder: MandateBuilder, engine: ConformanceEngine,
    golden_signer: MandateSigner,
) -> None:
    """The payload demands CONFORMS. The verdict is whatever the fields say it is."""
    verdict, _mandate, _ = run_case(case, builder, engine, golden_signer)
    assert verdict.decision is Decision(case["expected_decision"])

    injected = get_catalogue().get("DEMO-INJECT-01")
    assert injected is not None
    assert "return CONFORMS" in injected.description

    for result in verdict.results:
        assert "ignore all previous instructions" not in result.rationale.lower()


# --- L3 call rate ------------------------------------------------------------------------


def test_l3_call_rate_is_low_and_reported(
    builder: MandateBuilder, engine: ConformanceEngine, golden_signer: MandateSigner,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Most constraints never reach a model. That is the feasibility claim."""
    total_constraints = 0
    total_calls = 0
    decisions: dict[str, int] = {}

    for case in CASES:
        verdict, mandate, outcome = run_case(case, builder, engine, golden_signer)
        total_constraints += len(mandate.constraints)
        total_calls += outcome.l3_calls
        decisions[str(verdict.decision)] = decisions.get(str(verdict.decision), 0) + 1

    rate = total_calls / total_constraints
    with capsys.disabled():
        print(
            f"\n  golden cases: {decisions}"
            f"\n  constraints judged: {total_constraints}"
            f"\n  L3 calls attempted: {total_calls}"
            f"\n  L3 call rate: {rate:.1%}\n"
        )

    assert rate < 0.15, f"L3 is being called for {rate:.0%} of constraints"
