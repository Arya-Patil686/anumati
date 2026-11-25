"""The shopping agent — a tool-calling loop that builds a cart and tries to pay.

The planner is rule-based rather than a model, for the same reason the Mandate Builder
is: the benchmark has to be able to say what the agent did and why, and a model here
would make the natural-error subset unreproducible. The tool surface is shaped exactly
like a model's would be, so swapping the planner later touches only `_choose`.

What matters is the control flow, not the cleverness:

    search -> choose -> add to cart -> checkout -> ANUMATI decides

The agent never decides whether it may pay. It submits the cart, receives a verdict and
acts on it: pay, retry once with the reasons it was given, or ask the user.
"""

from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.models.cart import Cart
from app.models.common import ConstraintType, Decision
from app.models.mandate import IntentMandate
from app.models.verdict import StepUp, Verdict
from app.services.agent.faults import (
    CART_FAULTS,
    SELECTION_FAULTS,
    FaultMode,
    FaultOutcome,
    apply_cart_fault,
    apply_selection_fault,
)
from app.services.agent.tools import ShoppingTools, ToolLog
from app.services.catalogue import CatalogueService, Product, get_catalogue
from app.services.engine.aggregator import CheckOutcome

AGENT_ID = "agent_anumati_demo"
MAX_ATTEMPTS = 2


class AgentMode(StrEnum):
    NORMAL = "normal"
    FAULTY = "faulty"


class AttemptRecord(BaseModel):
    attempt: int
    cart: Cart
    verdict: Verdict
    step_up: StepUp | None = None
    fault: str | None = None
    fault_note: str = ""
    action: str
    ledger_seqs: list[int] = Field(default_factory=list)


class AgentRun(BaseModel):
    """Everything the console and the demo script need to show one agent run."""

    mandate_id: str
    mode: AgentMode
    attempts: list[AttemptRecord] = Field(default_factory=list)
    final_decision: Decision
    paid: bool = False
    awaiting_user: bool = False
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    narrative: list[str] = Field(default_factory=list)

    @property
    def final(self) -> AttemptRecord:
        return self.attempts[-1]


@dataclass
class _Plan:
    """What the agent decided to look for, read off the mandate."""

    query: str
    category_path: str | None = None
    max_price_paise: int | None = None
    quantity: int = 1
    wanted_attributes: dict[str, str] = field(default_factory=dict)
    wanted_brands: set[str] = field(default_factory=set)
    merchant: str | None = None
    wants_subscription: bool = False
    wants_veg: bool = False


def _plan_from(mandate: IntentMandate) -> _Plan:
    plan = _Plan(query=mandate.natural_language)

    for c in mandate.constraints:
        match c.type:
            case ConstraintType.CATEGORY:
                plan.category_path = str(c.value)
            case ConstraintType.BUDGET_TOTAL | ConstraintType.PRICE_ITEM:
                value = int(c.value)
                plan.max_price_paise = min(plan.max_price_paise or value, value)
            case ConstraintType.QUANTITY:
                plan.quantity = int(c.value)
            case ConstraintType.ATTRIBUTE:
                plan.wanted_attributes[str(c.value["name"])] = str(c.value["value"]).lower()
            case ConstraintType.BRAND_IN:
                plan.wanted_brands |= {str(b).lower() for b in c.value}
            case ConstraintType.MERCHANT:
                plan.merchant = str(c.value[0]) if c.value else None
            case ConstraintType.RECURRENCE:
                plan.wants_subscription = str(c.value) == "recurring"
            case ConstraintType.DIETARY:
                plan.wants_veg = str(c.value) == "veg"
            case _:
                continue

    if plan.category_path:
        plan.query = plan.category_path.split(">")[-1].strip()
    return plan


def _score(product: Product, plan: _Plan) -> int:
    """How well a candidate matches the plan. Higher is better."""
    score = 0
    if plan.category_path and product.category_path == plan.category_path:
        score += 10
    for name, wanted in plan.wanted_attributes.items():
        if str(product.attributes.get(name, "")).lower() == wanted:
            score += 5
    if plan.wanted_brands and product.brand.lower() in plan.wanted_brands:
        score += 4
    if plan.merchant and product.merchant_id == plan.merchant:
        score += 3
    if product.is_subscription == plan.wants_subscription:
        score += 3
    if plan.wants_veg and product.attributes.get("veg") is True:
        score += 2
    if plan.max_price_paise and product.price_paise <= plan.max_price_paise:
        score += 1
    return score


class ShoppingAgent:
    def __init__(
        self,
        catalogue: CatalogueService | None = None,
        *,
        mode: AgentMode = AgentMode.NORMAL,
        fault: FaultMode | None = None,
        seed: int = 7,
    ) -> None:
        self.catalogue = catalogue or get_catalogue()
        self.mode = mode
        self.fault = fault
        self.rng = random.Random(seed)

    # --- choosing ------------------------------------------------------------

    def _choose(
        self, tools: ShoppingTools, plan: _Plan
    ) -> tuple[Product | None, list[Product]]:
        candidates = tools.search_catalogue(
            plan.query,
            category_path=plan.category_path,
            max_price_paise=plan.max_price_paise,
            limit=25,
        )
        if not candidates:
            candidates = tools.search_catalogue(plan.query, limit=25)
        if not candidates:
            return None, []

        best = max(candidates, key=lambda p: (_score(p, plan), -p.price_paise))
        tools.get_product(best.sku)
        return best, candidates

    def _active_fault(self) -> FaultMode | None:
        if self.mode is not AgentMode.FAULTY:
            return None
        return self.fault or self.rng.choice(list(FaultMode))

    # --- running -------------------------------------------------------------

    def run(
        self,
        mandate: IntentMandate,
        checker: Callable[[Cart], tuple[CheckOutcome, list[int]]],
    ) -> AgentRun:
        plan = _plan_from(mandate)
        log = ToolLog()
        narrative: list[str] = []
        attempts: list[AttemptRecord] = []

        fault = self._active_fault()

        for attempt in range(1, MAX_ATTEMPTS + 1):
            tools = ShoppingTools(self.catalogue, log=log)
            chosen, _candidates = self._choose(tools, plan)
            if chosen is None:
                raise ValueError("the catalogue returned nothing for this instruction")

            outcome_note = FaultOutcome(applied=False)
            if fault in SELECTION_FAULTS:
                chosen, outcome_note = apply_selection_fault(
                    fault, mandate, chosen, self.catalogue, self.rng
                )

            tools.add_to_cart(chosen.sku, qty=plan.quantity)
            cart = tools.build_cart(
                mandate.mandate_id,
                merchant_id=chosen.merchant_id,
                delivery_days=chosen.delivery_days,
                agent_id=AGENT_ID,
            )

            if fault in CART_FAULTS:
                cart, outcome_note = apply_cart_fault(fault, cart, mandate, self.rng)

            tools.checkout(cart)
            check, seqs = checker(cart)
            verdict = check.verdict

            action, keep_going = self._react(verdict.decision, attempt)
            narrative.append(
                self._narrate(attempt, chosen, cart, verdict, outcome_note, action)
            )

            attempts.append(
                AttemptRecord(
                    attempt=attempt,
                    cart=cart,
                    verdict=verdict,
                    step_up=check.step_up,
                    fault=str(outcome_note.mode) if outcome_note.applied else None,
                    fault_note=outcome_note.note if outcome_note.applied else "",
                    action=action,
                    ledger_seqs=seqs,
                )
            )

            if not keep_going:
                break

            # The retry drops the fault and takes the reasons it was given seriously.
            fault = None

        final = attempts[-1]
        return AgentRun(
            mandate_id=mandate.mandate_id,
            mode=self.mode,
            attempts=attempts,
            final_decision=final.verdict.decision,
            paid=final.verdict.decision is Decision.CONFORMS,
            awaiting_user=final.verdict.decision is Decision.AMBIGUOUS,
            tool_calls=log.as_dicts(),
            narrative=narrative,
        )

    @staticmethod
    def _react(decision: Decision, attempt: int) -> tuple[str, bool]:
        """What the agent does with the verdict it was handed."""
        if decision is Decision.CONFORMS:
            return "paid", False
        if decision is Decision.AMBIGUOUS:
            return "asked the user to confirm", False
        if attempt < MAX_ATTEMPTS:
            return "blocked, retrying with the reasons given", True
        return "blocked, gave up", False

    @staticmethod
    def _narrate(
        attempt: int,
        chosen: Product,
        cart: Cart,
        verdict: Verdict,
        fault: FaultOutcome,
        action: str,
    ) -> str:
        rupees = cart.computed_total_paise / 100
        line = (
            f"attempt {attempt}: chose {chosen.title} ({chosen.sku}), "
            f"total ₹{rupees:,.2f} -> {verdict.decision} -> {action}"
        )
        if fault.applied:
            line += f" [fault: {fault.mode} - {fault.note}]"
        return line
