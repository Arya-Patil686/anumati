# Human annotation — the ambiguous set

The boundary cases are what this project is about, so their labels must not come from
whoever wrote the generator, and certainly not from the engine.

## Process

1. `python -m bench.annotate.sample --cases data/bench/cases.jsonl` fills
   `annotation_sheet.csv` with the ambiguous-by-construction cases, shuffled, with the
   gold column blank.
2. Three annotators label each row independently with `CONFORMS`, `VIOLATES` or
   `AMBIGUOUS`. They see the instruction and the cart's structured fields — never the
   engine's verdict.
3. `python -m bench.annotate.agreement` reports Fleiss' kappa and writes the agreed gold
   labels.

A case where the three annotators split three ways has **no majority**, and its gold
label is `AMBIGUOUS`. That is not a failure of the process. If three careful people
cannot agree, a system that returns a confident yes or no is guessing, and the honest
answer is to ask the user.

Report kappa in the results. A low kappa on this subset is itself a finding: it says the
boundary is genuinely contested, which is the argument for having an AMBIGUOUS band at all.
