"""Dispute classification, the evidence pack, the PDF, and GET /metrics."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.models.common import Allocation, LedgerKind, utc_now
from app.services.disputes import build_pack, classify, gather, reason_codes
from app.services.evidence_pdf import render
from app.services.ledger import LedgerService


def _signed_mandate(client: TestClient, instruction: str) -> str:
    created = client.post("/mandates", json={"instruction": instruction}).json()
    mandate_id = created["mandate"]["mandate_id"]
    client.post(f"/mandates/{mandate_id}/sign")
    return mandate_id


def _purchase(client: TestClient, instruction: str, sku: str, **cart: object) -> dict:
    """Run a cart through /check and record a payment, as a real flow would."""
    mandate_id = _signed_mandate(client, instruction)
    body = client.post(
        "/check",
        json={"mandate_id": mandate_id, "cart": {"items": [{"sku": sku}], **cart}},
    ).json()

    from app.db import get_engine

    with Session(get_engine()) as session:
        LedgerService(session).append(
            LedgerKind.PAYMENT,
            {
                "cart_id": body["cart"]["cart_id"],
                "mandate_id": mandate_id,
                "amount_paise": body["cart"]["total_paise"],
                "merchant_id": body["cart"]["merchant_id"],
                "paid_at": utc_now().isoformat(),
            },
        )
    return body


# --- reason codes -----------------------------------------------------------------


def test_every_scenario_maps_to_both_schemes() -> None:
    codes = reason_codes()
    for name, mapping in codes["scenarios"].items():
        assert mapping["visa"] in codes["visa"], name
        assert mapping["mc"] in codes["mastercard"], name
        assert Allocation(mapping["allocation"])


# --- classification ----------------------------------------------------------------


def test_a_breach_on_record_outranks_the_wording(client: TestClient) -> None:
    """"I changed my mind" against a VIOLATES verdict is still the agent's fault."""
    purchase = _purchase(
        client, "white running shoes under 5000, one-off", "DEMO-RUN-SUB-01",
        delivery_days=2,
    )
    assert purchase["verdict"]["decision"] == "VIOLATES"

    from app.db import get_engine

    with Session(get_engine()) as session:
        pack = build_pack(session, purchase["cart"]["cart_id"], "I changed my mind.")

    assert pack.scenario == "agent_recurrence_error"
    assert pack.allocation is Allocation.AGENT_OPERATOR
    assert pack.visa_code == "13.2"
    assert pack.mc_code == "4841"


def test_a_conforming_purchase_the_user_regrets_lands_on_the_user(
    client: TestClient,
) -> None:
    purchase = _purchase(
        client, "white running shoes under 5000", "DEMO-RUN-WHITE-01", delivery_days=2
    )
    assert purchase["verdict"]["decision"] == "CONFORMS"

    from app.db import get_engine

    with Session(get_engine()) as session:
        pack = build_pack(session, purchase["cart"]["cart_id"], "I do not want these.")

    assert pack.scenario == "conformed_user_regret"
    assert pack.allocation is Allocation.USER
    assert pack.visa_code == "13.3"


def test_a_delivery_complaint_is_routed_to_the_merchant(client: TestClient) -> None:
    """Fulfilment is outside conformance, and the pack says so."""
    purchase = _purchase(
        client, "white running shoes under 5000", "DEMO-RUN-WHITE-01", delivery_days=2
    )

    from app.db import get_engine

    with Session(get_engine()) as session:
        pack = build_pack(session, purchase["cart"]["cart_id"], "It never arrived.")

    assert pack.scenario == "not_delivered"
    assert pack.allocation is Allocation.MERCHANT
    assert pack.visa_code == "13.1"


def test_an_approved_step_up_becomes_the_defence(client: TestClient) -> None:
    purchase = _purchase(
        client, "white running shoes under 5000", "DEMO-RUN-OFFWHITE-01",
        shipping_paise=9900, delivery_days=2,
    )
    assert purchase["verdict"]["decision"] == "AMBIGUOUS"
    client.post(
        f"/stepup/{purchase['verdict']['verdict_id']}", json={"answer": "APPROVE"}
    )

    from app.db import get_engine

    with Session(get_engine()) as session:
        pack = build_pack(
            session, purchase["cart"]["cart_id"], "This is the wrong colour."
        )

    assert pack.scenario == "user_approved_stepup"
    assert pack.allocation is Allocation.USER
    assert any("APPROVE" in item.headline for item in pack.items)


def test_a_broken_chain_makes_every_record_unusable(client: TestClient) -> None:
    """If the chain fails, the pack says so rather than quoting records it cannot trust."""
    purchase = _purchase(
        client, "white running shoes under 5000", "DEMO-RUN-WHITE-01", delivery_days=2
    )

    from sqlalchemy import text

    from app.db import get_engine

    with get_engine().begin() as connection:
        connection.execute(
            text("UPDATE ledger_entries SET payload = :p WHERE seq = 2"),
            {"p": '{"tampered": true}'},
        )

    with Session(get_engine()) as session:
        pack = build_pack(session, purchase["cart"]["cart_id"], "I never ordered this.")

    assert pack.chain_ok is False
    assert pack.scenario == "mandate_forged"
    assert pack.allocation is Allocation.ISSUER_FRAUD


def test_a_purchase_with_no_check_on_record_is_the_agents_problem(
    client: TestClient, signer: object
) -> None:
    """No conformance check means no evidence the agent was authorised."""
    from app.db import get_engine
    from app.models.cart import Cart
    from app.services.catalogue import cart_item_from_product, get_catalogue

    product = get_catalogue().get("DEMO-RUN-WHITE-01")
    assert product is not None
    cart = Cart(
        mandate_id="mnd_missing", merchant_id=product.merchant_id,
        items=[cart_item_from_product(product)],
    ).with_recomputed_total()

    with Session(get_engine()) as session:
        LedgerService(session).append(LedgerKind.CART_CREATED, cart)
        pack = build_pack(session, cart.cart_id, "I never ordered this.")

    assert pack.scenario == "agent_exceeded_mandate"
    assert pack.allocation is Allocation.AGENT_OPERATOR


# --- the pack ------------------------------------------------------------------------


def test_the_pack_quotes_the_ledger_in_order(client: TestClient) -> None:
    purchase = _purchase(
        client, "white running shoes under 5000", "DEMO-RUN-WHITE-01", delivery_days=2
    )

    from app.db import get_engine

    with Session(get_engine()) as session:
        pack = build_pack(session, purchase["cart"]["cart_id"], "I do not want these.")

    seqs = [i.ledger_seq for i in pack.items if i.ledger_seq is not None]
    assert seqs == sorted(seqs)
    assert [i.kind for i in pack.items][:3] == ["mandate_signed", "cart_created", "verdict"]
    assert pack.items[-1].kind == "chain_verification"
    assert all(i.entry_hash for i in pack.items if i.ledger_seq is not None)


def test_the_pack_carries_what_the_user_agreed_to(client: TestClient) -> None:
    purchase = _purchase(
        client, "white running shoes under 5000", "DEMO-RUN-WHITE-01", delivery_days=2
    )

    from app.db import get_engine

    with Session(get_engine()) as session:
        pack = build_pack(session, purchase["cart"]["cart_id"], "I do not want these.")

    assert "white running shoes under 5000" in pack.mandate_read_back
    assert "proposal" in pack.caveat


def test_gather_reads_only_this_carts_records(client: TestClient) -> None:
    first = _purchase(
        client, "white running shoes under 5000", "DEMO-RUN-WHITE-01", delivery_days=2
    )
    _purchase(client, "5kg atta under 400", "DEMO-ATTA-5KG-01", delivery_days=2)

    from app.db import get_engine

    with Session(get_engine()) as session:
        facts = gather(session, first["cart"]["cart_id"])

    assert facts.cart is not None
    assert facts.cart.cart_id == first["cart"]["cart_id"]
    assert all(v.cart_id == facts.cart.cart_id for v in facts.verdicts)
    assert facts.paid is True


def test_classify_without_any_records(client: TestClient) -> None:
    from app.db import get_engine

    with Session(get_engine()) as session:
        facts = gather(session, "crt_nothing")
    scenario, why = classify("something happened", facts)
    assert scenario == "agent_exceeded_mandate"
    assert why


# --- PDF ------------------------------------------------------------------------------


def test_pdf_renders(client: TestClient, tmp_path: Path) -> None:
    purchase = _purchase(
        client, "white running shoes under 5000", "DEMO-RUN-WHITE-01", delivery_days=2
    )

    from app.db import get_engine

    with Session(get_engine()) as session:
        pack = build_pack(session, purchase["cart"]["cart_id"], "I do not want these.")

    path = render(pack, tmp_path / "pack.pdf")
    assert path.exists()
    assert path.read_bytes().startswith(b"%PDF")
    assert path.stat().st_size > 1500


def test_pdf_escapes_text_that_would_break_the_renderer(
    client: TestClient, tmp_path: Path
) -> None:
    """Claims are user input; reportlab reads markup in paragraphs."""
    purchase = _purchase(
        client, "white running shoes under 5000", "DEMO-RUN-WHITE-01", delivery_days=2
    )

    from app.db import get_engine

    with Session(get_engine()) as session:
        pack = build_pack(
            session, purchase["cart"]["cart_id"], "<b>not</b> what I asked for & more"
        )

    assert render(pack, tmp_path / "pack.pdf").exists()


# --- API --------------------------------------------------------------------------------


def test_dispute_endpoint_writes_to_the_ledger_and_serves_a_pdf(
    client: TestClient,
) -> None:
    purchase = _purchase(
        client, "white running shoes under 5000, one-off", "DEMO-RUN-SUB-01",
        delivery_days=2,
    )

    response = client.post(
        "/disputes",
        json={
            "cart_id": purchase["cart"]["cart_id"],
            "claim_text": "My agent signed me up for a subscription.",
        },
    )
    assert response.status_code == 201
    body = response.json()

    assert body["pack"]["allocation"] == "agent_operator"
    assert body["ledger_seq"] is not None
    assert client.get("/ledger/verify").json()["ok"] is True

    dispute_id = body["pack"]["dispute"]["dispute_id"]
    pdf = client.get(f"/disputes/{dispute_id}/pack.pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")

    fetched = client.get(f"/disputes/{dispute_id}")
    assert fetched.status_code == 200
    assert fetched.json()["pack"]["scenario"] == "agent_recurrence_error"


def test_dispute_for_an_unknown_cart_is_404(client: TestClient) -> None:
    response = client.post(
        "/disputes", json={"cart_id": "crt_nope", "claim_text": "where is it"}
    )
    assert response.status_code == 404


def test_unknown_dispute_is_404(client: TestClient) -> None:
    assert client.get("/disputes/dsp_nope").status_code == 404
    assert client.get("/disputes/dsp_nope/pack.pdf").status_code == 404


# --- metrics ------------------------------------------------------------------------------


def test_metrics_endpoint_serves_what_the_harness_wrote(client: TestClient) -> None:
    body = client.get("/metrics").json()

    if not body["available"]:
        assert "make bench" in body["message"]
        pytest.skip("no benchmark reports in this checkout")

    assert body["rows"]
    systems = {row["system"] for row in body["rows"]}
    assert "ANUMATI" in systems
    assert {row["split"] for row in body["rows"]} <= {"dev", "test"}

    anumati = next(
        r for r in body["rows"] if r["system"] == "ANUMATI" and r["split"] == "test"
    )
    assert 0.0 <= float(anumati["violation_recall"]) <= 1.0
    assert 0.0 <= float(anumati["macro_f1"]) <= 1.0


def test_metrics_never_invents_a_number(client: TestClient, monkeypatch) -> None:
    """With no reports on disk the endpoint says so instead of guessing."""
    from app.api import routes_metrics

    monkeypatch.setattr(routes_metrics, "REPORTS", Path("/nonexistent/reports"))
    body = client.get("/metrics").json()

    assert body["available"] is False
    assert body["rows"] == []
    assert "make bench" in body["message"]
