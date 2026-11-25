"""Dev/test splits with held-out operators and categories.

Anti-circularity rule 1 from the spec: whole perturbation operators and whole product
categories are held out for test, and thresholds are never tuned on them. Splitting at
random would let the engine be tuned on the same kinds of error it is then scored on,
and the numbers would mean nothing.

The split is deterministic given a seed, and `manifest()` records exactly what was held
out so a reader can check the claim.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

from bench.generate import BenchCase

SEED = 7
HELD_OUT_OPERATOR_SHARE = 0.30
HELD_OUT_CATEGORY_SHARE = 0.30


@dataclass(frozen=True)
class Split:
    dev: list[BenchCase]
    test: list[BenchCase]
    held_out_operators: frozenset[str]
    held_out_categories: frozenset[str]

    def manifest(self) -> dict[str, Any]:
        return {
            "dev_cases": len(self.dev),
            "test_cases": len(self.test),
            "held_out_operators": sorted(self.held_out_operators),
            "held_out_categories": sorted(self.held_out_categories),
        }


def make_split(cases: list[BenchCase], seed: int = SEED) -> Split:
    rng = random.Random(seed)

    operators = sorted({c.operator for c in cases if c.operator not in ("none",)})
    categories = sorted({c.category_path for c in cases})

    held_operators = frozenset(
        rng.sample(operators, k=max(1, round(len(operators) * HELD_OUT_OPERATOR_SHARE)))
    )
    held_categories = frozenset(
        rng.sample(categories, k=max(1, round(len(categories) * HELD_OUT_CATEGORY_SHARE)))
    )

    dev: list[BenchCase] = []
    test: list[BenchCase] = []
    for case in cases:
        unseen = case.operator in held_operators or case.category_path in held_categories
        (test if unseen else dev).append(case)

    return Split(
        dev=dev,
        test=test,
        held_out_operators=held_operators,
        held_out_categories=held_categories,
    )


def check_no_leakage(split: Split) -> list[str]:
    """Returns the problems found. An empty list is the claim this split makes."""
    problems: list[str] = []

    dev_operators = {c.operator for c in split.dev}
    leaked_operators = dev_operators & split.held_out_operators
    if leaked_operators:
        problems.append(f"held-out operators present in dev: {sorted(leaked_operators)}")

    dev_categories = {c.category_path for c in split.dev}
    leaked_categories = dev_categories & split.held_out_categories
    if leaked_categories:
        problems.append(f"held-out categories present in dev: {sorted(leaked_categories)}")

    dev_ids = {c.id for c in split.dev}
    overlap = dev_ids & {c.id for c in split.test}
    if overlap:
        problems.append(f"{len(overlap)} case(s) appear in both splits")

    if not split.dev or not split.test:
        problems.append("one of the splits is empty")

    return problems
