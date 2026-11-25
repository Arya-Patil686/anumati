"""Layer 2 — attribute grounding against the catalogue's structured fields.

This is where "is an off-white trail shoe a white running shoe?" gets answered, and the
answer is deliberately allowed to be *no clear answer*. L2 decides the clear wins and the
clear losses; everything in between is handed up as `unclear` with a confidence, which is
what produces the AMBIGUOUS band rather than a confident wrong verdict.

Near-match tables are explicit and small on purpose. A dispute pack has to be able to say
"off-white was treated as a near match for white", and that sentence has to point at a
line in this file, not at an embedding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.cart import Cart, CartItem
from app.models.common import ConstraintStatus, ConstraintType, Operator
from app.models.mandate import Constraint, IntentMandate
from app.models.verdict import ConstraintResult
from app.services.engine.taxonomy import Taxonomy, get_taxonomy, top_of

L2_TYPES = frozenset(
    {
        ConstraintType.CATEGORY,
        ConstraintType.ATTRIBUTE,
        ConstraintType.BRAND_IN,
        ConstraintType.BRAND_OUT,
        ConstraintType.DIETARY,
        ConstraintType.PACK_SIZE,
        ConstraintType.SUBSTITUTION,
        ConstraintType.REFUNDABLE,
    }
)

# Colours a shopper may or may not accept as the one they asked for. Symmetric.
COLOUR_NEAR_MATCHES: dict[str, set[str]] = {
    "white": {"off-white", "cream", "ivory", "beige"},
    "off-white": {"white", "cream", "ivory", "beige"},
    "beige": {"off-white", "cream", "tan"},
    "grey": {"silver", "charcoal", "slate"},
    "navy": {"blue"},
    "blue": {"navy"},
    "brown": {"tan"},
    "tan": {"brown", "beige"},
}

MATERIAL_NEAR_MATCHES: dict[str, set[str]] = {
    "leather": {"synthetic leather"},
    "synthetic leather": {"leather"},
    "mesh": {"knit"},
    "knit": {"mesh"},
    "cotton": {"cotton blend"},
    "cotton blend": {"cotton"},
}

# How close a pack size has to be before it stops being the thing that was asked for.
PACK_SIZE_NEAR_TOLERANCE = 0.05  # 4.9 kg against 5 kg is a near match, 4 kg is not

_UNIT_TO_BASE: dict[str, tuple[str, float]] = {
    "g": ("mass", 1.0), "gram": ("mass", 1.0), "grams": ("mass", 1.0),
    "kg": ("mass", 1000.0), "kgs": ("mass", 1000.0),
    "ml": ("volume", 1.0),
    "l": ("volume", 1000.0), "ltr": ("volume", 1000.0),
    "litre": ("volume", 1000.0), "litres": ("volume", 1000.0),
    "liter": ("volume", 1000.0), "liters": ("volume", 1000.0),
}

CONFIDENCE_EXACT = 0.99
CONFIDENCE_CLEAR_MISS = 0.95
CONFIDENCE_NEAR = 0.55  # deliberately below T_CONFORM, so a near match never passes alone
CONFIDENCE_SIBLING_MISS = 0.85  # a precise term got a neighbouring category


@dataclass(frozen=True)
class PackSize:
    quantity: float
    dimension: str  # "mass" | "volume"

    @classmethod
    def parse(cls, text: str) -> PackSize | None:
        match = re.match(r"\s*(\d+(?:\.\d+)?)\s*([a-z]+)\s*$", str(text).strip().lower())
        if not match:
            return None
        unit = _UNIT_TO_BASE.get(match.group(2))
        if unit is None:
            return None
        dimension, factor = unit
        return cls(quantity=float(match.group(1)) * factor, dimension=dimension)


def _result(
    constraint: Constraint,
    status: ConstraintStatus,
    *,
    confidence: float,
    evidence: list[str],
    rationale: str,
) -> ConstraintResult:
    return ConstraintResult(
        constraint_id=constraint.id,
        layer=2,
        status=status,
        confidence=confidence,
        evidence_fields=evidence,
        rationale=rationale,
        hard=constraint.hard,
    )


def _undecidable(constraint: Constraint, why: str) -> ConstraintResult:
    return _result(
        constraint,
        ConstraintStatus.NOT_DECIDABLE_HERE,
        confidence=0.0,
        evidence=[],
        rationale=why,
    )


# --- individual checks --------------------------------------------------------------


def check_category(
    constraint: Constraint, cart: Cart, taxonomy: Taxonomy
) -> ConstraintResult:
    """Exact path is a win; a different top-level category is a clear loss.

    Same branch but a different leaf is the interesting case — a trail shoe for
    "running shoes" — and it goes up as unclear.
    """
    wanted = str(constraint.value)
    paths = [item.category_path for item in cart.items if item.category_path]
    if not paths:
        return _undecidable(constraint, "no cart item carries a category path")

    # A term the user typed that the taxonomy itself considers ambiguous can never be
    # settled here, however well the paths line up.
    term_is_near_miss = False
    if constraint.source_span:
        match = taxonomy.resolve(constraint.source_span)
        term_is_near_miss = bool(match and match.is_near_miss)

    off_branch = [p for p in paths if top_of(p) != top_of(wanted)]
    if off_branch:
        return _result(
            constraint,
            ConstraintStatus.VIOLATED,
            confidence=CONFIDENCE_CLEAR_MISS,
            evidence=["category_path"],
            rationale=f"cart holds {off_branch[0]!r} but the mandate asks for {wanted!r}",
        )

    exact = [p for p in paths if p == wanted]
    if len(exact) == len(paths) and not term_is_near_miss:
        return _result(
            constraint,
            ConstraintStatus.SATISFIED,
            confidence=CONFIDENCE_EXACT,
            evidence=["category_path"],
            rationale=f"every item is in {wanted!r}",
        )

    if term_is_near_miss:
        return _result(
            constraint,
            ConstraintStatus.UNCLEAR,
            confidence=CONFIDENCE_NEAR,
            evidence=["category_path"],
            rationale=(
                f"{constraint.source_span!r} does not name one category precisely; "
                f"cart holds {paths[0]!r}"
            ),
        )

    # The user named their category precisely and got a sibling of it — training shoes
    # for "running shoes". That is a breach, not a matter of taste. Confidence sits
    # below a cross-branch miss because sibling categories genuinely do overlap.
    return _result(
        constraint,
        ConstraintStatus.VIOLATED,
        confidence=CONFIDENCE_SIBLING_MISS,
        evidence=["category_path"],
        rationale=(
            f"cart holds {paths[0]!r} but {constraint.source_span or wanted!r} names "
            f"{wanted!r}"
        ),
    )


def _attribute_values(cart: Cart, name: str) -> list[tuple[CartItem, str]]:
    out: list[tuple[CartItem, str]] = []
    for item in cart.items:
        value = item.attributes.get(name)
        if value is not None:
            out.append((item, str(value).lower()))
    return out


def check_attribute(constraint: Constraint, cart: Cart) -> ConstraintResult:
    name = str(constraint.value["name"])
    wanted = str(constraint.value["value"]).lower()
    present = _attribute_values(cart, name)

    if not present:
        return _undecidable(constraint, f"no cart item declares a {name}")

    actual = {value for _item, value in present}
    if actual == {wanted}:
        return _result(
            constraint,
            ConstraintStatus.SATISFIED,
            confidence=CONFIDENCE_EXACT,
            evidence=[f"attributes.{name}"],
            rationale=f"{name} is {wanted}",
        )

    near = COLOUR_NEAR_MATCHES if name == "colour" else MATERIAL_NEAR_MATCHES
    neighbours = near.get(wanted, set())
    if actual and actual <= (neighbours | {wanted}):
        other = ", ".join(sorted(actual - {wanted}))
        return _result(
            constraint,
            ConstraintStatus.UNCLEAR,
            confidence=CONFIDENCE_NEAR,
            evidence=[f"attributes.{name}"],
            rationale=f"{name} is {other}, a near match for {wanted} but not the same",
        )

    return _result(
        constraint,
        ConstraintStatus.VIOLATED,
        confidence=CONFIDENCE_CLEAR_MISS,
        evidence=[f"attributes.{name}"],
        rationale=f"{name} is {', '.join(sorted(actual))}, not {wanted}",
    )


def check_brand(constraint: Constraint, cart: Cart) -> ConstraintResult:
    wanted = {str(b).lower() for b in constraint.value}
    brands = {item.brand.lower() for item in cart.items if item.brand}
    if not brands:
        return _undecidable(constraint, "no cart item declares a brand")

    deny = constraint.type == ConstraintType.BRAND_OUT or constraint.operator == Operator.NOT_IN
    overlap = brands & wanted

    if deny:
        violated = bool(overlap)
        rationale = (
            f"{', '.join(sorted(overlap))} is excluded by the mandate"
            if violated
            else f"none of {', '.join(sorted(wanted))} is in the cart"
        )
    else:
        violated = not brands <= wanted
        outside = brands - wanted
        rationale = (
            f"{', '.join(sorted(outside))} is not among {', '.join(sorted(wanted))}"
            if violated
            else f"brand is {', '.join(sorted(brands))}"
        )

    return _result(
        constraint,
        ConstraintStatus.VIOLATED if violated else ConstraintStatus.SATISFIED,
        confidence=CONFIDENCE_EXACT if not violated else CONFIDENCE_CLEAR_MISS,
        evidence=["brand"],
        rationale=rationale,
    )


def check_dietary(constraint: Constraint, cart: Cart) -> ConstraintResult:
    wanted = str(constraint.value).lower()
    flags = [(item, item.attributes.get("veg")) for item in cart.items]
    known = [(item, bool(flag)) for item, flag in flags if flag is not None]

    if not known:
        return _undecidable(constraint, "no cart item declares a veg/non-veg flag")
    if len(known) < len(flags):
        missing = [item.sku for item, flag in flags if flag is None]
        return _undecidable(constraint, f"no veg/non-veg flag on {', '.join(missing)}")

    if wanted == "veg":
        offenders = [item.sku for item, is_veg in known if not is_veg]
        if offenders:
            return _result(
                constraint,
                ConstraintStatus.VIOLATED,
                confidence=CONFIDENCE_EXACT,
                evidence=["attributes.veg"],
                rationale=f"{', '.join(offenders)} is not vegetarian",
            )
        return _result(
            constraint,
            ConstraintStatus.SATISFIED,
            confidence=CONFIDENCE_EXACT,
            evidence=["attributes.veg"],
            rationale="every item is vegetarian",
        )

    # "eggless" and anything else needs more than a veg flag can tell us.
    return _undecidable(constraint, f"a veg flag cannot establish {wanted!r}")


def check_pack_size(constraint: Constraint, cart: Cart) -> ConstraintResult:
    wanted = PackSize.parse(str(constraint.value))
    if wanted is None:
        return _undecidable(constraint, f"cannot read the pack size {constraint.value!r}")

    present = _attribute_values(cart, "pack_size")
    if not present:
        return _undecidable(constraint, "no cart item declares a pack size")

    for item, raw in present:
        actual = PackSize.parse(raw)
        if actual is None or actual.dimension != wanted.dimension:
            return _undecidable(constraint, f"cannot compare {raw!r} with {constraint.value!r}")

        if actual.quantity == wanted.quantity:
            continue

        drift = abs(actual.quantity - wanted.quantity) / wanted.quantity
        status = (
            ConstraintStatus.UNCLEAR if drift <= PACK_SIZE_NEAR_TOLERANCE
            else ConstraintStatus.VIOLATED
        )
        return _result(
            constraint,
            status,
            confidence=CONFIDENCE_NEAR if status is ConstraintStatus.UNCLEAR
            else CONFIDENCE_CLEAR_MISS,
            evidence=["attributes.pack_size"],
            rationale=(
                f"{item.sku} is {raw}, {drift:.0%} away from the requested "
                f"{constraint.value}"
            ),
        )

    return _result(
        constraint,
        ConstraintStatus.SATISFIED,
        confidence=CONFIDENCE_EXACT,
        evidence=["attributes.pack_size"],
        rationale=f"pack size is {constraint.value}",
    )


def check_substitution(
    constraint: Constraint, cart: Cart, mandate: IntentMandate
) -> ConstraintResult:
    """"No substitutes" is only checkable against something the user named.

    If they named a brand and the cart carries another one, that is a substitution.
    With nothing named, there is nothing here to compare and L3 gets it.
    """
    requested_brands = {
        str(b).lower()
        for c in mandate.constraints
        if c.type == ConstraintType.BRAND_IN
        for b in c.value
    }
    if not requested_brands:
        return _undecidable(constraint, "the mandate names no brand to substitute away from")

    brands = {item.brand.lower() for item in cart.items if item.brand}
    swapped = brands - requested_brands
    if swapped:
        return _result(
            constraint,
            ConstraintStatus.VIOLATED,
            confidence=CONFIDENCE_CLEAR_MISS,
            evidence=["brand"],
            rationale=f"{', '.join(sorted(swapped))} substituted for "
            f"{', '.join(sorted(requested_brands))}, which was not permitted",
        )
    return _result(
        constraint,
        ConstraintStatus.SATISFIED,
        confidence=CONFIDENCE_EXACT,
        evidence=["brand"],
        rationale="no brand substitution was made",
    )


def check_refundable(constraint: Constraint, cart: Cart) -> ConstraintResult:
    flags = [item.attributes.get("refundable") for item in cart.items]
    if any(flag is None for flag in flags):
        return _undecidable(constraint, "the catalogue carries no returnability flag")

    offenders = [item.sku for item in cart.items if not item.attributes.get("refundable")]
    if offenders:
        return _result(
            constraint,
            ConstraintStatus.VIOLATED,
            confidence=CONFIDENCE_EXACT,
            evidence=["attributes.refundable"],
            rationale=f"{', '.join(offenders)} is not returnable",
        )
    return _result(
        constraint,
        ConstraintStatus.SATISFIED,
        confidence=CONFIDENCE_EXACT,
        evidence=["attributes.refundable"],
        rationale="every item is returnable",
    )


# --- entry point -----------------------------------------------------------------------


def evaluate(
    mandate: IntentMandate,
    cart: Cart,
    unresolved: set[str],
    *,
    taxonomy: Taxonomy | None = None,
) -> list[ConstraintResult]:
    """Judge the constraints L1 left open. `unresolved` holds their constraint ids."""
    taxonomy = taxonomy or get_taxonomy()
    results: list[ConstraintResult] = []

    for constraint in mandate.constraints:
        if constraint.id not in unresolved or constraint.type not in L2_TYPES:
            continue

        match constraint.type:
            case ConstraintType.CATEGORY:
                results.append(check_category(constraint, cart, taxonomy))
            case ConstraintType.ATTRIBUTE:
                results.append(check_attribute(constraint, cart))
            case ConstraintType.BRAND_IN | ConstraintType.BRAND_OUT:
                results.append(check_brand(constraint, cart))
            case ConstraintType.DIETARY:
                results.append(check_dietary(constraint, cart))
            case ConstraintType.PACK_SIZE:
                results.append(check_pack_size(constraint, cart))
            case ConstraintType.SUBSTITUTION:
                results.append(check_substitution(constraint, cart, mandate))
            case ConstraintType.REFUNDABLE:
                results.append(check_refundable(constraint, cart))

    return results
