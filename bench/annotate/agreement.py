"""Fleiss' kappa over the human-annotated ambiguous set.

The ambiguous boundary cases are the ones the whole project turns on, so their labels
cannot be asserted by whoever wrote the generator. Three annotators label each case
independently; this script reports how much they actually agreed, and which cases had no
majority.

A case with no majority is not a failure. It is the strongest possible evidence that
AMBIGUOUS is the right answer — if three careful humans split three ways, an engine that
returns a confident yes or no is guessing.

    python -m bench.annotate.agreement --sheet bench/annotate/annotation_sheet.csv
"""

from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

LABELS = ("CONFORMS", "VIOLATES", "AMBIGUOUS")
ANNOTATOR_COLUMNS = ("annotator_1", "annotator_2", "annotator_3")


@dataclass(frozen=True)
class Agreement:
    cases: int
    annotators: int
    kappa: float
    observed: float
    expected: float
    gold: dict[str, str]
    no_majority: list[str]

    @property
    def interpretation(self) -> str:
        """Landis & Koch, stated plainly."""
        if self.kappa < 0.0:
            return "worse than chance"
        if self.kappa < 0.20:
            return "slight"
        if self.kappa < 0.40:
            return "fair"
        if self.kappa < 0.60:
            return "moderate"
        if self.kappa < 0.80:
            return "substantial"
        return "almost perfect"


def read_sheet(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8") as handle:
        return [row for row in csv.DictReader(handle) if any(row.values())]


def fleiss_kappa(ratings: list[list[str]], labels: tuple[str, ...] = LABELS) -> tuple[float, float, float]:
    """Returns (kappa, observed agreement, chance agreement).

    `ratings` is one list of labels per case. Every case must have the same number of
    ratings, which is what the sheet enforces.
    """
    if not ratings:
        return 0.0, 0.0, 0.0

    n = len(ratings[0])
    if n < 2:
        raise ValueError("Fleiss' kappa needs at least two annotators per case")
    if any(len(r) != n for r in ratings):
        raise ValueError("every case must be rated by the same number of annotators")

    counts = [Counter(r) for r in ratings]
    cases = len(ratings)

    # Per-case agreement: the share of annotator pairs that chose the same label.
    per_case = [
        (sum(c[label] ** 2 for label in labels) - n) / (n * (n - 1)) for c in counts
    ]
    observed = sum(per_case) / cases

    totals = {label: sum(c[label] for c in counts) for label in labels}
    proportions = {label: totals[label] / (cases * n) for label in labels}
    expected = sum(p**2 for p in proportions.values())

    if expected >= 1.0:
        return 1.0, observed, expected
    return (observed - expected) / (1 - expected), observed, expected


def majority_label(ratings: list[str]) -> str | None:
    counts = Counter(ratings).most_common()
    if not counts:
        return None
    top, top_count = counts[0]
    if len(counts) > 1 and counts[1][1] == top_count:
        return None
    return top


def analyse(rows: list[dict[str, str]]) -> Agreement:
    ratings: list[list[str]] = []
    ids: list[str] = []

    for row in rows:
        labels = [row[column].strip().upper() for column in ANNOTATOR_COLUMNS]
        if not all(label in LABELS for label in labels):
            continue
        ratings.append(labels)
        ids.append(row.get("case_id") or row.get("instruction_id") or f"row{len(ids)}")

    kappa, observed, expected = fleiss_kappa(ratings)

    gold: dict[str, str] = {}
    no_majority: list[str] = []
    for case_id, labels in zip(ids, ratings, strict=True):
        winner = majority_label(labels)
        if winner is None:
            no_majority.append(case_id)
            gold[case_id] = "AMBIGUOUS"  # three-way split IS the evidence
        else:
            gold[case_id] = winner

    return Agreement(
        cases=len(ratings), annotators=len(ANNOTATOR_COLUMNS), kappa=kappa,
        observed=observed, expected=expected, gold=gold, no_majority=no_majority,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench.annotate.agreement")
    parser.add_argument("--sheet", type=Path,
                        default=Path("bench/annotate/annotation_sheet.csv"))
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    if not args.sheet.exists():
        print(f"no annotation sheet at {args.sheet}")
        return 1

    rows = read_sheet(args.sheet)
    if not rows:
        print(
            f"{args.sheet} has no annotated rows yet.\n"
            "Fill annotator_1..3 with CONFORMS / VIOLATES / AMBIGUOUS and re-run."
        )
        return 1

    result = analyse(rows)
    print(f"  cases annotated : {result.cases}")
    print(f"  annotators      : {result.annotators}")
    print(f"  observed agree  : {result.observed:.3f}")
    print(f"  chance agree    : {result.expected:.3f}")
    print(f"  Fleiss' kappa   : {result.kappa:.3f}  ({result.interpretation})")
    print(f"  no majority     : {len(result.no_majority)} -> gold AMBIGUOUS")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        with args.out.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(["case_id", "gold"])
            for case_id, label in result.gold.items():
                writer.writerow([case_id, label])
        print(f"  wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
