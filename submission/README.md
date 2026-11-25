# K! Hacks 4.0 — online submission

`HyperNova_ANUMATI_KHacks4.pptx` is the deck to upload. It is the organisers' template
(`template.pptx`) with our content filled in; their title bars, footer logos and
background art are untouched.

Everything is regenerated from source, so a changed number never has to be retyped:

```bash
../.venv/bin/python make_diagrams.py   # the five diagrams, into img/
../.venv/bin/python build_deck.py      # fills template.pptx -> the submission deck
```

Every figure on the Feasibility slide is copied from `bench/reports/results.csv`, which
only `bench/run_eval.py` writes. Re-run `make bench` at the repo root and the numbers
here should be updated from it rather than edited by hand.

## The links on the deck

The cover and the team slide both carry <https://arya-patil686.github.io/anumati/> — the
hosted console. It is a recorded demo: a static host has no API behind it, so the site
replays a session `make demo-fixtures` captured from the real engine. Re-run that, then
rebuild this deck, if the engine's answers change.
