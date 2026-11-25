"""POST /agent/run and POST /stepup/{verdict_id}.

`/agent/run` is the whole loop in one call: instruction -> signed mandate -> agent builds
a cart -> ANUMATI checks it -> the agent acts. It is what the demo drives and what the
benchmark's natural-error subset is collected from.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.api.routes_check import run_check
from app.db import get_session
from app.models.cart import Cart
from app.models.common import LedgerKind, StepUpAnswer, utc_now
from app.models.mandate import IntentMandate
from app.models.store import CartRow, MandateRow, StepUpRow, VerdictRow
from app.models.verdict import StepUp
from app.services.agent.faults import FaultMode
from app.services.agent.shopping_agent import AgentMode, AgentRun, ShoppingAgent
from app.services.engine.aggregator import get_engine
from app.services.ledger import LedgerService
from app.services.mandate_builder import MandateBuilder, MandateBuildError, get_mandate_signer

router = APIRouter(tags=["agent"])

SessionDep = Annotated[Session, Depends(get_session)]


class AgentRunRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)
    user_id: str = "u_demo"
    mode: AgentMode = AgentMode.NORMAL
    fault: FaultMode | None = None
    seed: int = 7


class AgentRunResponse(BaseModel):
    run: AgentRun
    mandate: IntentMandate
    read_back: str
    mandate_ledger_seq: int | None = None


class StepUpRequest(BaseModel):
    answer: StepUpAnswer


class StepUpResponse(BaseModel):
    step_up: StepUp
    ledger_seq: int
    payment_ledger_seq: int | None = None


@router.post("/agent/run", response_model=AgentRunResponse)
def agent_run(body: AgentRunRequest, session: SessionDep) -> AgentRunResponse:
    """Run the full loop. The mandate is built and signed here, as a demo user would."""
    try:
        built = MandateBuilder().build(body.instruction, body.user_id)
    except MandateBuildError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    mandate = built.mandate.model_copy(
        update={"signature_jws": get_mandate_signer().sign(built.mandate)}
    )

    ledger = LedgerService(session)
    mandate_entry = ledger.append(LedgerKind.MANDATE_SIGNED, mandate)

    row = MandateRow.from_domain(mandate)
    row.signed = True
    session.add(row)
    session.commit()

    agent = ShoppingAgent(mode=body.mode, fault=body.fault, seed=body.seed)

    def checker(cart: Cart) -> tuple[object, list[int]]:
        mandate_row = session.get(MandateRow, mandate.mandate_id)
        assert mandate_row is not None
        return run_check(session, mandate_row, cart, get_engine())

    try:
        run = agent.run(mandate, checker)  # type: ignore[arg-type]
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    if run.paid:
        _record_payment(session, run.final.cart)

    return AgentRunResponse(
        run=run,
        mandate=mandate,
        read_back=built.read_back,
        mandate_ledger_seq=mandate_entry.seq,
    )


def _record_payment(session: Session, cart: Cart) -> int | None:
    entry = LedgerService(session).append(
        LedgerKind.PAYMENT,
        {
            "cart_id": cart.cart_id,
            "mandate_id": cart.mandate_id,
            "amount_paise": cart.computed_total_paise,
            "merchant_id": cart.merchant_id,
            "paid_at": utc_now().isoformat(),
        },
    )
    return entry.seq


@router.post("/stepup/{verdict_id}", response_model=StepUpResponse)
def answer_step_up(
    verdict_id: str, body: StepUpRequest, session: SessionDep
) -> StepUpResponse:
    """The one tap. Whatever the user says is logged — that log is the defence later."""
    row = session.get(StepUpRow, verdict_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no step-up for verdict {verdict_id}")
    if row.user_answer is not None:
        raise HTTPException(status_code=409, detail="this step-up has already been answered")

    answered = row.to_domain().answer(body.answer)
    entry = LedgerService(session).append(LedgerKind.STEP_UP, answered)

    row.user_answer = str(answered.user_answer)
    row.answered_at = answered.answered_at
    row.doc = answered.model_dump(mode="json")
    session.add(row)
    session.commit()

    payment_seq: int | None = None
    if body.answer is StepUpAnswer.APPROVE:
        verdict_row = session.get(VerdictRow, verdict_id)
        if verdict_row is not None:
            cart_row = session.get(CartRow, verdict_row.cart_id)
            if cart_row is not None:
                payment_seq = _record_payment(session, cart_row.to_domain())

    assert entry.seq is not None
    return StepUpResponse(
        step_up=answered, ledger_seq=entry.seq, payment_ledger_seq=payment_seq
    )
