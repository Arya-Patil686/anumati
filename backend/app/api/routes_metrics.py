"""GET /metrics — the latest benchmark results, for the console.

Reads what `bench/run_eval.py` wrote. Nothing here computes or adjusts a number
(CLAUDE.md rule 8); if the reports are missing, it says so rather than guessing.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.config import REPO_ROOT

router = APIRouter(tags=["metrics"])

REPORTS = REPO_ROOT / "bench" / "reports"


class MetricsResponse(BaseModel):
    available: bool
    message: str = ""
    generated_at: str | None = None
    dataset: dict[str, Any] = Field(default_factory=dict)
    split: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    per_operator: list[dict[str, Any]] = Field(default_factory=list)


def _read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [dict(row) for row in csv.DictReader(handle)]


@router.get("/metrics", response_model=MetricsResponse)
def metrics() -> MetricsResponse:
    results = REPORTS / "results.csv"
    manifest_path = REPORTS / "manifest.json"

    if not results.exists():
        return MetricsResponse(
            available=False,
            message="No benchmark results yet. Run `make bench` to generate them.",
        )

    manifest: dict[str, Any] = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    rows = _read_csv(results)
    for row in rows:
        for key, value in list(row.items()):
            if key in ("system", "split"):
                continue
            try:
                row[key] = float(value)
            except (TypeError, ValueError):
                pass

    return MetricsResponse(
        available=True,
        generated_at=manifest.get("generated_at"),
        dataset=manifest.get("dataset", {}),
        split=manifest.get("split", {}),
        notes=manifest.get("notes", []),
        rows=rows,
        per_operator=_read_csv(REPORTS / "per_operator.csv"),
    )
