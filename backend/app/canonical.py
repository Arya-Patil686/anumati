"""Canonical JSON and hashing.

Rule 4 of CLAUDE.md: every hash in this system is taken over the *same* byte encoding,
or the ledger cannot be re-verified by anyone else. Sorted keys, no spaces, UTF-8,
no ASCII escaping.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel


def canonical_json(obj: Any) -> bytes:
    """Serialise to the one byte representation this system hashes and signs."""
    if isinstance(obj, BaseModel):
        obj = obj.model_dump(mode="json")
    text = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return text.encode("utf-8")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def payload_hash(payload: Any) -> str:
    """SHA-256 of the canonical JSON of a payload."""
    return sha256_hex(canonical_json(payload))


def prompt_hash(prompt: str) -> str:
    """Stable identifier for the exact prompt text that produced a verdict (rule 5)."""
    return sha256_hex(prompt.encode("utf-8"))
