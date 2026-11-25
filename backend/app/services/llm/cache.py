"""Record/replay cache for LLM calls.

The venue internet can fail, so the demo must run with the network off (CLAUDE.md
rule 2). Every call is keyed by the exact bytes of the rendered prompt, so a replay is
the same prompt getting the same answer — not a fuzzy match.

A miss in replay mode raises `CacheMiss` rather than falling through to the network.
Failing loudly is the point: a silent network call during the demo is the failure we are
trying to avoid.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from app.canonical import sha256_hex
from app.models.common import utc_now

_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_cache (
    cache_key   TEXT PRIMARY KEY,
    prompt_name TEXT NOT NULL,
    model_id    TEXT NOT NULL,
    prompt_hash TEXT NOT NULL,
    prompt_text TEXT NOT NULL,
    response    TEXT NOT NULL,
    latency_ms  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_cache_prompt ON llm_cache(prompt_hash);
"""


class CacheMiss(RuntimeError):
    """Raised when LLM_MODE=replay and the prompt was never recorded."""

    def __init__(self, prompt_name: str, prompt_hash: str) -> None:
        super().__init__(
            f"no recorded response for prompt {prompt_name!r} ({prompt_hash[:12]}). "
            f"Re-run with LLM_MODE=record while you have network."
        )
        self.prompt_name = prompt_name
        self.prompt_hash = prompt_hash


@dataclass(frozen=True)
class CacheEntry:
    cache_key: str
    prompt_name: str
    model_id: str
    prompt_hash: str
    response: str
    latency_ms: int
    created_at: datetime


def make_cache_key(model_id: str, prompt_hash: str, temperature: float) -> str:
    """Same model, same prompt, same temperature — same answer on replay."""
    return sha256_hex(f"{model_id}|{prompt_hash}|{temperature:.2f}".encode())


class LLMCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._conn:
            self._conn.executescript(_SCHEMA)

    def get(self, cache_key: str) -> CacheEntry | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM llm_cache WHERE cache_key = ?", (cache_key,)
            ).fetchone()
        if row is None:
            return None
        return CacheEntry(
            cache_key=row["cache_key"],
            prompt_name=row["prompt_name"],
            model_id=row["model_id"],
            prompt_hash=row["prompt_hash"],
            response=row["response"],
            latency_ms=row["latency_ms"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    def put(
        self,
        *,
        cache_key: str,
        prompt_name: str,
        model_id: str,
        prompt_hash: str,
        prompt_text: str,
        response: str,
        latency_ms: int,
    ) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO llm_cache "
                "(cache_key, prompt_name, model_id, prompt_hash, prompt_text, response, "
                " latency_ms, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    cache_key,
                    prompt_name,
                    model_id,
                    prompt_hash,
                    prompt_text,
                    response,
                    latency_ms,
                    utc_now().isoformat(),
                ),
            )

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._conn.close()
