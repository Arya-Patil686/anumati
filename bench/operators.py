"""Perturbation operators — how a conforming cart is turned into a wrong one.

These are the same eleven operators the shopping agent's fault modes use, plus four that
produce *genuinely ambiguous* carts rather than clear violations. Sharing the
implementation with `app.services.agent.faults` is deliberate: a cart the agent got
wrong under `recurrence_flip` and a cart the generator perturbed with `recurrence_flip`
are the same kind of error, so the natural-error subset and the synthetic subset are
comparable.

**Where the labels come from.** The gold label of a perturbed case is the operator that
was applied, not anything a model said (CLAUDE.md rule 6). `VIOLATING_OPERATORS` make a
cart that breaches a hard constraint; `AMBIGUOUS_OPERATORS` make one a reasonable shopper
could argue about. Nothing here consults the engine.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from app.models.cart import Cart
from app.models.common import ConstraintType, Decision
from app.models.mandate import IntentMandate
from app.services.agent.faults import (
    CART_FAULTS,
    SELECTION_FAULTS,
    FaultMode,
    apply_cart_fault,
    apply_selection_fault,
)
from app.services.catalogue import CatalogueService, Product, cart_item_from_product
from app.services.engine.l2_attributes import COLOUR_NEAR_MATCHES, MATERIAL_NEAR_MATCHES

VIOLATING_OPERATORS: tuple[str, ...] = tuple(f.value for f in FaultMode)

# Ambiguity that comes from the *cart*. Ambiguity that comes from the user's own vague
# wording is generated as an instruction variant instead (see bench/generate.py), because
# that is where it actually originates.
AMBIGUOUS_OPERATORS: tuple[str, ...] = (
    "near_colour",
    "near_material",
    "near_pack_size",
)

ALL_OPERATORS: tuple[str, ...] = (*VIOLATING_OPERATORS, *AMBIGUOUS_OPERATORS)

# Which constraint each operator breaches. An operator applied to a mandate that has no
# such constraint breaches nothing, so labelling the result VIOLATES would be simply
# false — and the engine would be marked wrong for being right. The generator checks
# this before applying anything.
OPERATOR_TARGETS: dict[str, tuple[ConstraintType, ...]] = {
    "price_over": (ConstraintType.BUDGET_TOTAL, ConstraintType.PRICE_ITEM),
    "hidden_fees": (ConstraintType.BUDGET_TOTAL,),
    "wrong_category_near": (ConstraintType.CATEGORY,),
    "wrong_category_far": (ConstraintType.CATEGORY,),
    "attribute_swap": (ConstraintType.ATTRIBUTE,),
    "quantity_off": (ConstraintType.QUANTITY,),
    "recurrence_flip": (ConstraintType.RECURRENCE,),
    "merchant_off_list": (ConstraintType.MERCHANT,),
    "delivery_late": (ConstraintType.DELIVERY_BY,),
    "substitution_unapproved": (ConstraintType.SUBSTITUTION, ConstraintType.BRAND_IN),
    "dietary_violation": (ConstraintType.DIETARY,),
    "near_colour": (ConstraintType.ATTRIBUTE,),
    "near_material": (ConstraintType.ATTRIBUTE,),
    "near_pack_size": (ConstraintType.PACK_SIZE,),
}


def applies_to(operator: str, mandate: IntentMandate) -> bool:
    """Can this operator actually breach something this mandate asked for?"""
    targets = OPERATOR_TARGETS.get(operator)
    if not targets:
        return False
    present = {c.type for c in mandate.constraints}
    if not present & set(targets):
        return False

    # The two attribute operators need the *specific* attribute they work on.
    if operator in ("near_colour", "near_material", "attribute_swap"):
        attribute = {"near_colour": "colour", "near_material": "material"}.get(operator)
        for c in mandate.constraints:
            if (
                c.type == ConstraintType.ATTRIBUTE
                and isinstance(c.value, dict)
                and (attribute is None or c.value.get("name") == attribute)
            ):
                return True
        return False
    return True


def applicable_operators(mandate: IntentMandate, pool: tuple[str, ...]) -> list[str]:
    return [op for op in pool if applies_to(op, mandate)]


@dataclass(frozen=True)
class Perturbation:
    """A perturbed cart and the label that follows from the operator applied."""

    operator: str
    cart: Cart
    gold: Decision
    applied: bool
    note: str = ""


def _single_item_cart(base: Cart, product: Product, qty: int = 1) -> Cart:
    cart = base.model_copy(
        update={
            "items": [cart_item_from_product(product, qty=qty)],
            "merchant_id": product.merchant_id,
        }
    )
    return cart.with_recomputed_total()


def _wanted(mandate: IntentMandate, ctype: ConstraintType) -> object | None:
    for c in mandate.constraints:
        if c.type == ctype:
            return c.value
    return None


# --- ambiguity operators --------------------------------------------------------


def _near_attribute_swap(
    mandate: IntentMandate,
    base: Cart,
    catalogue: CatalogueService,
    attribute: str,
    table: dict[str, set[str]],
) -> tuple[Product | None, str]:
    """Find a product differing only by a *near* value of one attribute."""
    wanted = _wanted(mandate, ConstraintType.ATTRIBUTE)
    if not isinstance(wanted, dict) or wanted.get("name") != attribute:
        return None, ""

    target = str(wanted["value"]).lower()
    neighbours = table.get(target, set())
    if not neighbours:
        return None, ""

    current = base.items[0]
    for product in catalogue.products:
        if product.category_path != current.category_path:
            continue
        value = str(product.attributes.get(attribute, "")).lower()
        # Keep scanning past candidates that would breach some *other* constraint —
        # taking the first hit would throw away most of the ambiguous class.
        if value in neighbours and not _breaches_anything_else(mandate, product):
            return product, f"{attribute} {value} instead of {target}"
    return None, ""


def _near_pack_size(
    mandate: IntentMandate, base: Cart, catalogue: CatalogueService
) -> tuple[Product | None, str]:
    wanted = _wanted(mandate, ConstraintType.PACK_SIZE)
    if not isinstance(wanted, str):
        return None, ""

    current = base.items[0]
    for product in catalogue.products:
        if product.category_path != current.category_path:
            continue
        size = str(product.attributes.get("pack_size", ""))
        if (
            size
            and size != wanted
            and _within(size, wanted, 0.05)
            and not _breaches_anything_else(mandate, product)
        ):
            return product, f"pack size {size} instead of {wanted}"
    return None, ""


def _within(a: str, b: str, tolerance: float) -> bool:
    from app.services.engine.l2_attributes import PackSize

    first, second = PackSize.parse(a), PackSize.parse(b)
    if first is None or second is None or first.dimension != second.dimension:
        return False
    return abs(first.quantity - second.quantity) / second.quantity <= tolerance


def apply_ambiguous_operator(
    operator: str,
    mandate: IntentMandate,
    base: Cart,
    catalogue: CatalogueService,
) -> Perturbation:
    """Operators whose gold label is AMBIGUOUS by construction."""
    product: Product | None = None
    note = ""

    match operator:
        case "near_colour":
            product, note = _near_attribute_swap(
                mandate, base, catalogue, "colour", COLOUR_NEAR_MATCHES
            )
        case "near_material":
            product, note = _near_attribute_swap(
                mandate, base, catalogue, "material", MATERIAL_NEAR_MATCHES
            )
        case "near_pack_size":
            product, note = _near_pack_size(mandate, base, catalogue)
    if product is None or _breaches_anything_else(mandate, product):
        return Perturbation(operator, base, Decision.AMBIGUOUS, applied=False)

    return Perturbation(
        operator, _single_item_cart(base, product), Decision.AMBIGUOUS, True, note
    )


def _is_near_match(mandate: IntentMandate, product: Product) -> bool:
    for c in mandate.constraints:
        if c.type != ConstraintType.ATTRIBUTE or not isinstance(c.value, dict):
            continue
        name, wanted = str(c.value["name"]), str(c.value["value"]).lower()
        actual = str(product.attributes.get(name, "")).lower()
        table = COLOUR_NEAR_MATCHES if name == "colour" else MATERIAL_NEAR_MATCHES
        if actual and actual in table.get(wanted, set()):
            return True
    return False


def _breaches_anything_else(mandate: IntentMandate, product: Product) -> bool:
    """Guard for ambiguity operators: the swap must leave every other hard constraint met."""
    for c in mandate.constraints:
        if c.type in (ConstraintType.BUDGET_TOTAL, ConstraintType.PRICE_ITEM):
            if product.price_paise > int(c.value):
                return True
        elif c.type == ConstraintType.BRAND_IN:
            if product.brand.lower() not in {str(b).lower() for b in c.value}:
                return True
        elif c.type == ConstraintType.MERCHANT:
            if product.merchant_id not in list(c.value):
                return True
        elif (
            c.type == ConstraintType.DIETARY
            and str(c.value) == "veg"
            and product.attributes.get("veg") is not True
        ) or c.type == ConstraintType.RECURRENCE and product.is_subscription != (
            str(c.value) == "recurring"
        ):
            return True
    return False


# --- violation operators ----------------------------------------------------------


def apply_violating_operator(
    operator: str,
    mandate: IntentMandate,
    base: Cart,
    catalogue: CatalogueService,
    rng: random.Random,
) -> Perturbation:
    """Operators whose gold label is VIOLATES by construction."""
    mode = FaultMode(operator)
    current_sku = base.items[0].sku
    product = catalogue.get(current_sku)
    if product is None:
        return Perturbation(operator, base, Decision.VIOLATES, applied=False)

    if mode in SELECTION_FAULTS:
        swapped, outcome = apply_selection_fault(mode, mandate, product, catalogue, rng)
        if not outcome.applied:
            return Perturbation(operator, base, Decision.VIOLATES, applied=False)
        if mode is FaultMode.ATTRIBUTE_SWAP and _is_near_match(mandate, swapped):
            # A near value is an ambiguity, not a breach. Labelling it VIOLATES would
            # punish exactly the behaviour this engine is built for.
            return Perturbation(operator, base, Decision.VIOLATES, applied=False)
        return Perturbation(
            operator, _single_item_cart(base, swapped), Decision.VIOLATES, True, outcome.note
        )

    if mode in CART_FAULTS:
        cart, outcome = apply_cart_fault(mode, base, mandate, rng)
        return Perturbation(
            operator, cart, Decision.VIOLATES, outcome.applied, outcome.note
        )

    return Perturbation(operator, base, Decision.VIOLATES, applied=False)


def apply(
    operator: str,
    mandate: IntentMandate,
    base: Cart,
    catalogue: CatalogueService,
    rng: random.Random,
) -> Perturbation:
    if operator in AMBIGUOUS_OPERATORS:
        return apply_ambiguous_operator(operator, mandate, base, catalogue)
    return apply_violating_operator(operator, mandate, base, catalogue, rng)
