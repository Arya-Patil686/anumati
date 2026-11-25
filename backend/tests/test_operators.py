"""Benchmark harness: operators, splits, metrics, agreement — and anti-circularity."""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from bench import generate, operators
from bench.annotate.agreement import analyse, fleiss_kappa, majority_label
from bench.baselines.b0_numeric import B0Numeric
from bench.baselines.b1_keyword import B1Keyword, content_words
from bench.baselines.b2_embedding import B2Embedding
from bench.generate import BenchCase
from bench.run_eval import expected_calibration_error, macro_f1, score
from bench.splits import check_no_leakage, make_split

from app.models.common import ConstraintType, Decision
from app.services.catalogue import CatalogueService
from app.services.catalogue_synth import synthetic_catalogue
from app.services.mandate_builder import MandateBuilder


@pytest.fixture(scope="module")
def catalogue() -> CatalogueService:
    return CatalogueService(synthetic_catalogue())


@pytest.fixture(scope="module")
def cases(catalogue: CatalogueService) -> list[BenchCase]:
    return generate.generate(instructions=40, catalogue=catalogue)


# --- anti-circularity ------------------------------------------------------------


def test_the_generator_never_imports_the_engine() -> None:
    """Rule 6, enforced rather than promised.

    If the label generator could reach the conformance engine, every number in the
    report would be the engine grading its own homework.
    """
    for module in (generate, operators):
        source = inspect.getsource(module)
        assert "ConformanceEngine" not in source, f"{module.__name__} reaches the engine"
        assert "from app.services.engine.aggregator" not in source
        assert "l3_judge" not in source


def test_labels_are_only_ever_the_operator_or_the_construction(
    cases: list[BenchCase],
) -> None:
    unperturbed = [c for c in cases if c.operator == "none"]
    assert unperturbed
    assert all(c.gold == Decision.CONFORMS.value for c in unperturbed)

    for case in cases:
        if case.operator in operators.VIOLATING_OPERATORS:
            assert case.gold == Decision.VIOLATES.value
        elif case.operator in operators.AMBIGUOUS_OPERATORS:
            assert case.gold == Decision.AMBIGUOUS.value


# --- operators --------------------------------------------------------------------


def test_every_operator_declares_what_it_breaches() -> None:
    for operator in (*operators.VIOLATING_OPERATORS, *operators.AMBIGUOUS_OPERATORS):
        assert operator in operators.OPERATOR_TARGETS, operator
        assert operators.OPERATOR_TARGETS[operator]


def test_an_operator_is_not_applied_when_it_would_breach_nothing(
    builder: MandateBuilder,
) -> None:
    """delivery_late against an instruction with no deadline is not a violation."""
    no_deadline = builder.build("white running shoes under 5000", user_id="u").mandate
    assert not operators.applies_to("delivery_late", no_deadline)
    assert not operators.applies_to("merchant_off_list", no_deadline)
    assert not operators.applies_to("dietary_violation", no_deadline)

    with_deadline = builder.build(
        "white running shoes under 5000, by Friday", user_id="u"
    ).mandate
    assert operators.applies_to("delivery_late", with_deadline)


def test_recurrence_is_always_applicable(builder: MandateBuilder) -> None:
    """Because every mandate carries a one-off default."""
    mandate = builder.build("white running shoes under 5000", user_id="u").mandate
    assert operators.applies_to("recurrence_flip", mandate)


def test_attribute_operators_need_their_specific_attribute(
    builder: MandateBuilder,
) -> None:
    coloured = builder.build("white running shoes under 5000", user_id="u").mandate
    assert operators.applies_to("near_colour", coloured)
    assert not operators.applies_to("near_material", coloured)


# --- generation --------------------------------------------------------------------


def test_generation_is_deterministic(catalogue: CatalogueService) -> None:
    first = generate.generate(instructions=10, seed=3, catalogue=catalogue)
    second = generate.generate(instructions=10, seed=3, catalogue=catalogue)
    assert [c.id for c in first] == [c.id for c in second]
    assert [c.gold for c in first] == [c.gold for c in second]


def test_all_three_classes_are_represented(cases: list[BenchCase]) -> None:
    golds = {c.gold for c in cases}
    assert golds == {"CONFORMS", "VIOLATES", "AMBIGUOUS"}


def test_hinglish_share_is_in_the_target_band(cases: list[BenchCase]) -> None:
    """The spec asks for roughly 20-25% code-mixed; we allow a wider band at this size."""
    share = sum(1 for c in cases if c.language != "en") / len(cases)
    assert 0.15 <= share <= 0.45, share


def test_injection_cases_are_present_and_labelled_by_structure(
    cases: list[BenchCase],
) -> None:
    injections = [c for c in cases if c.injection]
    assert len(injections) >= 3
    assert {c.gold for c in injections} == {"CONFORMS", "VIOLATES", "AMBIGUOUS"}


def test_a_conforming_cart_honours_the_quantity_asked_for(
    catalogue: CatalogueService,
) -> None:
    """Regression: '2 X under N each' used to produce a one-item cart labelled CONFORMS."""
    from app.models.cart import Cart
    from app.models.mandate import IntentMandate

    for case in generate.generate(instructions=60, seed=5, catalogue=catalogue):
        if case.operator != "none":
            continue
        mandate = IntentMandate.model_validate(case.mandate)
        cart = Cart.model_validate(case.cart)
        wanted = next(
            (int(c.value) for c in mandate.constraints
             if c.type == ConstraintType.QUANTITY), 1
        )
        assert sum(i.qty for i in cart.items) == wanted, case.instruction


def test_jsonl_round_trip(cases: list[BenchCase], tmp_path: Path) -> None:
    path = generate.write_jsonl(cases, tmp_path / "cases.jsonl")
    assert generate.read_jsonl(path) == cases


# --- splits -----------------------------------------------------------------------


def test_split_holds_out_whole_operators_and_categories(cases: list[BenchCase]) -> None:
    split = make_split(cases)
    assert not check_no_leakage(split)
    assert split.held_out_operators
    assert split.held_out_categories

    for case in split.dev:
        assert case.operator not in split.held_out_operators
        assert case.category_path not in split.held_out_categories


def test_split_is_deterministic(cases: list[BenchCase]) -> None:
    assert [c.id for c in make_split(cases).test] == [c.id for c in make_split(cases).test]


def test_leakage_check_catches_a_bad_split(cases: list[BenchCase]) -> None:
    split = make_split(cases)
    broken = type(split)(
        dev=split.dev + split.test[:1],
        test=split.test,
        held_out_operators=split.held_out_operators,
        held_out_categories=split.held_out_categories,
    )
    assert check_no_leakage(broken)


# --- metrics ------------------------------------------------------------------------


def test_macro_f1_is_perfect_when_everything_matches() -> None:
    gold = ["CONFORMS", "VIOLATES", "AMBIGUOUS"]
    assert macro_f1(gold, list(gold)) == pytest.approx(1.0)


def test_macro_f1_punishes_collapsing_to_one_class() -> None:
    gold = ["CONFORMS", "VIOLATES", "AMBIGUOUS", "VIOLATES"]
    assert macro_f1(gold, ["VIOLATES"] * 4) < 0.5


def test_calibration_error_is_zero_for_a_perfectly_calibrated_system() -> None:
    correct = [True] * 90 + [False] * 10
    confidence = [0.9] * 100
    ece, curve = expected_calibration_error(correct, confidence)
    assert ece == pytest.approx(0.0, abs=0.02)
    assert curve


def test_calibration_error_catches_overconfidence() -> None:
    correct = [True] * 50 + [False] * 50
    ece, _curve = expected_calibration_error(correct, [1.0] * 100)
    assert ece > 0.4


def test_scores_count_what_they_say(cases: list[BenchCase]) -> None:
    from bench.baselines import Prediction

    subset = cases[:20]
    predictions = [
        Prediction(decision=Decision(c.gold), confidence=0.9, latency_ms=1.0)
        for c in subset
    ]
    scores, _curve = score("perfect", "dev", subset, predictions)

    assert scores.accuracy == pytest.approx(1.0)
    assert scores.false_block_rate == 0.0
    assert scores.n == len(subset)


# --- baselines ------------------------------------------------------------------------


def test_only_anumati_can_say_ambiguous(cases: list[BenchCase]) -> None:
    """The baselines must guess on exactly the cases that deserve a question."""
    from bench.run_eval import _load

    subset = [c for c in cases if c.gold == "AMBIGUOUS"][:5]
    if not subset:
        pytest.skip("no ambiguous cases in this sample")

    for system in (B0Numeric(), B1Keyword(), B2Embedding()):
        for case in subset:
            mandate, cart = _load(case)
            assert system.predict(mandate, cart).decision is not Decision.AMBIGUOUS


def test_b0_cannot_see_the_product(catalogue: CatalogueService, builder: MandateBuilder) -> None:
    """A treadmill inside the cap passes numeric-only checks. That is the whole gap."""
    from app.models.cart import Cart
    from app.services.catalogue import cart_item_from_product
    from app.services.mandate_builder import MandateSigner

    built = builder.build("white running shoes under 30000", user_id="u").mandate
    mandate = built.model_copy(
        update={"signature_jws": MandateSigner.generate().sign(built)}
    )
    treadmill = catalogue.get("DEMO-TREADMILL-01")
    assert treadmill is not None
    cart = Cart(
        mandate_id=mandate.mandate_id, merchant_id=treadmill.merchant_id,
        items=[cart_item_from_product(treadmill)],
    ).with_recomputed_total()

    assert B0Numeric().predict(mandate, cart).decision is Decision.CONFORMS


def test_keyword_content_words_drop_stopwords() -> None:
    assert content_words("5000 se kam ke white running shoes") == {"white", "running", "shoes"}


# --- annotator agreement ----------------------------------------------------------------


def test_fleiss_kappa_is_one_when_everyone_agrees() -> None:
    kappa, observed, _expected = fleiss_kappa([["CONFORMS"] * 3, ["VIOLATES"] * 3])
    assert kappa == pytest.approx(1.0)
    assert observed == pytest.approx(1.0)


def test_fleiss_kappa_is_about_zero_for_chance_agreement() -> None:
    ratings = [["CONFORMS", "VIOLATES", "AMBIGUOUS"]] * 30
    kappa, _observed, _expected = fleiss_kappa(ratings)
    assert kappa <= 0.05


def test_fleiss_kappa_needs_a_consistent_panel() -> None:
    with pytest.raises(ValueError, match="same number"):
        fleiss_kappa([["CONFORMS", "CONFORMS"], ["VIOLATES"]])


def test_a_three_way_split_has_no_majority_and_becomes_ambiguous() -> None:
    assert majority_label(["CONFORMS", "VIOLATES", "AMBIGUOUS"]) is None

    rows = [
        {"case_id": "a", "annotator_1": "CONFORMS", "annotator_2": "VIOLATES",
         "annotator_3": "AMBIGUOUS"},
        {"case_id": "b", "annotator_1": "VIOLATES", "annotator_2": "VIOLATES",
         "annotator_3": "CONFORMS"},
    ]
    result = analyse(rows)
    assert result.no_majority == ["a"]
    assert result.gold["a"] == "AMBIGUOUS"
    assert result.gold["b"] == "VIOLATES"


def test_agreement_interpretation_is_stated_in_words() -> None:
    rows = [{"case_id": str(i), "annotator_1": "CONFORMS", "annotator_2": "CONFORMS",
             "annotator_3": "CONFORMS"} for i in range(5)]
    assert analyse(rows).interpretation == "almost perfect"
