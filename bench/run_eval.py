"""Run every system over MCB-IN and write the reports.

This is the only thing in the repository allowed to produce metric numbers
(CLAUDE.md rule 8). If a number appears in the README or a slide, it came from here.

    python -m bench.run_eval --cases data/bench/cases.jsonl

Writes to bench/reports/:
    results.md          the table, per split, with confusion matrices
    results.csv         one row per (system, split)
    per_operator.csv    accuracy per perturbation operator
    calibration.png     reliability curve per system
    manifest.json       split, seeds, encoder, counts — what the numbers rest on
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.config import ENGINE_VERSION, settings
from app.models.cart import Cart
from app.models.common import ConstraintStatus, Decision
from app.models.mandate import IntentMandate
from app.services.catalogue import CatalogueService
from app.services.catalogue_synth import synthetic_catalogue
from app.services.engine.aggregator import ConformanceEngine

from bench.baselines import Prediction, timed
from bench.baselines.b0_numeric import B0Numeric
from bench.baselines.b1_keyword import B1Keyword
from bench.baselines.b2_embedding import B2Embedding
from bench.baselines.b3_llm_oneprompt import B3LLMOnePrompt
from bench.generate import BenchCase, read_jsonl, summarise
from bench.splits import check_no_leakage, make_split

REPORTS = Path("bench/reports")
CLASSES = (Decision.CONFORMS, Decision.VIOLATES, Decision.AMBIGUOUS)
CALIBRATION_BINS = 10


# --- ANUMATI as a system -------------------------------------------------------


class Anumati:
    name = "ANUMATI"
    description = "Layered, per-constraint, with an AMBIGUOUS band"

    def __init__(self, engine: ConformanceEngine | None = None) -> None:
        self.engine = engine or ConformanceEngine()
        self.l3_calls = 0
        self.constraints_seen = 0

    @timed
    def predict(  # type: ignore[override]
        self, mandate: IntentMandate, cart: Cart
    ) -> tuple[Decision, float, bool, str]:
        outcome = self.engine.check(mandate, cart, signature_verified=True)
        self.l3_calls += outcome.l3_calls
        self.constraints_seen += len(mandate.constraints)

        verdict = outcome.verdict
        hard = [r for r in verdict.results if r.hard]
        confidence = _decision_confidence(verdict.decision, hard)
        return verdict.decision, confidence, outcome.l3_calls > 0, ""


def _decision_confidence(decision: Decision, hard: list[Any]) -> float:
    """How sure the engine is of the decision it reached. Used for calibration."""
    if not hard:
        return 0.5
    if decision is Decision.VIOLATES:
        violated = [r.confidence for r in hard if r.status is ConstraintStatus.VIOLATED]
        return max(violated) if violated else 0.5
    if decision is Decision.CONFORMS:
        return min(r.confidence for r in hard)
    unclear = [r.confidence for r in hard if r.status is ConstraintStatus.UNCLEAR]
    # An ambiguous verdict is a confident statement that we do not know.
    return 1.0 - (max(unclear) if unclear else 0.5)


# --- metrics ---------------------------------------------------------------------


@dataclass
class Scores:
    system: str
    split: str
    n: int = 0
    violation_recall: float = 0.0
    false_block_rate: float = 0.0
    step_up_rate: float = 0.0
    macro_f1: float = 0.0
    accuracy: float = 0.0
    english_f1: float = 0.0
    hinglish_f1: float = 0.0
    hinglish_gap: float = 0.0
    injection_asr: float = 0.0
    p50_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    llm_calls_per_check: float = 0.0
    ece: float = 0.0
    confusion: dict[str, dict[str, int]] = field(default_factory=dict)

    def row(self) -> dict[str, Any]:
        return {
            "system": self.system,
            "split": self.split,
            "n": self.n,
            "violation_recall": round(self.violation_recall, 4),
            "false_block_rate": round(self.false_block_rate, 4),
            "step_up_rate": round(self.step_up_rate, 4),
            "macro_f1": round(self.macro_f1, 4),
            "accuracy": round(self.accuracy, 4),
            "english_f1": round(self.english_f1, 4),
            "hinglish_f1": round(self.hinglish_f1, 4),
            "hinglish_gap": round(self.hinglish_gap, 4),
            "injection_asr": round(self.injection_asr, 4),
            "p50_latency_ms": round(self.p50_latency_ms, 2),
            "p95_latency_ms": round(self.p95_latency_ms, 2),
            "llm_calls_per_check": round(self.llm_calls_per_check, 3),
            "ece": round(self.ece, 4),
        }


def _f1(gold: list[str], pred: list[str], label: str) -> float:
    tp = sum(1 for g, p in zip(gold, pred, strict=True) if g == label and p == label)
    fp = sum(1 for g, p in zip(gold, pred, strict=True) if g != label and p == label)
    fn = sum(1 for g, p in zip(gold, pred, strict=True) if g == label and p != label)
    if tp == 0:
        return 0.0
    precision = tp / (tp + fp)
    recall = tp / (tp + fn)
    return 2 * precision * recall / (precision + recall)


def macro_f1(gold: list[str], pred: list[str]) -> float:
    if not gold:
        return 0.0
    present = [c.value for c in CLASSES if c.value in set(gold)]
    return sum(_f1(gold, pred, label) for label in present) / len(present) if present else 0.0


def expected_calibration_error(
    correct: list[bool], confidence: list[float], bins: int = CALIBRATION_BINS
) -> tuple[float, list[tuple[float, float, int]]]:
    """ECE plus the reliability curve points, so the plot and the number agree."""
    if not correct:
        return 0.0, []

    buckets: dict[int, list[tuple[bool, float]]] = defaultdict(list)
    for is_correct, conf in zip(correct, confidence, strict=True):
        index = min(bins - 1, max(0, int(conf * bins)))
        buckets[index].append((is_correct, conf))

    total = len(correct)
    ece = 0.0
    curve: list[tuple[float, float, int]] = []
    for index in sorted(buckets):
        entries = buckets[index]
        mean_conf = sum(c for _, c in entries) / len(entries)
        accuracy = sum(1 for ok, _ in entries if ok) / len(entries)
        ece += (len(entries) / total) * abs(accuracy - mean_conf)
        curve.append((mean_conf, accuracy, len(entries)))
    return ece, curve


def score(
    system_name: str,
    split_name: str,
    cases: list[BenchCase],
    predictions: list[Prediction],
) -> tuple[Scores, list[tuple[float, float, int]]]:
    gold = [c.gold for c in cases]
    pred = [p.decision.value for p in predictions]

    violations = [i for i, g in enumerate(gold) if g == Decision.VIOLATES.value]
    conforming = [i for i, g in enumerate(gold) if g == Decision.CONFORMS.value]
    injections = [i for i, c in enumerate(cases) if c.injection]

    english = [i for i, c in enumerate(cases) if c.language == "en"]
    hinglish = [i for i, c in enumerate(cases) if c.language != "en"]

    latencies = sorted(p.latency_ms for p in predictions)
    correct = [g == p for g, p in zip(gold, pred, strict=True)]
    ece, curve = expected_calibration_error(correct, [p.confidence for p in predictions])

    english_f1 = macro_f1([gold[i] for i in english], [pred[i] for i in english])
    hinglish_f1 = macro_f1([gold[i] for i in hinglish], [pred[i] for i in hinglish])

    confusion: dict[str, dict[str, int]] = {
        g.value: dict.fromkeys([c.value for c in CLASSES], 0) for g in CLASSES
    }
    for g, p in zip(gold, pred, strict=True):
        confusion[g][p] += 1

    scores = Scores(
        system=system_name,
        split=split_name,
        n=len(cases),
        violation_recall=(
            sum(1 for i in violations if pred[i] == Decision.VIOLATES.value) / len(violations)
            if violations else 0.0
        ),
        false_block_rate=(
            sum(1 for i in conforming if pred[i] == Decision.VIOLATES.value) / len(conforming)
            if conforming else 0.0
        ),
        step_up_rate=sum(1 for p in pred if p == Decision.AMBIGUOUS.value) / len(pred),
        macro_f1=macro_f1(gold, pred),
        accuracy=sum(correct) / len(correct),
        english_f1=english_f1,
        hinglish_f1=hinglish_f1,
        hinglish_gap=english_f1 - hinglish_f1,
        # The payload wants CONFORMS. An attack succeeds when it gets one it should not.
        injection_asr=(
            sum(
                1 for i in injections
                if pred[i] == Decision.CONFORMS.value and gold[i] != Decision.CONFORMS.value
            ) / len(injections)
            if injections else 0.0
        ),
        p50_latency_ms=statistics.median(latencies) if latencies else 0.0,
        p95_latency_ms=latencies[int(len(latencies) * 0.95)] if latencies else 0.0,
        llm_calls_per_check=sum(1 for p in predictions if p.used_llm) / len(predictions),
        ece=ece,
        confusion=confusion,
    )
    return scores, curve


def per_operator_accuracy(
    cases: list[BenchCase], predictions: list[Prediction]
) -> dict[str, tuple[int, float]]:
    totals: Counter[str] = Counter()
    hits: Counter[str] = Counter()
    for case, prediction in zip(cases, predictions, strict=True):
        totals[case.operator] += 1
        if case.gold == prediction.decision.value:
            hits[case.operator] += 1
    return {op: (totals[op], hits[op] / totals[op]) for op in sorted(totals)}


# --- running ------------------------------------------------------------------------


def _load(case: BenchCase) -> tuple[IntentMandate, Cart]:
    return IntentMandate.model_validate(case.mandate), Cart.model_validate(case.cart)


def run_system(system: Any, cases: list[BenchCase]) -> list[Prediction]:
    predictions: list[Prediction] = []
    for case in cases:
        mandate, cart = _load(case)
        predictions.append(system.predict(mandate, cart))
    return predictions


# --- reporting -------------------------------------------------------------------------


def _table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> str:
    header = "| " + " | ".join(title for _key, title in columns) + " |"
    rule = "|" + "|".join("---" for _ in columns) + "|"
    lines = [header, rule]
    for row in rows:
        cells = []
        for key, _title in columns:
            value = row[key]
            cells.append(f"{value:.1%}" if isinstance(value, float) and key.endswith(
                ("recall", "rate", "f1", "gap", "accuracy", "asr", "ece")
            ) else str(value))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _confusion_block(scores: Scores) -> str:
    lines = [f"**{scores.system} — {scores.split}**", "", "| gold \\ predicted | "
             + " | ".join(c.value for c in CLASSES) + " |",
             "|---|" + "|".join("---" for _ in CLASSES) + "|"]
    for gold_label in CLASSES:
        row = scores.confusion[gold_label.value]
        lines.append(
            f"| **{gold_label.value}** | " + " | ".join(str(row[c.value]) for c in CLASSES) + " |"
        )
    return "\n".join(lines)


def write_calibration_plot(
    curves: dict[str, list[tuple[float, float, int]]], path: Path
) -> Path | None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None

    figure, axis = plt.subplots(figsize=(6, 5))
    axis.plot([0, 1], [0, 1], "--", color="#888", linewidth=1, label="perfect calibration")
    for name, curve in curves.items():
        if not curve:
            continue
        axis.plot([c for c, _a, _n in curve], [a for _c, a, _n in curve],
                  marker="o", label=name)
    axis.set_xlabel("confidence")
    axis.set_ylabel("accuracy")
    axis.set_title("Reliability — MCB-IN test split")
    axis.set_xlim(0, 1)
    axis.set_ylim(0, 1)
    axis.legend(loc="lower right", fontsize=8)
    figure.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    figure.savefig(path, dpi=150)
    plt.close(figure)
    return path


def write_reports(
    all_scores: list[Scores],
    operator_rows: dict[str, dict[str, tuple[int, float]]],
    manifest: dict[str, Any],
    curves: dict[str, list[tuple[float, float, int]]],
    reports_dir: Path = REPORTS,
) -> None:
    reports_dir.mkdir(parents=True, exist_ok=True)

    import csv

    with (reports_dir / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(all_scores[0].row()))
        writer.writeheader()
        for scores in all_scores:
            writer.writerow(scores.row())

    with (reports_dir / "per_operator.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["system", "operator", "n", "accuracy"])
        for system, rows in operator_rows.items():
            for operator, (count, accuracy) in rows.items():
                writer.writerow([system, operator, count, round(accuracy, 4)])

    (reports_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    plot = write_calibration_plot(curves, reports_dir / "calibration.png")

    columns = [
        ("system", "System"), ("n", "n"), ("violation_recall", "Viol. recall"),
        ("false_block_rate", "False-block"), ("step_up_rate", "Step-up"),
        ("macro_f1", "Macro-F1"), ("hinglish_gap", "Hinglish gap"),
        ("injection_asr", "Injection ASR"), ("p95_latency_ms", "p95 ms"),
        ("ece", "ECE"),
    ]

    sections = [
        "# MCB-IN results",
        "",
        (
            f"Generated by `bench/run_eval.py` on {manifest['generated_at']}. "
            "Nothing else in this repository writes metric numbers."
        ),
        "",
        "## What the labels are",
        "",
        (
            "Gold labels come from how each case was **built** — the perturbation "
            "operator applied, or the structured catalogue fields — never from any "
            "model's output. The engine's own LLM path is not consulted when labelling."
        ),
        "",
        "## Split",
        "",
        (
            "Whole operators and whole categories are held out for test. Thresholds "
            "are tuned on dev and frozen before the test run."
        ),
        "",
        "```json",
        json.dumps(manifest["split"], indent=2),
        "```",
        "",
    ]

    for split_name in ("dev", "test"):
        rows = [s.row() for s in all_scores if s.split == split_name]
        if not rows:
            continue
        sections += [f"## {split_name} split", "", _table(rows, columns), ""]

    sections += ["## Confusion matrices", ""]
    for scores in all_scores:
        sections += [_confusion_block(scores), ""]

    if plot is not None:
        sections += ["## Calibration", "", "![reliability](calibration.png)", ""]

    if manifest.get("notes"):
        sections += ["## Notes", ""] + [f"- {note}" for note in manifest["notes"]] + [""]

    (reports_dir / "results.md").write_text("\n".join(sections), encoding="utf-8")


# --- entry point ---------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench.run_eval")
    parser.add_argument("--cases", type=Path, default=Path("data/bench/cases.jsonl"))
    parser.add_argument("--reports", type=Path, default=REPORTS)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--sentence-transformers", action="store_true")
    args = parser.parse_args(argv)

    if not args.cases.exists():
        print(f"no cases at {args.cases}; run `python -m bench.generate` first")
        return 1

    cases = read_jsonl(args.cases)
    if args.limit:
        cases = cases[: args.limit]

    split = make_split(cases)
    problems = check_no_leakage(split)
    if problems:
        print("split check failed:")
        for problem in problems:
            print(f"  - {problem}")
        return 1

    catalogue = CatalogueService(synthetic_catalogue())
    encoder = None
    if args.sentence_transformers:
        from bench.baselines.b2_embedding import build_encoder

        encoder = build_encoder(settings.embedding_model, prefer_transformers=True)

    b2 = B2Embedding(encoder=encoder)
    b3 = B3LLMOnePrompt()
    anumati = Anumati()
    systems = [B0Numeric(), B1Keyword(), b2, b3, anumati]

    all_scores: list[Scores] = []
    operator_rows: dict[str, dict[str, tuple[int, float]]] = {}
    curves: dict[str, list[tuple[float, float, int]]] = {}
    notes: list[str] = []

    for system in systems:
        for split_name, subset in (("dev", split.dev), ("test", split.test)):
            predictions = run_system(system, subset)

            if isinstance(system, B3LLMOnePrompt) and not system.available:
                if split_name == "dev":
                    notes.append(
                        f"**{system.name} was not run.** No model was reachable "
                        f"({system.unavailable_reason}). Its row is omitted rather than "
                        f"filled with invented numbers. Re-run with `LLM_MODE=record` "
                        f"and a provider configured to include it."
                    )
                continue

            scores, curve = score(system.name, split_name, subset, predictions)
            all_scores.append(scores)
            if split_name == "test":
                operator_rows[system.name] = per_operator_accuracy(subset, predictions)
                curves[system.name] = curve

            print(
                f"  {system.name:22} {split_name:5} "
                f"n={scores.n:5}  recall={scores.violation_recall:.3f}  "
                f"false-block={scores.false_block_rate:.3f}  "
                f"step-up={scores.step_up_rate:.3f}  macroF1={scores.macro_f1:.3f}"
            )

    notes.append(f"B2 encoder: `{b2.encoder_name}`.")
    notes.append(
        f"ANUMATI called its LLM judge for "
        f"{anumati.l3_calls}/{anumati.constraints_seen} constraints "
        f"({anumati.l3_calls / max(1, anumati.constraints_seen):.1%}); "
        f"in replay mode with nothing recorded those calls fail safe to a step-up."
    )
    notes.append(
        "Latency is measured in-process on one machine and excludes network time to a "
        "hosted model. It is a relative comparison, not a production figure."
    )

    manifest = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "engine_version": ENGINE_VERSION,
        "llm_mode": settings.llm_mode,
        "dataset": summarise(cases),
        "split": split.manifest(),
        "notes": notes,
        "catalogue_skus": len(catalogue),
    }

    write_reports(all_scores, operator_rows, manifest, curves, args.reports)
    print(f"\nwrote {args.reports}/results.md and results.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
