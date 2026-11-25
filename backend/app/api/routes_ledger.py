"""GET /ledger and GET /ledger/verify."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.db import get_session
from app.models.ledger import LedgerEntryRead, VerifyResult
from app.services.ledger import LedgerService

router = APIRouter(prefix="/ledger", tags=["ledger"])


def _service(session: Annotated[Session, Depends(get_session)]) -> LedgerService:
    return LedgerService(session)


ServiceDep = Annotated[LedgerService, Depends(_service)]


@router.get("/verify", response_model=VerifyResult)
def verify_chain(service: ServiceDep) -> VerifyResult:
    """Walk the whole chain. Returns {ok: false, broken_at: n} at the first bad entry."""
    return service.verify()


@router.get("", response_model=list[LedgerEntryRead])
def list_entries(
    service: ServiceDep,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[LedgerEntryRead]:
    return [LedgerEntryRead.from_entry(e) for e in service.entries(limit=limit, offset=offset)]


@router.get("/{seq}", response_model=LedgerEntryRead)
def get_entry(seq: int, service: ServiceDep) -> LedgerEntryRead:
    entry = service.get(seq)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"no ledger entry with seq {seq}")
    return LedgerEntryRead.from_entry(entry)
