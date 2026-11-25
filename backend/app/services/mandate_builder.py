"""Instruction → signed IntentMandate, plus the plain-language read-back.

The user's words are the only authority in this system, so this module is deliberately
**rule-based, not model-based**. Three reasons:

1. The read-back is what the user signs. It has to be a faithful restatement of their
   own sentence, and every constraint carries the `source_span` it came from.
2. It must run offline and give the same answer twice. An LLM-parsed mandate that drifts
   between runs would make the whole ledger worthless as evidence.
3. The engine's LLM judge (M3) is where model judgement belongs. Putting a model on both
   ends would make the benchmark circular (CLAUDE.md rule 6).

The LLM client is still available here for a future category-normalisation pass on
instructions the taxonomy does not recognise; it is not on the default path.

Hinglish is handled in the same patterns as English rather than in a separate parser,
because real instructions code-mix inside a single sentence: "5000 se kam ke white
running shoes, Friday tak chahiye".
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any

from pydantic import BaseModel

from app.config import settings
from app.models.common import (
    ConstraintType,
    Operator,
    Recurrence,
    utc_now,
)
from app.models.mandate import Constraint, IntentMandate
from app.services.engine.taxonomy import Taxonomy, get_taxonomy

DEFAULT_BUDGET_PAISE = 1_000_000  # ₹10,000 — used only when the user states no cap
DEFAULT_VALIDITY_DAYS = 7

# --- vocabulary ----------------------------------------------------------------

SOFT_MARKERS = (
    "preferably", "ideally", "if possible", "would be nice", "nice to have",
    "agar ho sake", "agar mil jaye", "ho sake to", "try to", "prefer",
)

COLOURS: dict[str, str] = {
    "white": "white", "safed": "white",
    "off-white": "off-white", "off white": "off-white", "offwhite": "off-white",
    "black": "black", "kala": "black", "kaala": "black",
    "navy": "navy", "blue": "blue", "neela": "blue", "neela rang": "blue",
    "grey": "grey", "gray": "grey", "slate": "grey",
    "red": "red", "laal": "red", "lal": "red",
    "green": "green", "hara": "green",
    "beige": "beige", "brown": "brown", "tan": "tan",
}

MATERIALS = ("mesh", "knit", "leather", "synthetic leather", "canvas", "cotton",
             "polyester", "rubber", "silk", "rayon")

QUANTITY_UNITS = ("pairs", "pair", "packets", "packet", "pieces", "piece", "units",
                  "unit", "boxes", "box", "bottles", "bottle", "packs", "pack",
                  "jodi", "adad")

HINGLISH_NUMBERS: dict[str, int] = {
    "ek": 1, "do": 2, "teen": 3, "char": 4, "chaar": 4, "paanch": 5, "panch": 5,
    "chhe": 6, "che": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10,
}

WEEKDAYS: dict[str, int] = {
    "monday": 0, "somvar": 0, "tuesday": 1, "mangalvar": 1, "wednesday": 2,
    "budhvar": 2, "thursday": 3, "guruvar": 3, "friday": 4, "shukravar": 4,
    "saturday": 5, "shanivar": 5, "sunday": 6, "ravivar": 6,
}

HINGLISH_MARKERS = (
    "se kam", "ke andar", "tak", "chahiye", "nahi", "mat", "karna", "har mahine",
    "ek baar", "din", "sirf", "bina", "wala", "wale", "ka", "ke", "ki", "mein",
    "se", "jodi", "hazaar", "hazar", "rupaye", "rupee",
)

MERCHANT_NAMES: dict[str, str] = {
    "bigbasket": "m_bigbasket", "big basket": "m_bigbasket",
    "freshcart": "m_freshcart", "fresh cart": "m_freshcart",
    "sportyfy": "m_sportyfy",
    "metromart": "m_metromart", "metro mart": "m_metromart",
    "quickkirana": "m_quickkirana", "quick kirana": "m_quickkirana",
}

_MONEY_UNITS: dict[str, int] = {
    "k": 1_000, "hazaar": 1_000, "hazar": 1_000, "hajar": 1_000,
    "thousand": 1_000, "lakh": 100_000, "lac": 100_000,
}

_MONEY = r"(?:₹|rs\.?|inr)?\s*(\d[\d,]*(?:\.\d{1,2})?)\s*(k|hazaar|hazar|hajar|thousand|lakh|lac)?"

# "within 3 days" and "under 5 kg" are not budgets. A bare number followed by a time,
# weight or count unit is never money, so the budget patterns refuse it.
_NOT_MONEY_UNIT = (
    r"(?!\s*(?:days?|din|hours?|ghante|weeks?|hafte|months?|mahine|"
    r"kgs?|gms?|grams?|g|ml|l|litres?|liters?|ltr|"
    r"pairs?|packets?|pieces?|units?|boxes?|bottles?|packs?|jodi|adad|%)\b)"
)


class MandateBuildError(ValueError):
    """The instruction could not be turned into a usable mandate."""


@dataclass(frozen=True)
class Extraction:
    constraint: Constraint
    start: int
    end: int


class BuiltMandate(BaseModel):
    """What POST /mandates returns: the structured mandate and what the user must read."""

    mandate: IntentMandate
    read_back: str
    unparsed_tail: str = ""


# --- small parsers --------------------------------------------------------------


def parse_money_to_paise(amount: str, unit: str | None) -> int:
    """'5,000' + 'k' → 500000000 paise. Integer paise out, always (rule 1)."""
    cleaned = amount.replace(",", "")
    multiplier = _MONEY_UNITS.get((unit or "").lower(), 1)
    rupees = float(cleaned) * multiplier
    return round(rupees * 100)


def is_soft(text: str, start: int, end: int, window: int = 40) -> bool:
    """A constraint is soft when a hedging phrase sits next to it."""
    context = text[max(0, start - window) : min(len(text), end + window)].lower()
    return any(marker in context for marker in SOFT_MARKERS)


def detect_language(text: str) -> str:
    """'hi-Latn' for code-mixed Hinglish, else 'en'. Used for reporting, not parsing."""
    lowered = f" {text.lower()} "
    hits = sum(1 for marker in HINGLISH_MARKERS if f" {marker} " in lowered)
    if any(d in text for d in "अआइईउऊएऐओऔकखगघचछजझटठडढतथदधनपफबभमयरलवशषसह"):
        return "hi"
    return "hi-Latn" if hits >= 2 else "en"


def resolve_weekday(name: str, today: date) -> date:
    """'friday' → the coming Friday; today if today is that day."""
    target = WEEKDAYS[name]
    ahead = (target - today.weekday()) % 7
    return today + timedelta(days=ahead)


# --- extractors -----------------------------------------------------------------
# Each returns the constraints it is sure of, with the span of text it read them from.


def _constraint(
    text: str,
    match: re.Match[str],
    ctype: ConstraintType,
    operator: Operator,
    value: Any,
    *,
    force_hard: bool | None = None,
) -> Extraction:
    start, end = match.span()
    hard = force_hard if force_hard is not None else not is_soft(text, start, end)
    return Extraction(
        constraint=Constraint(
            type=ctype,
            operator=operator,
            value=value,
            hard=hard,
            source_span=text[start:end].strip(" ,.;:!?"),
        ),
        start=start,
        end=end,
    )


def extract_budget(text: str) -> list[Extraction]:
    """Total spend cap, and the per-item variant when the phrasing says 'each'."""
    out: list[Extraction] = []
    lowered = text.lower()

    patterns = (
        rf"(?:under|below|less than|within|upto|up to|max(?:imum)?|budget(?: of)?|"
        rf"not more than)\s*{_MONEY}{_NOT_MONEY_UNIT}",
        rf"{_MONEY}\s*(?:se kam|ke andar|ke neeche|se niche|tak ka|ke budget)",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, lowered):
            paise = parse_money_to_paise(match.group(1), match.group(2))
            if paise <= 0:
                continue
            tail = lowered[match.end() : match.end() + 25]
            head = lowered[max(0, match.start() - 25) : match.start()]
            per_item = any(
                word in tail or word in head
                for word in ("each", "per item", "per pair", "per piece", "prati", "ek ka")
            )
            out.append(
                _constraint(
                    text,
                    match,
                    ConstraintType.PRICE_ITEM if per_item else ConstraintType.BUDGET_TOTAL,
                    Operator.LTE,
                    paise,
                    force_hard=True,
                )
            )
    return out


def extract_category(text: str, taxonomy: Taxonomy) -> list[Extraction]:
    match = taxonomy.find_in(text)
    if match is None:
        return []

    found = re.search(rf"(?<!\w){re.escape(match.term)}(?!\w)", text.lower())
    if found is None:
        return []
    return [
        _constraint(
            text, found, ConstraintType.CATEGORY, Operator.EQ, match.path, force_hard=True
        )
    ]


def extract_attributes(text: str) -> list[Extraction]:
    """Colour, size and material — the fields L2 can check against the catalogue."""
    out: list[Extraction] = []
    lowered = text.lower()

    for term in sorted(COLOURS, key=len, reverse=True):
        found = re.search(rf"(?<!\w){re.escape(term)}(?!\w)", lowered)
        if found:
            out.append(
                _constraint(
                    text, found, ConstraintType.ATTRIBUTE, Operator.EQ,
                    {"name": "colour", "value": COLOURS[term]},
                )
            )
            break

    size = re.search(r"\b(?:size\s*)?(uk\s*\d{1,2}|size\s*(?:xs|s|m|l|xl|xxl))\b", lowered)
    if size:
        out.append(
            _constraint(
                text, size, ConstraintType.ATTRIBUTE, Operator.EQ,
                {"name": "size", "value": size.group(1).replace("size", "").strip().upper()},
            )
        )

    for material in sorted(MATERIALS, key=len, reverse=True):
        found = re.search(rf"(?<!\w){re.escape(material)}(?!\w)", lowered)
        if found:
            out.append(
                _constraint(
                    text, found, ConstraintType.ATTRIBUTE, Operator.EQ,
                    {"name": "material", "value": material},
                )
            )
            break

    return out


def extract_quantity(text: str) -> list[Extraction]:
    lowered = text.lower()
    units = "|".join(QUANTITY_UNITS)
    numeric = re.search(rf"\b(\d{{1,3}})\s*({units})\b", lowered)
    if numeric:
        return [
            _constraint(text, numeric, ConstraintType.QUANTITY, Operator.EQ, int(numeric.group(1)))
        ]

    words = "|".join(HINGLISH_NUMBERS)
    worded = re.search(rf"\b({words})\s*({units})\b", lowered)
    if worded:
        return [
            _constraint(
                text, worded, ConstraintType.QUANTITY, Operator.EQ,
                HINGLISH_NUMBERS[worded.group(1)],
            )
        ]
    return []


def extract_pack_size(text: str) -> list[Extraction]:
    lowered = text.lower()
    match = re.search(r"\b(\d{1,3}(?:\.\d{1,2})?)\s*(kg|g|gram|grams|ml|l|litre|liter|ltr)\b",
                      lowered)
    if not match:
        return []

    unit = {"gram": "g", "grams": "g", "litre": "L", "liter": "L", "ltr": "L", "l": "L"}.get(
        match.group(2), match.group(2)
    )
    return [
        _constraint(
            text, match, ConstraintType.PACK_SIZE, Operator.EQ, f"{match.group(1)} {unit}"
        )
    ]


def extract_brands(text: str, brands: set[str]) -> list[Extraction]:
    """Brand allow/deny, matched against the brands the catalogue actually carries."""
    out: list[Extraction] = []
    lowered = text.lower()

    for brand in sorted(brands, key=len, reverse=True):
        found = re.search(rf"(?<!\w){re.escape(brand.lower())}(?!\w)", lowered)
        if not found:
            continue

        head = lowered[max(0, found.start() - 20) : found.start()]
        tail = lowered[found.end() : found.end() + 20]
        negated = any(
            word in head for word in ("not ", "no ", "except ", "avoid ", "other than ")
        ) or any(word in tail for word in (" nahi", " mat", " chhod"))

        out.append(
            _constraint(
                text, found,
                ConstraintType.BRAND_OUT if negated else ConstraintType.BRAND_IN,
                Operator.NOT_IN if negated else Operator.IN,
                [brand],
            )
        )
    return out


def extract_merchant(text: str) -> list[Extraction]:
    lowered = text.lower()
    for name in sorted(MERCHANT_NAMES, key=len, reverse=True):
        found = re.search(rf"(?<!\w){re.escape(name)}(?!\w)", lowered)
        if found:
            return [
                _constraint(
                    text, found, ConstraintType.MERCHANT, Operator.IN, [MERCHANT_NAMES[name]]
                )
            ]
    return []


def extract_delivery_by(text: str, today: date) -> list[Extraction]:
    lowered = text.lower()

    relative = re.search(r"\b(?:within|in)\s*(\d{1,2})\s*(?:days?|din)\b", lowered) or re.search(
        r"\b(\d{1,2})\s*(?:days?|din)\s*(?:mein|me|in)\b", lowered
    )
    if relative:
        due = today + timedelta(days=int(relative.group(1)))
        return [
            _constraint(text, relative, ConstraintType.DELIVERY_BY, Operator.LTE, due.isoformat())
        ]

    days = "|".join(WEEKDAYS)
    weekday = re.search(rf"\b(?:by|before|till|until)\s*({days})\b", lowered) or re.search(
        rf"\b({days})\s*(?:tak|se pehle)\b", lowered
    )
    if weekday:
        due = resolve_weekday(weekday.group(1), today)
        return [
            _constraint(text, weekday, ConstraintType.DELIVERY_BY, Operator.LTE, due.isoformat())
        ]

    soon = re.search(r"\b(today|aaj|tomorrow|kal)\b", lowered)
    if soon:
        offset = 0 if soon.group(1) in ("today", "aaj") else 1
        due = today + timedelta(days=offset)
        return [
            _constraint(text, soon, ConstraintType.DELIVERY_BY, Operator.LTE, due.isoformat())
        ]
    return []


def extract_recurrence(text: str) -> list[Extraction]:
    lowered = text.lower()

    once = re.search(
        r"\b(one[- ]off|one[- ]time|just once|only once|ek baar|sirf ek baar|"
        r"no subscription|subscription nahi|don'?t subscribe)\b",
        lowered,
    )
    if once:
        return [
            _constraint(
                text, once, ConstraintType.RECURRENCE, Operator.EQ,
                Recurrence.ONE_OFF.value, force_hard=True,
            )
        ]

    repeating = re.search(
        r"\b(subscription|subscribe|monthly|every month|har mahine|weekly|every week|"
        r"har hafte|daily|every day|roz|roj)\b",
        lowered,
    )
    if repeating:
        return [
            _constraint(
                text, repeating, ConstraintType.RECURRENCE, Operator.EQ,
                Recurrence.RECURRING.value, force_hard=True,
            )
        ]
    return []


def extract_substitution(text: str) -> list[Extraction]:
    match = re.search(
        r"\b(no substitut\w*|don'?t substitut\w*|do not substitut\w*|substitute mat|"
        r"koi substitute nahi|exact brand only|same brand only)\b",
        text.lower(),
    )
    if not match:
        return []
    return [
        _constraint(
            text, match, ConstraintType.SUBSTITUTION, Operator.IS_FALSE, False, force_hard=True
        )
    ]


def extract_dietary(text: str) -> list[Extraction]:
    lowered = text.lower()

    veg = re.search(
        r"\b(veg only|only veg|pure veg|vegetarian|sirf veg|veg hi|no non[- ]veg|"
        r"non[- ]veg nahi|bina non[- ]veg)\b",
        lowered,
    )
    if veg:
        return [
            _constraint(text, veg, ConstraintType.DIETARY, Operator.EQ, "veg", force_hard=True)
        ]

    eggless = re.search(r"\b(eggless|no egg|anda nahi)\b", lowered)
    if eggless:
        return [
            _constraint(
                text, eggless, ConstraintType.DIETARY, Operator.EQ, "eggless", force_hard=True
            )
        ]
    return []


def extract_refundable(text: str) -> list[Extraction]:
    match = re.search(
        r"\b(returnable|refundable|with return|return policy|returns? allowed|"
        r"wapas ho sake|return hona chahiye)\b",
        text.lower(),
    )
    if not match:
        return []
    return [_constraint(text, match, ConstraintType.REFUNDABLE, Operator.IS_TRUE, True)]


# --- the builder ----------------------------------------------------------------


class MandateBuilder:
    """Turns one instruction into one IntentMandate, deterministically."""

    def __init__(
        self,
        taxonomy: Taxonomy | None = None,
        brands: set[str] | None = None,
        default_budget_paise: int = DEFAULT_BUDGET_PAISE,
    ) -> None:
        self.taxonomy = taxonomy or get_taxonomy()
        self.brands = brands if brands is not None else _catalogue_brands()
        self.default_budget_paise = default_budget_paise

    def extract(self, instruction: str, today: date) -> list[Extraction]:
        return [
            *extract_budget(instruction),
            *extract_category(instruction, self.taxonomy),
            *extract_attributes(instruction),
            *extract_quantity(instruction),
            *extract_pack_size(instruction),
            *extract_brands(instruction, self.brands),
            *extract_merchant(instruction),
            *extract_delivery_by(instruction, today),
            *extract_recurrence(instruction),
            *extract_substitution(instruction),
            *extract_dietary(instruction),
            *extract_refundable(instruction),
        ]

    def build(
        self,
        instruction: str,
        user_id: str,
        *,
        today: date | None = None,
        language: str | None = None,
    ) -> BuiltMandate:
        text = instruction.strip()
        if not text:
            raise MandateBuildError("instruction is empty")

        today = today or utc_now().date()
        extractions = _dedupe(self.extract(text, today))
        constraints = [e.constraint for e in extractions]

        budget = _budget_from(constraints)
        if budget is None:
            budget = self.default_budget_paise
            constraints.append(
                Constraint(
                    type=ConstraintType.BUDGET_TOTAL,
                    operator=Operator.LTE,
                    value=budget,
                    hard=True,
                    source_span=None,  # not from the user's words — said so in the read-back
                )
            )

        mandate = IntentMandate(
            user_id=user_id,
            natural_language=text,
            language=language or detect_language(text),
            constraints=constraints,
            budget_total_paise=budget,
            expires_at=_expiry(constraints, today),
            merchants_allow=_merchants(constraints),
            recurrence=_recurrence(constraints),
            max_uses=1,
            user_cart_confirmation_required=False,
        )

        return BuiltMandate(
            mandate=mandate,
            read_back=render_read_back(mandate),
            unparsed_tail=_unparsed(text, extractions),
        )


def _catalogue_brands() -> set[str]:
    """Brands the catalogue actually carries. Loaded lazily to keep imports cheap."""
    from app.services.catalogue import get_catalogue

    return {p.brand for p in get_catalogue().products if p.brand}


def _dedupe(extractions: list[Extraction]) -> list[Extraction]:
    """One constraint per (type, value). Keep the first, which read the earliest span."""
    seen: set[tuple[str, str]] = set()
    out: list[Extraction] = []
    for e in sorted(extractions, key=lambda x: x.start):
        key = (str(e.constraint.type), repr(e.constraint.value))
        if key in seen:
            continue
        seen.add(key)
        out.append(e)
    return out


def _budget_from(constraints: list[Constraint]) -> int | None:
    for c in constraints:
        if c.type == ConstraintType.BUDGET_TOTAL:
            return int(c.value)
    return None


def _merchants(constraints: list[Constraint]) -> list[str]:
    out: list[str] = []
    for c in constraints:
        if c.type == ConstraintType.MERCHANT and c.operator == Operator.IN:
            out.extend(c.value)
    return out


def _recurrence(constraints: list[Constraint]) -> Recurrence:
    for c in constraints:
        if c.type == ConstraintType.RECURRENCE:
            return Recurrence(c.value)
    return Recurrence.ONE_OFF


def _expiry(constraints: list[Constraint], today: date) -> datetime:
    """Valid until two days after the delivery deadline, else a week."""
    for c in constraints:
        if c.type == ConstraintType.DELIVERY_BY:
            due = date.fromisoformat(str(c.value)) + timedelta(days=2)
            return datetime.combine(due, time.max, tzinfo=utc_now().tzinfo)
    return utc_now() + timedelta(days=DEFAULT_VALIDITY_DAYS)


def _unparsed(text: str, extractions: list[Extraction]) -> str:
    """Words no extractor claimed. Surfaced so the user can see what was ignored."""
    covered = [False] * len(text)
    for e in extractions:
        for i in range(e.start, min(e.end, len(text))):
            covered[i] = True
    leftover = "".join(ch if not covered[i] else " " for i, ch in enumerate(text))
    return " ".join(leftover.split())


# --- read-back -------------------------------------------------------------------


def paise_to_rupees(paise: int) -> str:
    """₹4,499 or ₹4,499.50 — for display only. Never parsed back."""
    rupees, remainder = divmod(int(paise), 100)
    return f"₹{rupees:,}" if remainder == 0 else f"₹{rupees:,}.{remainder:02d}"


def describe(constraint: Constraint) -> str:
    """One constraint as a sentence the user can check against what they meant."""
    c = constraint
    value = c.value

    match c.type:
        case ConstraintType.CATEGORY:
            # Quote the user's own words, and show the category they resolved to.
            term = (c.source_span or value.split(">")[-1]).strip().lower()
            text = f"the item must be {term} (category: {value})"
        case ConstraintType.ATTRIBUTE:
            text = f"{value['name']} must be {value['value']}"
        case ConstraintType.BUDGET_TOTAL:
            text = (
                "the total, including tax and delivery, must not exceed "
                f"{paise_to_rupees(value)}"
            )
        case ConstraintType.PRICE_ITEM:
            text = f"each item must cost at most {paise_to_rupees(value)}"
        case ConstraintType.QUANTITY:
            text = f"quantity must be {value}"
        case ConstraintType.BRAND_IN:
            text = f"brand must be {' or '.join(value)}"
        case ConstraintType.BRAND_OUT:
            text = f"brand must not be {' or '.join(value)}"
        case ConstraintType.MERCHANT:
            text = f"it must be bought from {', '.join(v.removeprefix('m_') for v in value)}"
        case ConstraintType.DELIVERY_BY:
            text = f"it must arrive by {date.fromisoformat(str(value)).strftime('%d %b %Y')}"
        case ConstraintType.RECURRENCE:
            text = (
                "this is a one-off purchase, not a subscription"
                if value == Recurrence.ONE_OFF.value
                else "this is a recurring subscription"
            )
        case ConstraintType.SUBSTITUTION:
            text = "substitutes are not allowed"
        case ConstraintType.DIETARY:
            text = f"items must be {value}"
        case ConstraintType.PACK_SIZE:
            text = f"pack size must be {value}"
        case ConstraintType.REFUNDABLE:
            text = "the item must be returnable"
        case _:
            text = f"{c.type}: {value}"

    if c.source_span is None:
        text += " (assumed — you did not say this)"
    if not c.hard:
        text += " (preference, will not block the purchase)"
    return text


def render_read_back(mandate: IntentMandate) -> str:
    """What the user is asked to confirm before signing.

    Written in the second person and in plain English, because a mandate the user did not
    actually understand is not consent — and at dispute time this text is the record of
    what they agreed to.
    """
    lines = [
        "You are authorising an AI agent to buy on your behalf.",
        f'Your instruction: "{mandate.natural_language}"',
        "",
        "The agent may complete the purchase only if all of these hold:",
    ]
    hard = mandate.hard_constraints()
    soft = mandate.soft_constraints()

    lines.extend(f"  {i}. {describe(c)}" for i, c in enumerate(hard, start=1))

    if soft:
        lines.append("")
        lines.append("Preferences — these will not block the purchase:")
        lines.extend(f"  - {describe(c)}" for c in soft)

    lines.extend(
        [
            "",
            f"Spending limit: {paise_to_rupees(mandate.budget_total_paise)} in total, "
            f"including tax and delivery.",
            f"This authorisation expires on {mandate.expires_at.strftime('%d %b %Y')} "
            f"and may be used {mandate.max_uses} time(s).",
        ]
    )
    if mandate.merchants_allow:
        allowed = ", ".join(m.removeprefix("m_") for m in mandate.merchants_allow)
        lines.append(f"Allowed merchants: {allowed}.")
    return "\n".join(lines)


# --- signing ---------------------------------------------------------------------


class MandateSigner:
    """ES256 JWS over the mandate, mirroring how AP2 mandates are carried.

    Separate key from the ledger's Ed25519 key on purpose: this one attests *the user
    authorised this*, the ledger's attests *this service recorded that*. In production
    they belong to different parties.
    """

    def __init__(self, private_key_pem: bytes) -> None:
        from joserfc.jwk import ECKey

        self._key = ECKey.import_key(private_key_pem)

    @classmethod
    def from_settings(cls) -> MandateSigner:
        path = settings.mandate_key_path
        if path.exists():
            return cls(path.read_bytes())

        from joserfc.jwk import ECKey

        key = ECKey.generate_key("P-256")
        pem = key.as_pem(private=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(pem)
        path.chmod(0o600)
        return cls(pem)

    @classmethod
    def generate(cls) -> MandateSigner:
        from joserfc.jwk import ECKey

        return cls(ECKey.generate_key("P-256").as_pem(private=True))

    @property
    def public_jwk(self) -> dict[str, Any]:
        return dict(self._key.as_dict(private=False))

    def sign(self, mandate: IntentMandate) -> str:
        """Compact JWS over the mandate's canonical payload, excluding the signature."""
        from joserfc import jws

        from app.canonical import canonical_json

        payload = canonical_json(mandate.signing_payload())
        return jws.serialize_compact({"alg": "ES256", "typ": "JWS"}, payload, self._key)

    def verify(self, mandate: IntentMandate) -> bool:
        from joserfc import jws

        from app.canonical import canonical_json

        if not mandate.signature_jws:
            return False
        try:
            decoded = jws.deserialize_compact(mandate.signature_jws, self._key)
        except Exception:  # any failure to decode is a failed verification
            return False
        return decoded.payload == canonical_json(mandate.signing_payload())


_signer: MandateSigner | None = None


def get_mandate_signer() -> MandateSigner:
    global _signer
    if _signer is None:
        _signer = MandateSigner.from_settings()
    return _signer


def set_mandate_signer(signer: MandateSigner | None) -> None:
    global _signer
    _signer = signer
