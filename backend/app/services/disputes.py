"""Dispute module — a claim plus the ledger becomes an evidence pack.

This is the part a bank's dispute-ops analyst actually uses. The claim arrives in the
cardholder's words; everything that answers it is already on the ledger, written before
anyone knew there would be a dispute. That ordering is the whole value: the evidence was
not assembled after the fact by the party it favours.

The allocation is **a proposal**. Current card-scheme rules decide who pays. What ANUMATI
adds is the thing no scheme code carries today — whether the agent stayed inside its
mandate — expressed in the codes the existing flow already understands.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from app.config import REPO_ROOT
from app.models.cart import Cart
from app.models.common import (
    Allocation,
    ConstraintStatus,
    ConstraintType,
    Decision,
    LedgerKind,
    StepUpAnswer,
)
from app.models.dispute import DisputeCase
from app.models.ledger import LedgerEntry, VerifyResult
from app.models.mandate import IntentMandate
from app.models.verdict import StepUp, Verdict
from app.services.ledger import LedgerService

REASON_CODES_PATH = REPO_ROOT / "data" / "reason_codes.yaml"


@lru_cache
def reason_codes(path: Path | None = None) -> dict[str, Any]:
    return yaml.safe_load((path or REASON_CODES_PATH).read_text(encoding="utf-8")) or {}


# Phrases that point at fulfilment rather than conformance. Deliberately small and
# readable: an analyst has to be able to see why a claim was routed where it was.
NOT_DELIVERED_PATTERNS = (
    r"\bnever (arrived|came|delivered|received)\b",
    r"\b(not|hasn'?t|haven'?t|didn'?t) (arrived|come|delivered|been delivered|received)\b",
    r"\bno (delivery|parcel|package)\b",
    r"\bstill waiting\b",
    r"\bnahi (aaya|mila|pahuncha)\b",
)

SUBSCRIPTION_PATTERNS = (
    r"\bsubscription\b", r"\brecurring\b", r"\bevery month\b", r"\bmonthly\b",
    r"\bcharged again\b", r"\bhar mahine\b",
)


@dataclass(frozen=True)
class LedgerFacts:
    """Everything the ledger holds about one cart, gathered in one pass."""

    cart: Cart | None
    mandate: IntentMandate | None
    verdicts: list[Verdict]
    step_ups: list[StepUp]
    paid: bool
    entries: list[LedgerEntry]
    chain: VerifyResult

    @property
    def final_verdict(self) -> Verdict | None:
        return self.verdicts[-1] if self.verdicts else None

    @property
    def approved_step_up(self) -> StepUp | None:
        for step_up in self.step_ups:
            if step_up.user_answer is StepUpAnswer.APPROVE:
                return step_up
        return None

    def seqs(self) -> list[int]:
        return [e.seq for e in self.entries if e.seq is not None]


class EvidenceItem(BaseModel):
    """One line of the pack, each pinned to the ledger entry that proves it."""

    ledger_seq: int | None = None
    entry_hash: str | None = None
    kind: str
    headline: str
    detail: str = ""


class EvidencePack(BaseModel):
    dispute: DisputeCase
    claim_text: str
    scenario: str
    scenario_explanation: str
    allocation: Allocation
    visa_code: str
    visa_label: str
    mc_code: str
    mc_label: str
    decision_on_record: Decision | None = None
    chain_ok: bool
    chain_broken_at: int | None = None
    items: list[EvidenceItem] = Field(default_factory=list)
    mandate_read_back: str = ""
    caveat: str = (
        "This allocation is ANUMATI's proposal. Card-scheme rules govern who finally "
        "bears the loss. UPI agent-payment dispute categories are not published yet; "
        "map to the bank's existing UPI complaint flow until NPCI publishes UAP rules."
    )


# --- gathering ------------------------------------------------------------------


def gather(session: Session, cart_id: str) -> LedgerFacts:
    """Read the ledger, not the database tables. The chain is what the bank can check."""
    ledger = LedgerService(session)
    entries = list(session.exec(select(LedgerEntry).order_by(LedgerEntry.seq)).all())

    cart: Cart | None = None
    mandate: IntentMandate | None = None
    verdicts: list[Verdict] = []
    step_ups: list[StepUp] = []
    paid = False
    relevant: list[LedgerEntry] = []

    verdict_ids: set[str] = set()

    for entry in entries:
        payload = entry.payload
        kind = entry.kind

        if kind == LedgerKind.CART_CREATED and payload.get("cart_id") == cart_id:
            cart = Cart.model_validate(payload)
            relevant.append(entry)
        elif kind == LedgerKind.VERDICT and payload.get("cart_id") == cart_id:
            verdict = Verdict.model_validate(payload)
            verdicts.append(verdict)
            verdict_ids.add(verdict.verdict_id)
            relevant.append(entry)
        elif kind == LedgerKind.PAYMENT and payload.get("cart_id") == cart_id:
            paid = True
            relevant.append(entry)

    mandate_id = cart.mandate_id if cart else None
    for entry in entries:
        payload = entry.payload
        if entry.kind == LedgerKind.MANDATE_SIGNED and payload.get("mandate_id") == mandate_id:
            mandate = IntentMandate.model_validate(payload)
            relevant.insert(0, entry)
        elif entry.kind == LedgerKind.STEP_UP and payload.get("verdict_id") in verdict_ids:
            step_ups.append(StepUp.model_validate(payload))
            relevant.append(entry)

    relevant.sort(key=lambda e: e.seq or 0)
    return LedgerFacts(
        cart=cart, mandate=mandate, verdicts=verdicts, step_ups=step_ups,
        paid=paid, entries=relevant, chain=ledger.verify(),
    )


# --- classification ---------------------------------------------------------------


def _matches(text: str, patterns: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(re.search(p, lowered) for p in patterns)


def classify(claim_text: str, facts: LedgerFacts) -> tuple[str, str]:
    """Route the claim to a scenario, and say why in a sentence an analyst can quote.

    The ledger outranks the wording. A claim that says "I never ordered this" against a
    cart with a VIOLATES verdict on record is an agent that overstepped, whatever words
    the cardholder reached for.
    """
    if not facts.chain.ok:
        return "mandate_forged", (
            f"the decision chain fails verification at entry {facts.chain.broken_at}, "
            "so these records cannot be relied on"
        )

    if facts.mandate is None or facts.cart is None:
        return "agent_exceeded_mandate", (
            "no signed mandate or cart is on record for this purchase, so there is no "
            "evidence the agent was authorised at all"
        )

    verdict = facts.final_verdict
    if verdict is None:
        return "agent_exceeded_mandate", (
            "no conformance check was performed before this purchase"
        )

    if verdict.decision is Decision.VIOLATES:
        recurrence = _violated_types(verdict, facts.mandate)
        if ConstraintType.RECURRENCE in recurrence:
            return "agent_recurrence_error", (
                "the agent set up a recurring charge against a one-off authorisation, "
                "and the check recorded that before payment"
            )
        return "agent_exceeded_mandate", (
            "the cart was checked and found to breach the signed mandate, yet the "
            "purchase went through"
        )

    if _matches(claim_text, NOT_DELIVERED_PATTERNS):
        return "not_delivered", (
            "the cart matched the mandate; the complaint is about fulfilment, which is "
            "outside conformance"
        )

    if _matches(claim_text, SUBSCRIPTION_PATTERNS) and _has_subscription(facts.cart):
        return "agent_recurrence_error", (
            "the cart contains a recurring plan and the cardholder disputes the "
            "recurrence"
        )

    if facts.approved_step_up is not None:
        return "user_approved_stepup", (
            "the engine was unsure, asked, and the cardholder approved it in the app"
        )

    return "conformed_user_regret", (
        "the cart matched the signed mandate on every constraint, and the check was "
        "recorded before payment"
    )


def _violated_types(verdict: Verdict, mandate: IntentMandate) -> set[ConstraintType]:
    by_id = {c.id: c for c in mandate.constraints}
    return {
        by_id[r.constraint_id].type
        for r in verdict.results
        if r.status is ConstraintStatus.VIOLATED and r.constraint_id in by_id
    }


def _has_subscription(cart: Cart) -> bool:
    return any(item.is_subscription for item in cart.items)


# --- pack building -------------------------------------------------------------------


def _money(paise: int) -> str:
    rupees, remainder = divmod(int(paise), 100)
    return f"₹{rupees:,}" if remainder == 0 else f"₹{rupees:,}.{remainder:02d}"


def build_pack(
    session: Session, cart_id: str, claim_text: str, claim_category: str | None = None
) -> EvidencePack:
    facts = gather(session, cart_id)
    scenario, explanation = classify(claim_text, facts)

    codes = reason_codes()
    mapping = codes.get("scenarios", {}).get(scenario, {})
    visa_code = mapping.get("visa", "")
    mc_code = mapping.get("mc", "")

    items = _evidence_items(facts)

    dispute = DisputeCase(
        cart_id=cart_id,
        claim_text=claim_text,
        claim_category=claim_category or scenario,
        evidence_refs=facts.seqs(),
        proposed_allocation=Allocation(mapping.get("allocation", "user")),
        visa_code=visa_code,
        mc_code=mc_code,
    )

    from app.services.mandate_builder import render_read_back

    return EvidencePack(
        dispute=dispute,
        claim_text=claim_text,
        scenario=scenario,
        scenario_explanation=explanation,
        allocation=dispute.proposed_allocation or Allocation.USER,
        visa_code=visa_code,
        visa_label=codes.get("visa", {}).get(visa_code, ""),
        mc_code=mc_code,
        mc_label=codes.get("mastercard", {}).get(mc_code, ""),
        decision_on_record=facts.final_verdict.decision if facts.final_verdict else None,
        chain_ok=facts.chain.ok,
        chain_broken_at=facts.chain.broken_at,
        items=items,
        mandate_read_back=render_read_back(facts.mandate) if facts.mandate else "",
    )


def _evidence_items(facts: LedgerFacts) -> list[EvidenceItem]:
    items: list[EvidenceItem] = []

    for entry in facts.entries:
        payload = entry.payload
        kind = entry.kind
        headline, detail = "", ""

        if kind == LedgerKind.MANDATE_SIGNED:
            headline = "The user signed an instruction"
            detail = f'"{payload.get("natural_language", "")}"'
        elif kind == LedgerKind.CART_CREATED:
            lines = ", ".join(
                f"{i['qty']} x {i['title']}" for i in payload.get("items", [])
            )
            headline = "The agent built a cart"
            detail = f"{lines} — total {_money(payload.get('total_paise', 0))}"
        elif kind == LedgerKind.VERDICT:
            decision = payload.get("decision")
            headline = f"ANUMATI checked the cart: {decision}"
            reasons = [
                r.get("rationale", "")
                for r in payload.get("results", [])
                if r.get("status") in ("violated", "unclear") and r.get("rationale")
            ]
            detail = "; ".join(reasons[:4])
        elif kind == LedgerKind.STEP_UP:
            headline = f"The user was asked and answered {payload.get('user_answer')}"
            detail = payload.get("question_text", "")
        elif kind == LedgerKind.PAYMENT:
            headline = "Payment was made"
            detail = (
                f"{_money(payload.get('amount_paise', 0))} to "
                f"{payload.get('merchant_id', '')}"
            )

        if headline:
            items.append(
                EvidenceItem(
                    ledger_seq=entry.seq, entry_hash=entry.entry_hash,
                    kind=str(kind), headline=headline, detail=detail,
                )
            )

    chain = facts.chain
    items.append(
        EvidenceItem(
            kind="chain_verification",
            headline=(
                "The decision chain verifies"
                if chain.ok
                else f"The decision chain FAILS at entry {chain.broken_at}"
            ),
            detail=(
                f"{chain.entries_checked} entries checked against public key "
                f"{(chain.public_key_hex or '')[:16]}…"
                + (f" — {chain.reason}" if chain.reason else "")
            ),
        )
    )
    return items
