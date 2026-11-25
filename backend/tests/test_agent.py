"""Shopping agent, fault modes, and the step-up flow."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.models.cart import Cart
from app.models.common import ConstraintType, Decision
from app.services.agent.faults import CART_FAULTS, SELECTION_FAULTS, FaultMode
from app.services.agent.shopping_agent import AgentMode, ShoppingAgent, _plan_from
from app.services.agent.tools import ShoppingTools, ToolLog
from app.services.catalogue import CatalogueService
from app.services.engine.aggregator import CheckOutcome, ConformanceEngine
from app.services.mandate_builder import MandateBuilder, MandateSigner


@pytest.fixture(scope="module")
def catalogue() -> CatalogueService:
    from app.services.catalogue_synth import synthetic_catalogue

    return CatalogueService(synthetic_catalogue())


def make_checker(engine: ConformanceEngine):
    """A checker that runs the engine but persists nothing."""

    def checker(cart: Cart) -> tuple[CheckOutcome, list[int]]:
        mandate = checker.mandate  # type: ignore[attr-defined]
        return engine.check(mandate, cart, signature_verified=True), []

    return checker


def run_agent(
    instruction: str,
    builder: MandateBuilder,
    catalogue: CatalogueService,
    *,
    mode: AgentMode = AgentMode.NORMAL,
    fault: FaultMode | None = None,
):
    signer = MandateSigner.generate()
    built = builder.build(instruction, user_id="u_agent")
    mandate = built.mandate.model_copy(update={"signature_jws": signer.sign(built.mandate)})

    engine = ConformanceEngine()
    checker = make_checker(engine)
    checker.mandate = mandate  # type: ignore[attr-defined]

    agent = ShoppingAgent(catalogue, mode=mode, fault=fault)
    return agent.run(mandate, checker)


# --- tools ------------------------------------------------------------------------


def test_tools_log_everything_they_do(catalogue: CatalogueService) -> None:
    """The tool log is what a dispute pack quotes about what the agent did."""
    log = ToolLog()
    tools = ShoppingTools(catalogue, log=log)

    tools.search_catalogue("running shoes", limit=5)
    tools.get_product("DEMO-RUN-WHITE-01")
    tools.add_to_cart("DEMO-RUN-WHITE-01")
    cart = tools.build_cart("mnd_x")
    tools.checkout(cart)

    names = [c.name for c in log.calls]
    assert names == ["search_catalogue", "get_product", "add_to_cart", "checkout"]
    assert all(c.result_summary for c in log.calls)


def test_the_agent_cannot_invent_a_product(catalogue: CatalogueService) -> None:
    tools = ShoppingTools(catalogue)
    assert tools.add_to_cart("NOT-A-REAL-SKU") is None
    assert tools.get_product("NOT-A-REAL-SKU") is None


def test_cart_totals_itself_from_catalogue_prices(catalogue: CatalogueService) -> None:
    tools = ShoppingTools(catalogue)
    tools.add_to_cart("DEMO-RUN-WHITE-01", qty=2)
    cart = tools.build_cart("mnd_x", shipping_paise=9_900)
    assert cart.computed_total_paise == 449_900 * 2 + 9_900
    assert cart.total_is_consistent


# --- planning ---------------------------------------------------------------------


def test_the_plan_is_read_off_the_mandate(builder: MandateBuilder) -> None:
    built = builder.build(
        "2 pairs of Aeron white running shoes under 5000 from sportyfy", user_id="u"
    )
    plan = _plan_from(built.mandate)

    assert plan.category_path == "Footwear > Sports > Running"
    assert plan.quantity == 2
    assert plan.wanted_attributes["colour"] == "white"
    assert "aeron" in plan.wanted_brands
    assert plan.merchant == "m_sportyfy"
    assert plan.max_price_paise == 500_000


# --- a clean run -------------------------------------------------------------------


def test_a_normal_run_conforms_and_pays(
    builder: MandateBuilder, catalogue: CatalogueService
) -> None:
    run = run_agent("white running shoes under 5000", builder, catalogue)
    assert run.final_decision is Decision.CONFORMS
    assert run.paid is True
    assert len(run.attempts) == 1
    assert run.attempts[0].action == "paid"


def test_a_normal_run_respects_the_budget(
    builder: MandateBuilder, catalogue: CatalogueService
) -> None:
    run = run_agent("white running shoes under 5000", builder, catalogue)
    assert run.final.cart.computed_total_paise <= 500_000


def test_the_narrative_explains_each_attempt(
    builder: MandateBuilder, catalogue: CatalogueService
) -> None:
    run = run_agent("white running shoes under 5000", builder, catalogue)
    assert run.narrative
    assert "attempt 1" in run.narrative[0]


# --- fault modes --------------------------------------------------------------------


@pytest.mark.parametrize("fault", sorted(SELECTION_FAULTS | CART_FAULTS))
def test_every_fault_mode_runs_without_crashing(
    fault: FaultMode, builder: MandateBuilder, catalogue: CatalogueService
) -> None:
    run = run_agent(
        "2 Annapurna 5kg atta, veg only, under 400 each, from bigbasket, by tomorrow",
        builder, catalogue, mode=AgentMode.FAULTY, fault=fault,
    )
    assert run.attempts
    assert run.final_decision in set(Decision)


def test_recurrence_flip_is_caught_and_named(
    builder: MandateBuilder, catalogue: CatalogueService
) -> None:
    """The M4 acceptance case."""
    run = run_agent(
        "white running shoes under 5000", builder, catalogue,
        mode=AgentMode.FAULTY, fault=FaultMode.RECURRENCE_FLIP,
    )
    first = run.attempts[0]
    assert first.verdict.decision is Decision.VIOLATES
    assert first.fault == "recurrence_flip"
    assert any(item.is_subscription for item in first.cart.items)

    reasons = [r.rationale for r in first.verdict.results if r.status == "violated"]
    assert any("subscription" in r for r in reasons)


def test_a_blocked_agent_retries_once_and_then_succeeds(
    builder: MandateBuilder, catalogue: CatalogueService
) -> None:
    run = run_agent(
        "white running shoes under 5000", builder, catalogue,
        mode=AgentMode.FAULTY, fault=FaultMode.RECURRENCE_FLIP,
    )
    assert len(run.attempts) == 2
    assert run.attempts[0].action.startswith("blocked, retrying")
    assert run.attempts[1].fault is None
    assert run.final_decision is Decision.CONFORMS


def test_hidden_fees_push_the_total_over_the_cap(
    builder: MandateBuilder, catalogue: CatalogueService
) -> None:
    run = run_agent(
        "white running shoes under 5000", builder, catalogue,
        mode=AgentMode.FAULTY, fault=FaultMode.HIDDEN_FEES,
    )
    first = run.attempts[0]
    assert first.cart.shipping_paise > 0
    assert first.cart.computed_total_paise > 500_000
    assert first.verdict.decision is Decision.VIOLATES


def test_wrong_category_far_is_blocked(
    builder: MandateBuilder, catalogue: CatalogueService
) -> None:
    run = run_agent(
        "white running shoes under 20000", builder, catalogue,
        mode=AgentMode.FAULTY, fault=FaultMode.WRONG_CATEGORY_FAR,
    )
    assert run.attempts[0].verdict.decision is Decision.VIOLATES


def test_an_agent_never_decides_it_may_pay(
    builder: MandateBuilder, catalogue: CatalogueService
) -> None:
    """Whatever the fault, `paid` follows the verdict and nothing else."""
    for fault in sorted(SELECTION_FAULTS | CART_FAULTS):
        run = run_agent(
            "white running shoes under 5000", builder, catalogue,
            mode=AgentMode.FAULTY, fault=fault,
        )
        assert run.paid == (run.final_decision is Decision.CONFORMS)


# --- the mandate default that makes recurrence checkable --------------------------


def test_an_unstated_mandate_is_a_one_off_purchase(builder: MandateBuilder) -> None:
    """Otherwise an agent can turn a one-time buy into a subscription and breach nothing."""
    built = builder.build("white running shoes under 5000", user_id="u")
    recurrence = next(
        c for c in built.mandate.constraints if c.type == ConstraintType.RECURRENCE
    )
    assert recurrence.value == "one_off"
    assert recurrence.hard is True
    assert recurrence.source_span is None
    assert "one-off purchase" in built.read_back


# --- API ------------------------------------------------------------------------------


def test_agent_run_endpoint(client: TestClient) -> None:
    response = client.post(
        "/agent/run", json={"instruction": "white running shoes under 5000"}
    )
    assert response.status_code == 200
    body = response.json()

    assert body["run"]["final_decision"] == "CONFORMS"
    assert body["run"]["paid"] is True
    assert body["read_back"]
    assert body["mandate_ledger_seq"] == 1
    assert client.get("/ledger/verify").json()["ok"] is True


def test_agent_run_with_a_fault_writes_the_whole_story_to_the_ledger(
    client: TestClient,
) -> None:
    response = client.post(
        "/agent/run",
        json={"instruction": "white running shoes under 5000",
              "mode": "faulty", "fault": "recurrence_flip"},
    )
    body = response.json()
    assert body["run"]["attempts"][0]["verdict"]["decision"] == "VIOLATES"

    kinds = [e["kind"] for e in client.get("/ledger").json()]
    assert kinds[0] == "mandate_signed"
    assert kinds.count("verdict") == 2, "both the blocked attempt and the retry are recorded"
    assert "payment" in kinds
    assert client.get("/ledger/verify").json()["ok"] is True


def test_step_up_flow(client: TestClient) -> None:
    created = client.post("/mandates", json={"instruction": "white running shoes, under 5000"})
    mandate_id = created.json()["mandate"]["mandate_id"]
    client.post(f"/mandates/{mandate_id}/sign")

    check = client.post(
        "/check",
        json={"mandate_id": mandate_id,
              "cart": {"items": [{"sku": "DEMO-RUN-OFFWHITE-01"}],
                       "shipping_paise": 9900, "delivery_days": 3}},
    ).json()
    assert check["verdict"]["decision"] == "AMBIGUOUS"
    verdict_id = check["verdict"]["verdict_id"]

    answered = client.post(f"/stepup/{verdict_id}", json={"answer": "APPROVE"})
    assert answered.status_code == 200
    body = answered.json()

    assert body["step_up"]["user_answer"] == "APPROVE"
    assert body["step_up"]["answered_at"]
    assert body["payment_ledger_seq"] is not None

    kinds = [e["kind"] for e in client.get("/ledger").json()]
    assert "step_up" in kinds and "payment" in kinds
    assert client.get("/ledger/verify").json()["ok"] is True


def test_a_rejected_step_up_does_not_pay(client: TestClient) -> None:
    created = client.post("/mandates", json={"instruction": "white running shoes, under 5000"})
    mandate_id = created.json()["mandate"]["mandate_id"]
    client.post(f"/mandates/{mandate_id}/sign")

    check = client.post(
        "/check",
        json={"mandate_id": mandate_id,
              "cart": {"items": [{"sku": "DEMO-RUN-OFFWHITE-01"}], "delivery_days": 3}},
    ).json()

    body = client.post(
        f"/stepup/{check['verdict']['verdict_id']}", json={"answer": "REJECT"}
    ).json()
    assert body["payment_ledger_seq"] is None
    assert "payment" not in [e["kind"] for e in client.get("/ledger").json()]


def test_a_step_up_cannot_be_answered_twice(client: TestClient) -> None:
    created = client.post("/mandates", json={"instruction": "white running shoes, under 5000"})
    mandate_id = created.json()["mandate"]["mandate_id"]
    client.post(f"/mandates/{mandate_id}/sign")
    check = client.post(
        "/check",
        json={"mandate_id": mandate_id,
              "cart": {"items": [{"sku": "DEMO-RUN-OFFWHITE-01"}], "delivery_days": 3}},
    ).json()
    verdict_id = check["verdict"]["verdict_id"]

    assert client.post(f"/stepup/{verdict_id}", json={"answer": "APPROVE"}).status_code == 200
    assert client.post(f"/stepup/{verdict_id}", json={"answer": "REJECT"}).status_code == 409


def test_step_up_for_an_unknown_verdict_is_404(client: TestClient) -> None:
    assert client.post("/stepup/vrd_nope", json={"answer": "APPROVE"}).status_code == 404
