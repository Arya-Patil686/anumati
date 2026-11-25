"""LedgerEntry — the append-only, hash-chained, signed decision record.

This is both the domain model and the stored table, because the stored bytes *are* the
evidence. Nothing else in the system is allowed to update a row here.

Chain rule:
    entry_hash = SHA256(prev_hash || canonical_json(payload))

`prev_hash` of the first entry is GENESIS_HASH (64 zeros). `signature` is an Ed25519
signature over the ASCII bytes of `entry_hash`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel
from sqlalchemy import JSON, Column
from sqlmodel import Field, SQLModel

from app.canonical import canonical_json, sha256_hex
from app.models.common import LedgerKind, utc_now

GENESIS_HASH = "0" * 64


def compute_entry_hash(prev_hash: str, payload: Any) -> str:
    """The one definition of the chain link. Used by both append and verify."""
    return sha256_hex(prev_hash.encode("ascii") + canonical_json(payload))


class LedgerEntry(SQLModel, table=True):
    __tablename__ = "ledger_entries"

    seq: int | None = Field(default=None, primary_key=True)
    ts: datetime = Field(default_factory=utc_now, index=True)
    kind: str = Field(index=True)
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    payload_hash: str
    prev_hash: str
    entry_hash: str = Field(index=True)
    signature: str

    def recomputed_hash(self) -> str:
        return compute_entry_hash(self.prev_hash, self.payload)

    def recomputed_payload_hash(self) -> str:
        return sha256_hex(canonical_json(self.payload))


class LedgerEntryRead(BaseModel):
    """API shape. Identical fields, but a plain Pydantic model with seq guaranteed."""

    seq: int
    ts: datetime
    kind: LedgerKind | str
    payload: dict[str, Any]
    payload_hash: str
    prev_hash: str
    entry_hash: str
    signature: str

    @classmethod
    def from_entry(cls, entry: LedgerEntry) -> LedgerEntryRead:
        if entry.seq is None:
            raise ValueError("entry has not been persisted yet")
        return cls(
            seq=entry.seq,
            ts=entry.ts,
            kind=entry.kind,
            payload=entry.payload,
            payload_hash=entry.payload_hash,
            prev_hash=entry.prev_hash,
            entry_hash=entry.entry_hash,
            signature=entry.signature,
        )


class VerifyResult(BaseModel):
    """What GET /ledger/verify returns.

    `broken_at` is the seq of the first entry that fails, or None when the chain is intact.
    """

    ok: bool
    broken_at: int | None = None
    reason: str | None = None
    entries_checked: int = 0
    head_hash: str | None = None
    public_key_hex: str | None = None
