"""Baselines B0-B3 — what the alternatives to ANUMATI actually do.

Every system answers the same question through the same interface, so the comparison is
like for like: given a mandate and a cart, return a decision, a confidence and how long
it took.

Only ANUMATI can return AMBIGUOUS. That is the point of the comparison — the others must
guess one way or the other on a case a reasonable shopper would hesitate over.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol

from app.models.cart import Cart
from app.models.common import Decision
from app.models.mandate import IntentMandate


@dataclass(frozen=True)
class Prediction:
    decision: Decision
    confidence: float
    latency_ms: float
    used_llm: bool = False
    note: str = ""


class System(Protocol):
    """What every baseline and ANUMATI itself implement."""

    name: str
    description: str

    def predict(self, mandate: IntentMandate, cart: Cart) -> Prediction: ...


def timed(fn):
    """Wrap a decision function so every system is timed the same way."""

    def wrapper(self, mandate: IntentMandate, cart: Cart) -> Prediction:
        started = time.perf_counter()
        decision, confidence, used_llm, note = fn(self, mandate, cart)
        elapsed = (time.perf_counter() - started) * 1000
        return Prediction(
            decision=decision, confidence=confidence, latency_ms=elapsed,
            used_llm=used_llm, note=note,
        )

    return wrapper
