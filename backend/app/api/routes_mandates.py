"""POST /mandates and POST /mandates/{id}/sign.

Two steps on purpose. Building a mandate is free and reversible; signing it is the
moment the user grants authority, and it is the first thing written to the ledger.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.db import get_session
from app.models.common import LedgerKind
from app.models.mandate import IntentMandate
from app.models.store import MandateRow
from app.services.ledger import LedgerService
from app.services.mandate_builder import (
    MandateBuilder,
    MandateBuildError,
    get_mandate_signer,
    render_read_back,
)

router = APIRouter(prefix="/mandates", tags=["mandates"])

SessionDep = Annotated[Session, Depends(get_session)]


class CreateMandateRequest(BaseModel):
    instruction: str = Field(min_length=1, max_length=2000)
    user_id: str = Field(default="u_demo", max_length=64)
    language: str | None = None


class MandateResponse(BaseModel):
    mandate: IntentMandate
    read_back: str
    unparsed_tail: str = ""
    signed: bool = False
    ledger_seq: int | None = None


def _builder() -> MandateBuilder:
    return MandateBuilder()


def _load(session: Session, mandate_id: str) -> MandateRow:
    row = session.get(MandateRow, mandate_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no mandate {mandate_id}")
    return row


@router.post("", response_model=MandateResponse, status_code=201)
def create_mandate(
    body: CreateMandateRequest,
    session: SessionDep,
    builder: Annotated[MandateBuilder, Depends(_builder)],
) -> MandateResponse:
    """Parse an instruction into a draft mandate. Nothing is authorised yet."""
    try:
        built = builder.build(body.instruction, body.user_id, language=body.language)
    except MandateBuildError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    session.add(MandateRow.from_domain(built.mandate))
    session.commit()

    return MandateResponse(
        mandate=built.mandate,
        read_back=built.read_back,
        unparsed_tail=built.unparsed_tail,
        signed=False,
    )


@router.post("/{mandate_id}/sign", response_model=MandateResponse)
def sign_mandate(mandate_id: str, session: SessionDep) -> MandateResponse:
    """Sign the mandate (demo key) and write the first ledger entry for it."""
    row = _load(session, mandate_id)
    if row.signed:
        raise HTTPException(status_code=409, detail="mandate is already signed")

    mandate = row.to_domain()
    signed = mandate.model_copy(update={"signature_jws": get_mandate_signer().sign(mandate)})

    entry = LedgerService(session).append(LedgerKind.MANDATE_SIGNED, signed)

    row.doc = signed.model_dump(mode="json")
    row.signed = True
    session.add(row)
    session.commit()

    return MandateResponse(
        mandate=signed,
        read_back=render_read_back(signed),
        signed=True,
        ledger_seq=entry.seq,
    )


@router.get("/{mandate_id}", response_model=MandateResponse)
def get_mandate(mandate_id: str, session: SessionDep) -> MandateResponse:
    row = _load(session, mandate_id)
    mandate = row.to_domain()
    return MandateResponse(
        mandate=mandate,
        read_back=render_read_back(mandate),
        signed=row.signed,
    )
