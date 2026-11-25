"""The decision ledger: append-only, hash-chained, Ed25519-signed.

Why both a chain and a signature
--------------------------------
The hash chain shows that no earlier entry was changed after the fact. The signature
shows the entries came from this service and not from whoever got hold of the database
file. A dispute pack is only worth anything if both hold.

Chain rule (one definition, in app/models/ledger.py):
    entry_hash = SHA256(prev_hash || canonical_json(payload))

Verification walks the chain from seq 1 and stops at the first entry that fails, which
is what `broken_at` reports.
"""

from __future__ import annotations

import threading
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from pydantic import BaseModel
from sqlmodel import Session, desc, select

from app.canonical import canonical_json, sha256_hex
from app.config import settings
from app.models.common import LedgerKind
from app.models.ledger import (
    GENESIS_HASH,
    LedgerEntry,
    VerifyResult,
    compute_entry_hash,
)

# SQLite gives us one writer. The lock keeps seq allocation and the prev_hash read
# inside one critical section so two appends cannot pick up the same head.
_append_lock = threading.Lock()


class LedgerSigner:
    """Holds the Ed25519 key pair and signs entry hashes."""

    def __init__(self, private_key: Ed25519PrivateKey) -> None:
        self._private_key = private_key
        self._public_key: Ed25519PublicKey = private_key.public_key()

    @classmethod
    def from_settings(cls) -> LedgerSigner:
        """Key from the env, else from keys/, else a dev key generated and stored there."""
        if settings.ledger_signing_key_hex:
            seed = bytes.fromhex(settings.ledger_signing_key_hex.strip())
            return cls(Ed25519PrivateKey.from_private_bytes(seed))

        path = settings.ledger_key_path
        if path.exists():
            seed = bytes.fromhex(path.read_text().strip())
            return cls(Ed25519PrivateKey.from_private_bytes(seed))

        key = Ed25519PrivateKey.generate()
        seed = key.private_bytes_raw()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(seed.hex())
        path.chmod(0o600)
        return cls(key)

    @classmethod
    def generate(cls) -> LedgerSigner:
        return cls(Ed25519PrivateKey.generate())

    @property
    def public_key_hex(self) -> str:
        return self._public_key.public_bytes_raw().hex()

    def sign(self, entry_hash: str) -> str:
        return self._private_key.sign(entry_hash.encode("ascii")).hex()

    def verify(self, entry_hash: str, signature_hex: str) -> bool:
        try:
            self._public_key.verify(bytes.fromhex(signature_hex), entry_hash.encode("ascii"))
        except (InvalidSignature, ValueError):
            return False
        return True


class LedgerService:
    """Append and verify. There is deliberately no update or delete."""

    def __init__(self, session: Session, signer: LedgerSigner | None = None) -> None:
        self.session = session
        self.signer = signer or get_signer()

    # --- reading -------------------------------------------------------------

    def head(self) -> LedgerEntry | None:
        stmt = select(LedgerEntry).order_by(desc(LedgerEntry.seq)).limit(1)
        return self.session.exec(stmt).first()

    def head_hash(self) -> str:
        head = self.head()
        return head.entry_hash if head else GENESIS_HASH

    def entries(self, limit: int = 100, offset: int = 0) -> list[LedgerEntry]:
        stmt = select(LedgerEntry).order_by(LedgerEntry.seq).offset(offset).limit(limit)
        return list(self.session.exec(stmt).all())

    def get(self, seq: int) -> LedgerEntry | None:
        return self.session.get(LedgerEntry, seq)

    def count(self) -> int:
        return len(list(self.session.exec(select(LedgerEntry.seq)).all()))

    # --- appending -----------------------------------------------------------

    def append(self, kind: LedgerKind | str, payload: BaseModel | dict[str, Any]) -> LedgerEntry:
        """Add one entry to the end of the chain and return it (with seq assigned)."""
        doc: dict[str, Any] = (
            payload.model_dump(mode="json") if isinstance(payload, BaseModel) else payload
        )

        with _append_lock:
            prev_hash = self.head_hash()
            entry = LedgerEntry(
                kind=str(kind),
                payload=doc,
                payload_hash=sha256_hex(canonical_json(doc)),
                prev_hash=prev_hash,
                entry_hash=compute_entry_hash(prev_hash, doc),
                signature="",
            )
            entry.signature = self.signer.sign(entry.entry_hash)
            self.session.add(entry)
            self.session.commit()
            self.session.refresh(entry)
        return entry

    # --- verification --------------------------------------------------------

    def verify(self) -> VerifyResult:
        """Walk the chain from the start; report the first entry that does not hold up."""
        entries = list(self.session.exec(select(LedgerEntry).order_by(LedgerEntry.seq)).all())

        prev_hash = GENESIS_HASH
        for index, entry in enumerate(entries, start=1):
            seq = entry.seq if entry.seq is not None else index

            if entry.seq != index:
                # Report the seq that is *missing*, not the one that turned up in its place.
                return self._broken(
                    index, f"sequence gap: expected seq {index}, found {entry.seq}"
                )

            if entry.prev_hash != prev_hash:
                return self._broken(seq, "prev_hash does not match the previous entry's hash")

            if entry.recomputed_payload_hash() != entry.payload_hash:
                return self._broken(seq, "payload_hash does not match the stored payload")

            if entry.recomputed_hash() != entry.entry_hash:
                return self._broken(seq, "entry_hash does not match prev_hash + payload")

            if not self.signer.verify(entry.entry_hash, entry.signature):
                return self._broken(seq, "signature is not valid for this entry_hash")

            prev_hash = entry.entry_hash

        return VerifyResult(
            ok=True,
            broken_at=None,
            entries_checked=len(entries),
            head_hash=prev_hash,
            public_key_hex=self.signer.public_key_hex,
        )

    def _broken(self, seq: int, reason: str) -> VerifyResult:
        return VerifyResult(
            ok=False,
            broken_at=seq,
            reason=reason,
            entries_checked=seq,
            public_key_hex=self.signer.public_key_hex,
        )


_signer: LedgerSigner | None = None


def get_signer() -> LedgerSigner:
    global _signer
    if _signer is None:
        _signer = LedgerSigner.from_settings()
    return _signer


def set_signer(signer: LedgerSigner) -> None:
    """Used by the tests so they never touch the on-disk dev key."""
    global _signer
    _signer = signer
