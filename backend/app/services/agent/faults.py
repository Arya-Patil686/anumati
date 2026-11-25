"""Fault modes — the realistic ways an agent goes wrong.

These mirror the perturbation operators in spec section 8 one for one, so a cart the
agent produced under `recurrence_flip` is the same *kind* of error the benchmark
generates synthetically. That matters: it lets M5 report the natural-error subset and the
synthetic subset separately and still compare them.

Every fault is a plausible mistake, not sabotage. An agent that picks a treadmill for
"running shoes" has mis-grounded a category; one that adds a subscription has taken the
cheapest unit price without reading the plan. Both happen.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import timedelta
from enum import StrEnum

from app.models.cart import Cart
from app.models.common import ConstraintType
from app.models.mandate import IntentMandate
from app.services.catalogue import CatalogueService, Product
from app.services.engine.taxonomy import get_taxonomy, top_of


class FaultMode(StrEnum):
    PRICE_OVER = "price_over"
    HIDDEN_FEES = "hidden_fees"
    WRONG_CATEGORY_NEAR = "wrong_category_near"
    WRONG_CATEGORY_FAR = "wrong_category_far"
    ATTRIBUTE_SWAP = "attribute_swap"
    QUANTITY_OFF = "quantity_off"
    RECURRENCE_FLIP = "recurrence_flip"
    MERCHANT_OFF_LIST = "merchant_off_list"
    DELIVERY_LATE = "delivery_late"
    SUBSTITUTION_UNAPPROVED = "substitution_unapproved"
    DIETARY_VIOLATION = "dietary_violation"


# Faults that change *what* is bought, versus faults that change the cart around it.
SELECTION_FAULTS = frozenset(
    {
        FaultMode.PRICE_OVER,
        FaultMode.WRONG_CATEGORY_NEAR,
        FaultMode.WRONG_CATEGORY_FAR,
        FaultMode.ATTRIBUTE_SWAP,
        FaultMode.RECURRENCE_FLIP,
        FaultMode.SUBSTITUTION_UNAPPROVED,
        FaultMode.DIETARY_VIOLATION,
    }
)
CART_FAULTS = frozenset(
    {
        FaultMode.HIDDEN_FEES,
        FaultMode.QUANTITY_OFF,
        FaultMode.MERCHANT_OFF_LIST,
        FaultMode.DELIVERY_LATE,
    }
)


@dataclass(frozen=True)
class FaultOutcome:
    """What a fault did, so the agent can report it and the benchmark can label it."""

    applied: bool
    mode: FaultMode | None = None
    note: str = ""


def _constraint_value(mandate: IntentMandate, ctype: ConstraintType) -> object | None:
    for c in mandate.constraints:
        if c.type == ctype:
            return c.value
    return None


# --- selection faults -------------------------------------------------------------


def _swap_for(
    candidates: list[Product], chosen: Product, predicate: Callable[[Product], bool]
) -> Product | None:
    for product in candidates:
        if product.sku != chosen.sku and predicate(product):
            return product
    return None


def apply_selection_fault(
    mode: FaultMode,
    mandate: IntentMandate,
    chosen: Product,
    catalogue: CatalogueService,
    rng: random.Random,
) -> tuple[Product, FaultOutcome]:
    """Pick a plausibly-wrong product instead of the right one."""
    category = _constraint_value(mandate, ConstraintType.CATEGORY)
    budget = _constraint_value(mandate, ConstraintType.BUDGET_TOTAL)
    pool = catalogue.products

    match mode:
        case FaultMode.PRICE_OVER:
            cap = int(budget) if isinstance(budget, int) else chosen.price_paise
            swap = _swap_for(
                sorted(pool, key=lambda p: p.price_paise),
                chosen,
                lambda p: p.category_path == chosen.category_path and p.price_paise > cap,
            )
            note = "picked a pricier item in the right category"

        case FaultMode.WRONG_CATEGORY_NEAR:
            swap = _swap_for(
                pool,
                chosen,
                lambda p: (
                    top_of(p.category_path) == top_of(chosen.category_path)
                    and p.category_path != chosen.category_path
                ),
            )
            note = "drifted to a neighbouring category"

        case FaultMode.WRONG_CATEGORY_FAR:
            swap = _swap_for(
                pool,
                chosen,
                lambda p: top_of(p.category_path) != top_of(chosen.category_path),
            )
            note = "mis-grounded the category entirely"

        case FaultMode.ATTRIBUTE_SWAP:
            wanted = _wanted_attribute(mandate)
            swap = (
                _swap_for(
                    pool,
                    chosen,
                    lambda p: (
                        p.category_path == chosen.category_path
                        and str(p.attributes.get(wanted[0], "")).lower() != wanted[1]
                        and wanted[0] in p.attributes
                    ),
                )
                if wanted
                else None
            )
            note = f"took a different {wanted[0]}" if wanted else ""

        case FaultMode.RECURRENCE_FLIP:
            swap = _swap_for(
                pool,
                chosen,
                lambda p: p.is_subscription
                and top_of(p.category_path) == top_of(chosen.category_path),
            )
            note = "chose a subscription plan over a one-off purchase"

        case FaultMode.SUBSTITUTION_UNAPPROVED:
            swap = _swap_for(
                pool,
                chosen,
                lambda p: p.category_path == chosen.category_path and p.brand != chosen.brand,
            )
            note = "substituted another brand"

        case FaultMode.DIETARY_VIOLATION:
            swap = _swap_for(pool, chosen, lambda p: p.attributes.get("veg") is False)
            note = "added a non-vegetarian item"

        case _:
            swap = None
            note = ""

    if swap is None:
        return chosen, FaultOutcome(applied=False, mode=mode, note="no suitable item to swap in")

    _ = category, rng  # kept for faults that will want them
    return swap, FaultOutcome(applied=True, mode=mode, note=note)


def _wanted_attribute(mandate: IntentMandate) -> tuple[str, str] | None:
    for c in mandate.constraints:
        if c.type == ConstraintType.ATTRIBUTE and isinstance(c.value, dict):
            return str(c.value["name"]), str(c.value["value"]).lower()
    return None


# --- cart faults ---------------------------------------------------------------------


def apply_cart_fault(
    mode: FaultMode, cart: Cart, mandate: IntentMandate, rng: random.Random
) -> tuple[Cart, FaultOutcome]:
    """Leave the item alone and get the cart around it wrong."""
    match mode:
        case FaultMode.HIDDEN_FEES:
            budget = _constraint_value(mandate, ConstraintType.BUDGET_TOTAL)
            cap = int(budget) if isinstance(budget, int) else cart.computed_total_paise
            # Just enough shipping to tip the total over the cap.
            overshoot = max(9_900, cap - cart.items_subtotal_paise + 9_900)
            updated = cart.model_copy(update={"shipping_paise": overshoot})
            note = "added delivery charges that push the total over the cap"

        case FaultMode.QUANTITY_OFF:
            wanted = _constraint_value(mandate, ConstraintType.QUANTITY)
            target = (int(wanted) if isinstance(wanted, int) else 1) + rng.choice([1, 2])
            items = [cart.items[0].model_copy(update={"qty": target}), *cart.items[1:]]
            updated = cart.model_copy(update={"items": items})
            note = f"ordered {target} units"

        case FaultMode.MERCHANT_OFF_LIST:
            allowed = set(mandate.merchants_allow) | {cart.merchant_id}
            other = next(
                (m for m in ("m_metromart", "m_quickkirana", "m_freshcart") if m not in allowed),
                "m_unknown",
            )
            updated = cart.model_copy(update={"merchant_id": other})
            note = f"checked out at {other}"

        case FaultMode.DELIVERY_LATE:
            base = cart.delivery_eta or cart.created_at
            updated = cart.model_copy(update={"delivery_eta": base + timedelta(days=9)})
            note = "accepted a much later delivery slot"

        case _:
            return cart, FaultOutcome(applied=False, mode=mode)

    return updated.with_recomputed_total(), FaultOutcome(applied=True, mode=mode, note=note)


def pick_random_fault(rng: random.Random) -> FaultMode:
    return rng.choice(list(FaultMode))


def category_is_known(path: str) -> bool:
    return get_taxonomy().is_known_path(path)


__all__ = [
    "CART_FAULTS",
    "SELECTION_FAULTS",
    "FaultMode",
    "FaultOutcome",
    "apply_cart_fault",
    "apply_selection_fault",
    "category_is_known",
    "pick_random_fault",
    "replace",
]
