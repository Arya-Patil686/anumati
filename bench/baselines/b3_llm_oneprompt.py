"""B3 — the obvious LLM approach: one prompt, whole cart, yes or no.

"Does this cart satisfy the instruction?" It is what most teams would build, and it is a
fair comparison because it is genuinely reasonable. Its weaknesses are structural rather
than a matter of prompt wording:

* it answers the whole cart at once, so it cannot say *which* constraint failed, and a
  verdict that cannot name a reason is not dispute evidence;
* the product text is in the prompt, so an injected instruction has a path to the answer;
* it is yes/no, so it has nowhere to put "a reasonable shopper would hesitate here".

It needs a model. In replay mode with nothing recorded it reports itself as unavailable,
and `run_eval.py` leaves its row blank rather than inventing numbers (CLAUDE.md rule 8).
"""

from __future__ import annotations

from app.models.cart import Cart
from app.models.common import Decision
from app.models.mandate import IntentMandate
from app.services.llm.cache import CacheMiss
from app.services.llm.client import LLMClient, LLMError, LLMRequest, get_llm_client

from bench.baselines import timed

SYSTEM = (
    "You check whether a shopping cart satisfies a user's purchase instruction. "
    "Answer only with JSON."
)
SCHEMA_HINT = '{"satisfies": true, "confidence": 0.0}'


class B3LLMOnePrompt:
    name = "B3 LLM one-prompt"
    description = "Single prompt over the whole cart, yes/no"

    def __init__(self, client: LLMClient | None = None) -> None:
        self._client = client
        self.unavailable_reason: str | None = None
        self.calls = 0

    @property
    def client(self) -> LLMClient:
        if self._client is None:
            self._client = get_llm_client()
        return self._client

    def build_request(self, mandate: IntentMandate, cart: Cart) -> LLMRequest:
        items = [
            {
                "sku": i.sku, "title": i.title, "description": i.description,
                "brand": i.brand, "category_path": i.category_path,
                "attributes": i.attributes, "unit_price_paise": i.unit_price_paise,
                "qty": i.qty, "is_subscription": i.is_subscription,
            }
            for i in cart.items
        ]
        return LLMRequest(
            prompt_name="b3_one_prompt",
            system=SYSTEM,
            instruction=(
                f'The user asked: "{mandate.natural_language}"\n'
                f"The cart total, including tax and delivery, is "
                f"{cart.computed_total_paise} paise.\n"
                f"Does this cart satisfy the instruction?"
            ),
            data={"cart_items": items},
            schema_hint=SCHEMA_HINT,
            temperature=0.0,
        )

    @timed
    def predict(  # type: ignore[override]
        self, mandate: IntentMandate, cart: Cart
    ) -> tuple[Decision, float, bool, str]:
        try:
            self.calls += 1
            response = self.client.complete_json(self.build_request(mandate, cart))
        except (CacheMiss, LLMError) as exc:
            self.unavailable_reason = str(exc)[:160]
            # Not a prediction. run_eval drops the system rather than scoring this.
            return Decision.AMBIGUOUS, 0.0, True, "unavailable"

        satisfies = bool(response.content.get("satisfies"))
        try:
            confidence = float(response.content.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5

        return (
            Decision.CONFORMS if satisfies else Decision.VIOLATES,
            max(0.0, min(1.0, confidence)),
            True,
            "",
        )

    @property
    def available(self) -> bool:
        return self.unavailable_reason is None
