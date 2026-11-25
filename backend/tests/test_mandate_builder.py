"""Mandate Builder tests, including the M2 acceptance criterion.

Acceptance: 20 sample instructions (5 Hinglish) parse into valid mandates covering every
constraint type; the read-back is generated; signing writes a ledger entry.

`today` is pinned so date-dependent assertions do not drift.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.models.common import ConstraintType, Decision, Recurrence  # noqa: F401
from app.models.mandate import Constraint, IntentMandate
from app.services.mandate_builder import (
    MandateBuilder,
    MandateBuildError,
    MandateSigner,
    detect_language,
    parse_money_to_paise,
    render_read_back,
)

TODAY = date(2026, 11, 25)
T = ConstraintType

# (id, instruction, constraint types this instruction must yield)
INSTRUCTIONS: list[tuple[str, str, set[ConstraintType]]] = [
    ("en_01", "white running shoes, under 5000, delivered by Friday",
     {T.CATEGORY, T.ATTRIBUTE, T.BUDGET_TOTAL, T.DELIVERY_BY}),
    ("en_02", "2 pairs of Aeron running shoes under 5k each, no substitutes, returnable",
     {T.QUANTITY, T.BRAND_IN, T.CATEGORY, T.PRICE_ITEM, T.SUBSTITUTION, T.REFUNDABLE}),
    ("en_03", "veg only 5kg atta from bigbasket, one-off, within 3 days",
     {T.DIETARY, T.PACK_SIZE, T.CATEGORY, T.MERCHANT, T.RECURRENCE, T.DELIVERY_BY}),
    ("en_04", "1 L mustard oil, not Sunlite, max 300",
     {T.PACK_SIZE, T.CATEGORY, T.BRAND_OUT, T.BUDGET_TOTAL}),
    ("en_05", "black leather formal shoes size UK 9, budget 4000",
     {T.ATTRIBUTE, T.CATEGORY, T.BUDGET_TOTAL}),
    ("en_06", "daily milk subscription from freshcart, 1 L, under 70 each",
     {T.RECURRENCE, T.MERCHANT, T.PACK_SIZE, T.PRICE_ITEM, T.CATEGORY}),
    ("en_07", "3 packets Swad namkeen, no substitutes",
     {T.QUANTITY, T.BRAND_IN, T.CATEGORY, T.SUBSTITUTION}),
    ("en_08", "green yoga mat, preferably rubber, under 1500",
     {T.ATTRIBUTE, T.CATEGORY, T.BUDGET_TOTAL}),
    ("en_09", "eggless biscuits 250 g, returnable, by tomorrow",
     {T.DIETARY, T.CATEGORY, T.PACK_SIZE, T.REFUNDABLE, T.DELIVERY_BY}),
    ("en_10", "toothpaste 200 g from metromart, monthly",
     {T.CATEGORY, T.PACK_SIZE, T.MERCHANT, T.RECURRENCE}),
    ("en_11", "navy track pants size L, under 1200, one-off",
     {T.ATTRIBUTE, T.CATEGORY, T.BUDGET_TOTAL, T.RECURRENCE}),
    ("en_12", "Annapurna basmati rice 5 kg, no substitutes, delivered by Monday",
     {T.BRAND_IN, T.CATEGORY, T.PACK_SIZE, T.SUBSTITUTION, T.DELIVERY_BY}),
    ("en_13", "chicken 1 kg from freshcart, today",
     {T.CATEGORY, T.PACK_SIZE, T.MERCHANT, T.DELIVERY_BY}),
    ("en_14", "2 bottles sunflower oil, not more than 500 each, returnable",
     {T.QUANTITY, T.CATEGORY, T.PRICE_ITEM, T.REFUNDABLE}),
    ("en_15", "red sneakers UK 8, avoid Kinetiq, under 3000",
     {T.ATTRIBUTE, T.CATEGORY, T.BRAND_OUT, T.BUDGET_TOTAL}),
    # --- Hinglish / code-mixed ---
    ("hi_01", "5000 se kam ke white running shoes, Friday tak chahiye",
     {T.BUDGET_TOTAL, T.ATTRIBUTE, T.CATEGORY, T.DELIVERY_BY}),
    ("hi_02", "do packet toor dal chahiye, sirf veg, har mahine subscription",
     {T.QUANTITY, T.CATEGORY, T.DIETARY, T.RECURRENCE}),
    ("hi_03", "sarson ka tel 1 L, 300 ke andar, bigbasket se",
     {T.CATEGORY, T.PACK_SIZE, T.BUDGET_TOTAL, T.MERCHANT}),
    ("hi_04", "paanch packet chawal chahiye, ek baar, substitute mat karna",
     {T.QUANTITY, T.CATEGORY, T.RECURRENCE, T.SUBSTITUTION}),
    ("hi_05", "kala joota chahiye, 2000 se kam, kal tak",
     {T.ATTRIBUTE, T.CATEGORY, T.BUDGET_TOTAL, T.DELIVERY_BY}),
]

HINGLISH_IDS = {case_id for case_id, _, _ in INSTRUCTIONS if case_id.startswith("hi_")}


def _build(builder: MandateBuilder, instruction: str) -> IntentMandate:
    return builder.build(instruction, user_id="u_test", today=TODAY).mandate


# --- the 20-instruction set ------------------------------------------------------


def test_instruction_set_shape() -> None:
    assert len(INSTRUCTIONS) == 20
    assert len(HINGLISH_IDS) == 5


@pytest.mark.parametrize(("case_id", "instruction", "expected"), INSTRUCTIONS)
def test_each_instruction_yields_its_constraints(
    builder: MandateBuilder, case_id: str, instruction: str, expected: set[ConstraintType]
) -> None:
    mandate = _build(builder, instruction)
    found = {c.type for c in mandate.constraints}
    assert expected <= found, f"{case_id}: missing {expected - found}"


def test_every_constraint_type_is_covered(builder: MandateBuilder) -> None:
    """All 14 types in the data model appear somewhere in the sample set."""
    found: set[ConstraintType] = set()
    for _, instruction, _ in INSTRUCTIONS:
        found |= {c.type for c in _build(builder, instruction).constraints}
    assert set(ConstraintType) == found, f"never produced: {set(ConstraintType) - found}"


@pytest.mark.parametrize(("case_id", "instruction", "_expected"), INSTRUCTIONS)
def test_mandates_are_valid_and_round_trip(
    builder: MandateBuilder, case_id: str, instruction: str, _expected: set[ConstraintType]
) -> None:
    mandate = _build(builder, instruction)
    assert mandate.mandate_id.startswith("mnd_")
    assert mandate.natural_language == instruction
    assert mandate.budget_total_paise > 0
    assert mandate.expires_at is not None
    assert IntentMandate.model_validate(mandate.model_dump(mode="json")) == mandate


@pytest.mark.parametrize(("case_id", "instruction", "_expected"), INSTRUCTIONS)
def test_source_spans_quote_the_instruction(
    builder: MandateBuilder, case_id: str, instruction: str, _expected: set[ConstraintType]
) -> None:
    """A span must be the user's own words, or absent. Never paraphrase."""
    for c in _build(builder, instruction).constraints:
        if c.source_span is not None:
            assert c.source_span.lower() in instruction.lower(), f"{case_id}: {c.source_span!r}"


@pytest.mark.parametrize(("case_id", "instruction", "_expected"), INSTRUCTIONS)
def test_money_values_are_integer_paise(
    builder: MandateBuilder, case_id: str, instruction: str, _expected: set[ConstraintType]
) -> None:
    for c in _build(builder, instruction).constraints:
        if c.type in (ConstraintType.BUDGET_TOTAL, ConstraintType.PRICE_ITEM):
            assert isinstance(c.value, int), f"{case_id}: {c.value!r} is not an int"


def test_hinglish_instructions_are_detected(builder: MandateBuilder) -> None:
    detected = {
        case_id
        for case_id, instruction, _ in INSTRUCTIONS
        if _build(builder, instruction).language != "en"
    }
    assert HINGLISH_IDS <= detected, f"missed: {HINGLISH_IDS - detected}"


# --- specific parsing behaviour --------------------------------------------------


@pytest.mark.parametrize(
    ("amount", "unit", "expected_paise"),
    [
        ("5000", None, 500_000),
        ("5,000", None, 500_000),
        ("5", "k", 500_000),
        ("5", "hazaar", 500_000),
        ("1.5", "lakh", 15_000_000),
        ("99.50", None, 9_950),
    ],
)
def test_money_parsing(amount: str, unit: str | None, expected_paise: int) -> None:
    result = parse_money_to_paise(amount, unit)
    assert result == expected_paise
    assert isinstance(result, int)


def test_within_n_days_is_a_deadline_not_a_budget(builder: MandateBuilder) -> None:
    """Regression: 'within 3 days' once parsed as a ₹3 spending cap."""
    mandate = _build(builder, "5kg atta, within 3 days")
    budgets = [c for c in mandate.constraints if c.type == ConstraintType.BUDGET_TOTAL]
    assert len(budgets) == 1
    assert budgets[0].source_span is None, "the budget should be the default, not '3'"

    deadlines = [c for c in mandate.constraints if c.type == ConstraintType.DELIVERY_BY]
    assert deadlines[0].value == "2026-11-28"


def test_budget_is_defaulted_and_flagged_when_unstated(builder: MandateBuilder) -> None:
    built = builder.build("white running shoes", user_id="u_test", today=TODAY)
    budget = next(c for c in built.mandate.constraints if c.type == ConstraintType.BUDGET_TOTAL)
    assert budget.source_span is None
    assert "assumed" in built.read_back


def test_per_item_cap_is_distinguished_from_the_total(builder: MandateBuilder) -> None:
    mandate = _build(builder, "2 pairs running shoes under 3000 each")
    per_item = [c for c in mandate.constraints if c.type == ConstraintType.PRICE_ITEM]
    assert per_item and per_item[0].value == 300_000


def test_hedged_constraints_are_soft(builder: MandateBuilder) -> None:
    mandate = _build(builder, "green yoga mat, preferably rubber, under 1500")
    material = next(
        c for c in mandate.constraints
        if c.type == ConstraintType.ATTRIBUTE and c.value["name"] == "material"
    )
    assert material.hard is False

    budget = next(c for c in mandate.constraints if c.type == ConstraintType.BUDGET_TOTAL)
    assert budget.hard is True, "a spending cap is never a preference"


def test_negated_brand_becomes_brand_out(builder: MandateBuilder) -> None:
    mandate = _build(builder, "red sneakers, avoid Kinetiq, under 3000")
    brands = [c for c in mandate.constraints if c.type == ConstraintType.BRAND_OUT]
    assert brands and brands[0].value == ["Kinetiq"]


def test_near_miss_category_resolves_to_its_primary_path(builder: MandateBuilder) -> None:
    mandate = _build(builder, "sports shoes under 4000")
    category = next(c for c in mandate.constraints if c.type == ConstraintType.CATEGORY)
    assert category.value == "Footwear > Sports > Running"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("white running shoes under 5000", "en"),
        ("5000 se kam ke white running shoes, Friday tak chahiye", "hi-Latn"),
        ("सफेद जूते चाहिए", "hi"),
    ],
)
def test_language_detection(text: str, expected: str) -> None:
    assert detect_language(text) == expected


def test_empty_instruction_is_rejected(builder: MandateBuilder) -> None:
    with pytest.raises(MandateBuildError):
        builder.build("   ", user_id="u_test")


def test_constraint_rejects_float_values() -> None:
    """Rule 1, enforced at the model boundary."""
    with pytest.raises(ValidationError):
        Constraint(type=ConstraintType.BUDGET_TOTAL, operator="lte", value=49.99)


# --- read-back --------------------------------------------------------------------


@pytest.mark.parametrize(("case_id", "instruction", "_expected"), INSTRUCTIONS)
def test_read_back_is_generated_for_every_instruction(
    builder: MandateBuilder, case_id: str, instruction: str, _expected: set[ConstraintType]
) -> None:
    built = builder.build(instruction, user_id="u_test", today=TODAY)
    text = built.read_back

    assert instruction in text, "the read-back must quote what the user actually typed"
    assert "Spending limit:" in text
    assert text.count("\n") >= len(built.mandate.hard_constraints())


def test_read_back_lists_hard_constraints_and_marks_preferences(
    builder: MandateBuilder,
) -> None:
    built = builder.build(
        "green yoga mat, preferably rubber, under 1500", user_id="u_test", today=TODAY
    )
    assert "Preferences" in built.read_back
    assert "will not block the purchase" in built.read_back
    assert "₹1,500" in built.read_back


def test_read_back_quotes_the_users_own_category_words(builder: MandateBuilder) -> None:
    built = builder.build("white running shoes under 5000", user_id="u_test", today=TODAY)
    assert "running shoes" in built.read_back
    assert "Footwear > Sports > Running" in built.read_back


# --- signing ----------------------------------------------------------------------


def test_jws_signature_verifies(builder: MandateBuilder) -> None:
    signer = MandateSigner.generate()
    mandate = _build(builder, "white running shoes under 5000")
    signed = mandate.model_copy(update={"signature_jws": signer.sign(mandate)})

    assert signed.is_signed
    assert signer.verify(signed)


def test_editing_a_signed_mandate_breaks_its_signature(builder: MandateBuilder) -> None:
    signer = MandateSigner.generate()
    mandate = _build(builder, "white running shoes under 5000")
    signed = mandate.model_copy(update={"signature_jws": signer.sign(mandate)})

    raised = signed.model_copy(update={"budget_total_paise": 99_999_900})
    assert not signer.verify(raised)


def test_another_key_cannot_verify(builder: MandateBuilder) -> None:
    a, b = MandateSigner.generate(), MandateSigner.generate()
    mandate = _build(builder, "white running shoes under 5000")
    signed = mandate.model_copy(update={"signature_jws": a.sign(mandate)})
    assert not b.verify(signed)


def test_unsigned_mandate_does_not_verify(builder: MandateBuilder) -> None:
    signer = MandateSigner.generate()
    assert not signer.verify(_build(builder, "white running shoes under 5000"))


# --- API --------------------------------------------------------------------------


def test_create_mandate_returns_read_back(client: TestClient) -> None:
    response = client.post(
        "/mandates",
        json={"instruction": "white running shoes, under 5000, delivered by Friday"},
    )
    assert response.status_code == 201
    body = response.json()

    assert body["signed"] is False
    assert body["mandate"]["budget_total_paise"] == 500_000
    assert "Spending limit:" in body["read_back"]
    assert {c["type"] for c in body["mandate"]["constraints"]} >= {
        "CATEGORY", "ATTRIBUTE", "BUDGET_TOTAL", "DELIVERY_BY"
    }


def test_signing_writes_a_ledger_entry(client: TestClient) -> None:
    """M2 acceptance: signing a mandate appends to the chain."""
    before = len(client.get("/ledger").json())

    created = client.post("/mandates", json={"instruction": "5kg atta, veg only"}).json()
    mandate_id = created["mandate"]["mandate_id"]

    signed = client.post(f"/mandates/{mandate_id}/sign")
    assert signed.status_code == 200
    body = signed.json()

    assert body["signed"] is True
    assert body["mandate"]["signature_jws"]
    assert body["ledger_seq"] == before + 1

    entries = client.get("/ledger").json()
    assert len(entries) == before + 1
    assert entries[-1]["kind"] == "mandate_signed"
    assert entries[-1]["payload"]["mandate_id"] == mandate_id

    assert client.get("/ledger/verify").json()["ok"] is True


def test_signing_twice_is_rejected(client: TestClient) -> None:
    created = client.post("/mandates", json={"instruction": "1 L milk daily"}).json()
    mandate_id = created["mandate"]["mandate_id"]

    assert client.post(f"/mandates/{mandate_id}/sign").status_code == 200
    assert client.post(f"/mandates/{mandate_id}/sign").status_code == 409


def test_get_unknown_mandate_is_404(client: TestClient) -> None:
    assert client.get("/mandates/mnd_nope").status_code == 404


def test_empty_instruction_is_422(client: TestClient) -> None:
    assert client.post("/mandates", json={"instruction": ""}).status_code == 422


def test_signed_mandate_in_ledger_verifies_against_the_mandate_key(
    client: TestClient, mandate_signer: MandateSigner
) -> None:
    """The ledger payload is the signed mandate, and that signature still checks out."""
    created = client.post("/mandates", json={"instruction": "white running shoes under 5000"})
    mandate_id = created.json()["mandate"]["mandate_id"]
    client.post(f"/mandates/{mandate_id}/sign")

    payload = client.get("/ledger").json()[-1]["payload"]
    assert mandate_signer.verify(IntentMandate.model_validate(payload))


def test_read_back_is_regenerated_on_get(client: TestClient) -> None:
    created = client.post("/mandates", json={"instruction": "2 packets toor dal, veg only"})
    mandate_id = created.json()["mandate"]["mandate_id"]

    fetched = client.get(f"/mandates/{mandate_id}").json()
    assert fetched["read_back"] == render_read_back(
        IntentMandate.model_validate(fetched["mandate"])
    )
