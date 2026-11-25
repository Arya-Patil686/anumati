"""Layer 1 deterministic rules and Layer 2 attribute grounding."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.cart import Cart, CartItem
from app.models.common import (
    ConstraintStatus,
    ConstraintType,
    Operator,
    Recurrence,
    utc_now,
)
from app.models.mandate import Constraint, IntentMandate
from app.services.engine import l1_rules, l2_attributes
from app.services.engine.l2_attributes import PackSize
from app.services.engine.taxonomy import get_taxonomy

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


def make_mandate(constraints: list[Constraint], **kwargs) -> IntentMandate:
    defaults = {
        "user_id": "u_test",
        "natural_language": "test instruction",
        "budget_total_paise": 500_000,
        "expires_at": NOW + timedelta(days=5),
        "signature_jws": "fake.jws.token",
    }
    return IntentMandate(constraints=constraints, **(defaults | kwargs))


def make_cart(items: list[CartItem], **kwargs) -> Cart:
    cart = Cart(mandate_id="mnd_x", merchant_id=kwargs.pop("merchant_id", "m_sportyfy"),
                items=items, **kwargs)
    return cart.with_recomputed_total()


def shoe(**overrides) -> CartItem:
    base = {
        "sku": "S1",
        "title": "Running Shoe",
        "category_path": "Footwear > Sports > Running",
        "brand": "Aeron",
        "attributes": {"colour": "white", "size": "UK 9", "material": "mesh"},
        "unit_price_paise": 449_900,
        "qty": 1,
    }
    return CartItem(**(base | overrides))


def status_of(results, constraint_id: str) -> ConstraintStatus:
    return next(r for r in results if r.constraint_id == constraint_id).status


# --- mandate-level checks ----------------------------------------------------------


def test_unsigned_mandate_is_violated() -> None:
    mandate = make_mandate([], signature_jws=None)
    result = l1_rules.check_signature(mandate, verified=None)
    assert result.status is ConstraintStatus.VIOLATED


def test_signature_without_a_key_is_undecidable() -> None:
    result = l1_rules.check_signature(make_mandate([]), verified=None)
    assert result.status is ConstraintStatus.NOT_DECIDABLE_HERE


def test_bad_signature_is_violated() -> None:
    result = l1_rules.check_signature(make_mandate([]), verified=False)
    assert result.status is ConstraintStatus.VIOLATED


def test_expired_mandate_is_violated() -> None:
    mandate = make_mandate([], expires_at=NOW - timedelta(days=1))
    assert l1_rules.check_expiry(mandate, now=NOW).status is ConstraintStatus.VIOLATED


def test_live_mandate_passes_expiry() -> None:
    assert l1_rules.check_expiry(make_mandate([]), now=NOW).status is ConstraintStatus.SATISFIED


def test_use_budget_is_enforced() -> None:
    mandate = make_mandate([], max_uses=2)
    assert l1_rules.check_uses(mandate, uses_consumed=1).status is ConstraintStatus.SATISFIED
    assert l1_rules.check_uses(mandate, uses_consumed=2).status is ConstraintStatus.VIOLATED


def test_a_cart_that_misreports_its_total_is_caught() -> None:
    """The agent saying one number and charging another is itself evidence."""
    cart = Cart(mandate_id="m", merchant_id="m_x", items=[shoe()], total_paise=1)
    assert l1_rules.check_total_consistency(cart).status is ConstraintStatus.VIOLATED
    assert l1_rules.check_total_consistency(cart.with_recomputed_total()).status is (
        ConstraintStatus.SATISFIED
    )


# --- budget ------------------------------------------------------------------------


def test_budget_counts_tax_and_shipping() -> None:
    """The cap is on what the user pays, not on the sticker price."""
    constraint = Constraint(type=ConstraintType.BUDGET_TOTAL, operator=Operator.LTE,
                            value=500_000)
    cart = make_cart([shoe()], shipping_paise=99_900, tax_paise=9_900)
    assert l1_rules.check_budget_total(constraint, cart).status is ConstraintStatus.VIOLATED

    cheap = make_cart([shoe()], shipping_paise=9_900)
    assert l1_rules.check_budget_total(constraint, cheap).status is ConstraintStatus.SATISFIED


def test_per_item_cap_names_the_offending_sku() -> None:
    constraint = Constraint(type=ConstraintType.PRICE_ITEM, operator=Operator.LTE,
                            value=300_000)
    result = l1_rules.check_price_item(constraint, make_cart([shoe()]))
    assert result.status is ConstraintStatus.VIOLATED
    assert "S1" in result.rationale


# --- quantity, delivery, recurrence, merchant ----------------------------------------


@pytest.mark.parametrize(
    ("operator", "wanted", "qty", "expected"),
    [
        (Operator.EQ, 2, 2, ConstraintStatus.SATISFIED),
        (Operator.EQ, 2, 4, ConstraintStatus.VIOLATED),
        (Operator.LTE, 3, 2, ConstraintStatus.SATISFIED),
        (Operator.GTE, 3, 2, ConstraintStatus.VIOLATED),
    ],
)
def test_quantity(operator, wanted, qty, expected) -> None:
    constraint = Constraint(type=ConstraintType.QUANTITY, operator=operator, value=wanted)
    assert l1_rules.check_quantity(constraint, make_cart([shoe(qty=qty)])).status is expected


def test_delivery_without_an_eta_is_undecidable() -> None:
    constraint = Constraint(type=ConstraintType.DELIVERY_BY, operator=Operator.LTE,
                            value="2026-11-27")
    result = l1_rules.check_delivery_by(constraint, make_cart([shoe()]))
    assert result.status is ConstraintStatus.NOT_DECIDABLE_HERE


@pytest.mark.parametrize(
    ("eta_days", "expected"),
    [(1, ConstraintStatus.SATISFIED), (2, ConstraintStatus.SATISFIED),
     (9, ConstraintStatus.VIOLATED)],
)
def test_delivery_deadline(eta_days: int, expected: ConstraintStatus) -> None:
    deadline = (utc_now() + timedelta(days=2)).date().isoformat()
    constraint = Constraint(type=ConstraintType.DELIVERY_BY, operator=Operator.LTE,
                            value=deadline)
    cart = make_cart([shoe()], delivery_eta=utc_now() + timedelta(days=eta_days))
    assert l1_rules.check_delivery_by(constraint, cart).status is expected


def test_subscription_when_the_user_said_buy_once() -> None:
    """Visa 13.2 / Mastercard 4841 in one check."""
    constraint = Constraint(type=ConstraintType.RECURRENCE, operator=Operator.EQ,
                            value=Recurrence.ONE_OFF.value)
    cart = make_cart([shoe(is_subscription=True)])
    result = l1_rules.check_recurrence(constraint, cart)
    assert result.status is ConstraintStatus.VIOLATED
    assert "subscription" in result.rationale


def test_recurring_mandate_needs_a_subscription_item() -> None:
    constraint = Constraint(type=ConstraintType.RECURRENCE, operator=Operator.EQ,
                            value=Recurrence.RECURRING.value)
    assert l1_rules.check_recurrence(constraint, make_cart([shoe()])).status is (
        ConstraintStatus.VIOLATED
    )


def test_merchant_allow_and_deny() -> None:
    allow = Constraint(type=ConstraintType.MERCHANT, operator=Operator.IN,
                       value=["m_sportyfy"])
    mandate = make_mandate([allow])
    assert l1_rules.check_merchant(allow, make_cart([shoe()]), mandate).status is (
        ConstraintStatus.SATISFIED
    )
    off_list = make_cart([shoe()], merchant_id="m_metromart")
    assert l1_rules.check_merchant(allow, off_list, mandate).status is ConstraintStatus.VIOLATED


# --- L1 leaves product questions alone ------------------------------------------------


def test_l1_defers_product_constraints_to_later_layers() -> None:
    category = Constraint(type=ConstraintType.CATEGORY, operator=Operator.EQ,
                          value="Footwear > Sports > Running")
    results = l1_rules.evaluate(make_mandate([category]), make_cart([shoe()]), now=NOW)
    assert status_of(results, category.id) is ConstraintStatus.NOT_DECIDABLE_HERE


# --- L2: category ---------------------------------------------------------------------


def _l2_category(constraint: Constraint, cart: Cart):
    return l2_attributes.check_category(constraint, cart, get_taxonomy())


def test_exact_category_is_satisfied() -> None:
    constraint = Constraint(type=ConstraintType.CATEGORY, operator=Operator.EQ,
                            value="Footwear > Sports > Running", source_span="running shoes")
    assert _l2_category(constraint, make_cart([shoe()])).status is ConstraintStatus.SATISFIED


def test_a_different_top_level_category_is_a_clear_loss() -> None:
    constraint = Constraint(type=ConstraintType.CATEGORY, operator=Operator.EQ,
                            value="Footwear > Sports > Running", source_span="running shoes")
    treadmill = shoe(sku="T1", category_path="Fitness > Cardio Equipment > Treadmills")
    result = _l2_category(constraint, make_cart([treadmill]))
    assert result.status is ConstraintStatus.VIOLATED
    assert result.confidence >= 0.9


def test_an_ambiguous_term_is_never_settled_here() -> None:
    """Even an exact path match stays unclear when the user's word was vague."""
    constraint = Constraint(type=ConstraintType.CATEGORY, operator=Operator.EQ,
                            value="Footwear > Sports > Running", source_span="sports shoes")
    result = _l2_category(constraint, make_cart([shoe()]))
    assert result.status is ConstraintStatus.UNCLEAR
    assert result.confidence < 0.7


# --- L2: attributes --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("wanted", "actual", "expected"),
    [
        ("white", "white", ConstraintStatus.SATISFIED),
        ("white", "off-white", ConstraintStatus.UNCLEAR),
        ("white", "red", ConstraintStatus.VIOLATED),
        ("navy", "blue", ConstraintStatus.UNCLEAR),
        ("black", "black", ConstraintStatus.SATISFIED),
    ],
)
def test_colour_matching(wanted: str, actual: str, expected: ConstraintStatus) -> None:
    constraint = Constraint(type=ConstraintType.ATTRIBUTE, operator=Operator.EQ,
                            value={"name": "colour", "value": wanted})
    cart = make_cart([shoe(attributes={"colour": actual})])
    assert l2_attributes.check_attribute(constraint, cart).status is expected


def test_near_matches_never_clear_the_conform_threshold() -> None:
    """A near match must not be able to approve a purchase on its own."""
    assert l2_attributes.CONFIDENCE_NEAR < 0.70


def test_missing_attribute_is_handed_upward() -> None:
    constraint = Constraint(type=ConstraintType.ATTRIBUTE, operator=Operator.EQ,
                            value={"name": "colour", "value": "white"})
    cart = make_cart([shoe(attributes={})])
    assert l2_attributes.check_attribute(constraint, cart).status is (
        ConstraintStatus.NOT_DECIDABLE_HERE
    )


# --- L2: brand, dietary, pack size, substitution ----------------------------------------


def test_brand_in_and_brand_out() -> None:
    allow = Constraint(type=ConstraintType.BRAND_IN, operator=Operator.IN, value=["Aeron"])
    deny = Constraint(type=ConstraintType.BRAND_OUT, operator=Operator.NOT_IN, value=["Aeron"])
    cart = make_cart([shoe()])
    assert l2_attributes.check_brand(allow, cart).status is ConstraintStatus.SATISFIED
    assert l2_attributes.check_brand(deny, cart).status is ConstraintStatus.VIOLATED


def test_veg_only_blocks_non_veg() -> None:
    constraint = Constraint(type=ConstraintType.DIETARY, operator=Operator.EQ, value="veg")
    chicken = shoe(sku="C1", attributes={"veg": False})
    assert l2_attributes.check_dietary(constraint, make_cart([chicken])).status is (
        ConstraintStatus.VIOLATED
    )


def test_eggless_cannot_be_settled_from_a_veg_flag() -> None:
    constraint = Constraint(type=ConstraintType.DIETARY, operator=Operator.EQ, value="eggless")
    biscuit = shoe(sku="B1", attributes={"veg": True})
    assert l2_attributes.check_dietary(constraint, make_cart([biscuit])).status is (
        ConstraintStatus.NOT_DECIDABLE_HERE
    )


@pytest.mark.parametrize(
    ("wanted", "actual", "expected"),
    [
        ("5 kg", "5 kg", ConstraintStatus.SATISFIED),
        ("5 kg", "4.9 kg", ConstraintStatus.UNCLEAR),
        ("5 kg", "1 kg", ConstraintStatus.VIOLATED),
        ("1 L", "1 L", ConstraintStatus.SATISFIED),
        ("1 L", "500 ml", ConstraintStatus.VIOLATED),
    ],
)
def test_pack_size(wanted: str, actual: str, expected: ConstraintStatus) -> None:
    constraint = Constraint(type=ConstraintType.PACK_SIZE, operator=Operator.EQ, value=wanted)
    cart = make_cart([shoe(sku="P1", attributes={"pack_size": actual})])
    assert l2_attributes.check_pack_size(constraint, cart).status is expected


def test_pack_size_cannot_compare_mass_with_volume() -> None:
    constraint = Constraint(type=ConstraintType.PACK_SIZE, operator=Operator.EQ, value="1 L")
    cart = make_cart([shoe(sku="P2", attributes={"pack_size": "1 kg"})])
    assert l2_attributes.check_pack_size(constraint, cart).status is (
        ConstraintStatus.NOT_DECIDABLE_HERE
    )


@pytest.mark.parametrize(
    ("text", "quantity", "dimension"),
    [("5 kg", 5000.0, "mass"), ("500 g", 500.0, "mass"), ("1 L", 1000.0, "volume"),
     ("250 ml", 250.0, "volume")],
)
def test_pack_size_parsing(text: str, quantity: float, dimension: str) -> None:
    parsed = PackSize.parse(text)
    assert parsed is not None
    assert parsed.quantity == quantity
    assert parsed.dimension == dimension


def test_unparseable_pack_size() -> None:
    assert PackSize.parse("one big bag") is None


def test_substitution_needs_a_named_brand() -> None:
    substitution = Constraint(type=ConstraintType.SUBSTITUTION, operator=Operator.IS_FALSE,
                              value=False)
    mandate = make_mandate([substitution])
    assert l2_attributes.check_substitution(substitution, make_cart([shoe()]), mandate).status is (
        ConstraintStatus.NOT_DECIDABLE_HERE
    )


def test_substitution_is_violated_when_the_brand_was_swapped() -> None:
    brand = Constraint(type=ConstraintType.BRAND_IN, operator=Operator.IN, value=["Annapurna"])
    substitution = Constraint(type=ConstraintType.SUBSTITUTION, operator=Operator.IS_FALSE,
                              value=False)
    mandate = make_mandate([brand, substitution])
    cart = make_cart([shoe(brand="Gold Harvest")])
    result = l2_attributes.check_substitution(substitution, cart, mandate)
    assert result.status is ConstraintStatus.VIOLATED
    assert "gold harvest" in result.rationale.lower()


def test_a_cart_survives_a_round_trip_through_json() -> None:
    """Regression: computed fields are serialised but must not break re-validation.

    A cart that cannot be read back out of the ledger is not evidence.
    """
    cart = make_cart([shoe(qty=2)], shipping_paise=9_900)
    dumped = cart.model_dump(mode="json")

    assert dumped["items"][0]["line_total_paise"] == 899_800
    assert Cart.model_validate(dumped) == cart
