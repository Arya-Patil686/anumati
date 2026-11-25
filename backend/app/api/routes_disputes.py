"""POST /disputes — a claim in, an evidence pack out."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.config import REPO_ROOT
from app.db import get_session
from app.models.common import LedgerKind
from app.models.store import DisputeRow
from app.services.disputes import EvidencePack, build_pack
from app.services.evidence_pdf import render
from app.services.ledger import LedgerService

router = APIRouter(prefix="/disputes", tags=["disputes"])

SessionDep = Annotated[Session, Depends(get_session)]
PACK_DIR = REPO_ROOT / "data" / "evidence"


class DisputeRequest(BaseModel):
    cart_id: str
    claim_text: str = Field(min_length=1, max_length=2000)
    claim_category: str | None = None


class DisputeResponse(BaseModel):
    pack: EvidencePack
    pdf_path: str
    pdf_url: str
    ledger_seq: int | None = None


@router.post("", response_model=DisputeResponse, status_code=201)
def open_dispute(body: DisputeRequest, session: SessionDep) -> DisputeResponse:
    pack = build_pack(session, body.cart_id, body.claim_text, body.claim_category)
    if not pack.items:
        raise HTTPException(status_code=404, detail=f"nothing on record for {body.cart_id}")

    pdf_path = PACK_DIR / f"{pack.dispute.dispute_id}.pdf"
    render(pack, pdf_path)

    dispute = pack.dispute.model_copy(update={"pack_pdf_path": str(pdf_path)})
    entry = LedgerService(session).append(LedgerKind.DISPUTE_OPENED, dispute)

    session.add(DisputeRow.from_domain(dispute))
    session.commit()

    return DisputeResponse(
        pack=pack.model_copy(update={"dispute": dispute}),
        pdf_path=str(pdf_path),
        pdf_url=f"/disputes/{dispute.dispute_id}/pack.pdf",
        ledger_seq=entry.seq,
    )


@router.get("/{dispute_id}", response_model=DisputeResponse)
def get_dispute(dispute_id: str, session: SessionDep) -> DisputeResponse:
    row = session.get(DisputeRow, dispute_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"no dispute {dispute_id}")

    dispute = row.to_domain()
    pack = build_pack(session, dispute.cart_id, dispute.claim_text, dispute.claim_category)
    return DisputeResponse(
        pack=pack.model_copy(update={"dispute": dispute}),
        pdf_path=dispute.pack_pdf_path or "",
        pdf_url=f"/disputes/{dispute_id}/pack.pdf",
    )


@router.get("/{dispute_id}/pack.pdf")
def download_pack(dispute_id: str, session: SessionDep) -> FileResponse:
    row = session.get(DisputeRow, dispute_id)
    if row is None or not row.pack_pdf_path:
        raise HTTPException(status_code=404, detail=f"no evidence pack for {dispute_id}")

    from pathlib import Path

    path = Path(row.pack_pdf_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="the evidence pack file is missing")

    return FileResponse(
        path, media_type="application/pdf", filename=f"anumati-{dispute_id}.pdf"
    )
