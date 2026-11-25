"""DisputeCase — a claim, the ledger records that answer it, and a proposed allocation.

The allocation is ANUMATI's proposal. Current card-scheme rules still govern who pays;
see docs/spec.pdf section 4.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.common import Allocation, new_id, utc_now


class DisputeCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dispute_id: str = Field(default_factory=lambda: new_id("dsp"))
    cart_id: str
    claim_text: str
    claim_category: str | None = None
    evidence_refs: list[int] = Field(
        default_factory=list,
        description="Ledger seq numbers that make up the evidence pack.",
    )
    proposed_allocation: Allocation | None = None
    visa_code: str | None = None
    mc_code: str | None = None
    pack_pdf_path: str | None = None
    created_at: datetime = Field(default_factory=utc_now)
