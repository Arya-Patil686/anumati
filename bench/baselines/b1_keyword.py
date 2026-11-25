"""B1 — B0 plus a keyword match between the instruction and the product title.

The naive text check: if the words the user typed appear in what the agent bought, call
it a match. Cheap, and it catches a treadmill sold as a treadmill. It has no idea that
"off-white" is nearly "white", or that a subscription is not a one-off purchase.
"""

from __future__ import annotations

import re

from app.models.cart import Cart
from app.models.common import Decision
from app.models.mandate import IntentMandate

from bench.baselines import timed
from bench.baselines.b0_numeric import B0Numeric

STOPWORDS = frozenset(
    {
        "the", "a", "an", "under", "below", "and", "or", "for", "with", "from", "by",
        "to", "of", "in", "on", "me", "my", "i", "want", "need", "please", "buy", "get",
        "se", "kam", "ke", "ki", "ka", "andar", "chahiye", "tak", "mein", "sirf", "hai",
        "each", "only", "one", "off", "no", "not", "delivered", "delivery",
    }
)
MATCH_THRESHOLD = 0.5


def content_words(text: str) -> set[str]:
    words = re.findall(r"[a-z]+", text.lower())
    return {w for w in words if w not in STOPWORDS and len(w) > 2}


class B1Keyword:
    name = "B1 keyword"
    description = "B0 plus instruction words matched against the product title"

    def __init__(self) -> None:
        self._numeric = B0Numeric()

    @timed
    def predict(  # type: ignore[override]
        self, mandate: IntentMandate, cart: Cart
    ) -> tuple[Decision, float, bool, str]:
        numeric = self._numeric.predict(mandate, cart)
        if numeric.decision is Decision.VIOLATES:
            return Decision.VIOLATES, numeric.confidence, False, numeric.note

        wanted = content_words(mandate.natural_language)
        if not wanted:
            return Decision.CONFORMS, 0.5, False, "no content words to match"

        haystack = " ".join(f"{i.title} {i.brand} {i.category_path}" for i in cart.items)
        found = content_words(haystack)
        overlap = len(wanted & found) / len(wanted)

        if overlap >= MATCH_THRESHOLD:
            return Decision.CONFORMS, min(0.99, 0.5 + overlap / 2), False, (
                f"{overlap:.0%} of instruction words appear in the item"
            )
        return Decision.VIOLATES, min(0.99, 1 - overlap), False, (
            f"only {overlap:.0%} of instruction words appear in the item"
        )
