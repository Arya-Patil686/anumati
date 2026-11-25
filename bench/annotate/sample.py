"""Fill the annotation sheet with the cases humans need to label.

Annotators see the instruction and the cart's structured fields. They never see the
operator that was applied or any engine output, so their labels are independent of both.

    python -m bench.annotate.sample --cases data/bench/cases.jsonl --n 200
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

from bench.generate import read_jsonl

SHEET = Path("bench/annotate/annotation_sheet.csv")
COLUMNS = [
    "case_id",
    "instruction",
    "language",
    "item_title",
    "item_category",
    "item_attributes",
    "unit_price_paise",
    "qty",
    "is_subscription",
    "cart_total_paise",
    "delivery_eta",
    "annotator_1",
    "annotator_2",
    "annotator_3",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="bench.annotate.sample")
    parser.add_argument("--cases", type=Path, default=Path("data/bench/cases.jsonl"))
    parser.add_argument("--out", type=Path, default=SHEET)
    parser.add_argument("--n", type=int, default=200, help="150-300 per the spec")
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args(argv)

    if not args.cases.exists():
        print(f"no cases at {args.cases}; run `python -m bench.generate` first")
        return 1

    cases = [c for c in read_jsonl(args.cases) if c.gold == "AMBIGUOUS"]
    if not cases:
        print("no ambiguous-by-construction cases to annotate")
        return 1

    rng = random.Random(args.seed)
    rng.shuffle(cases)
    chosen = cases[: args.n]

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        for case in chosen:
            item = (case.cart.get("items") or [{}])[0]
            writer.writerow(
                {
                    "case_id": case.id,
                    "instruction": case.instruction,
                    "language": case.language,
                    "item_title": item.get("title", ""),
                    "item_category": item.get("category_path", ""),
                    "item_attributes": json.dumps(
                        item.get("attributes", {}), ensure_ascii=False
                    ),
                    "unit_price_paise": item.get("unit_price_paise", ""),
                    "qty": item.get("qty", ""),
                    "is_subscription": item.get("is_subscription", ""),
                    "cart_total_paise": case.cart.get("total_paise", ""),
                    "delivery_eta": case.cart.get("delivery_eta", ""),
                    "annotator_1": "",
                    "annotator_2": "",
                    "annotator_3": "",
                }
            )

    print(f"wrote {len(chosen)} rows to {args.out}")
    print("Annotators: fill annotator_1..3 with CONFORMS / VIOLATES / AMBIGUOUS.")
    print("Then: python -m bench.annotate.agreement")
    return 0


if __name__ == "__main__":
    sys.exit(main())
