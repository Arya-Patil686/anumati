"""ConstraintResult, Verdict and StepUp — the output of the conformance engine."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.common import (
    ConstraintStatus,
    Decision,
    StepUpAnswer,
    new_id,
    utc_now,
)

Layer = Literal[1, 2, 3]


class ConstraintResult(BaseModel):
    """One constraint, judged by exactly one layer.

    `evidence_fields` names the structured catalogue fields the decision rests on, so a
    dispute pack can point at them rather than at a sentence of prose.
    """

    model_config = ConfigDict(extra="forbid")

    constraint_id: str
    layer: Layer
    status: ConstraintStatus
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_fields: list[str] = Field(default_factory=list)
    rationale: str = ""
    hard: bool = True


class Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    verdict_id: str = Field(default_factory=lambda: new_id("vrd"))
    mandate_id: str
    cart_id: str
    decision: Decision
    results: list[ConstraintResult] = Field(default_factory=list)

    # Rule 5 — every verdict carries enough to reproduce it.
    engine_version: str
    model_id: str | None = None
    prompt_hash: str | None = None
    latency_ms: int = 0

    created_at: datetime = Field(default_factory=utc_now)

    def violated(self) -> list[ConstraintResult]:
        return [r for r in self.results if r.status == ConstraintStatus.VIOLATED]

    def unclear(self) -> list[ConstraintResult]:
        return [r for r in self.results if r.status == ConstraintStatus.UNCLEAR]

    @property
    def used_llm(self) -> bool:
        return any(r.layer == 3 for r in self.results)


class StepUp(BaseModel):
    """The one-tap question put to the user when a verdict is AMBIGUOUS."""

    model_config = ConfigDict(extra="forbid")

    verdict_id: str
    question_text: str
    user_answer: StepUpAnswer | None = None
    answered_at: datetime | None = None

    def answer(self, response: StepUpAnswer) -> StepUp:
        return self.model_copy(update={"user_answer": response, "answered_at": utc_now()})
