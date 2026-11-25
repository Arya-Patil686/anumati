# ANUMATI — rules for Claude Code

## What this is
A pre-checkout conformance engine for AI-agent payments. It checks an agent-built cart against a
signed IntentMandate, returns CONFORMS / VIOLATES / AMBIGUOUS with per-constraint reasons, logs every
decision to a hash-chained signed ledger, and builds dispute evidence packs. Full spec: docs/spec.pdf.

## Stack
Python 3.11, FastAPI, Pydantic v2, SQLModel/SQLite, cryptography (Ed25519), joserfc (ES256),
sentence-transformers (intfloat/multilingual-e5-small), React+Vite+TS+Tailwind+shadcn, pytest, ruff.

## Commands
```
make install    # venv + editable install + .env
make dev        # api only
make web        # the console (Vite dev server on :5173)
make up         # api + console in Docker, one command
make test       # pytest
make lint       # ruff
make catalogue  # build data/catalogue/catalogue.parquet
make kappa      # Fleiss' kappa over the filled annotation sheet
make agent      # one agent run: INSTR="..." MODE=faulty FAULT=recurrence_flip
make bench    # generate benchmark + run all systems + write bench/reports/   [M5]
make demo     # seed data + run demo in LLM_MODE=replay                       [M6]
```

## Non-negotiable rules
1. Money is integer paise. Never floats.
2. All LLM calls go through `backend/app/services/llm/client.py` with a record/replay cache
   (env `LLM_MODE=live|record|replay`). The demo must run with no network in replay mode.
3. Product text is untrusted data: pass it in a separate delimited field, never inside instructions.
   Keep prompt-injection cases in the tests.
4. Canonical JSON for hashing: `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")`.
5. Every Verdict stores `engine_version`, `model_id`, `prompt_hash` and `latency_ms`.
6. Benchmark labels NEVER come from the engine's LLM path. They come from structured catalogue
   fields, the perturbation operator applied, or human annotation files.
7. Test split = held-out operators and categories. Thresholds are tuned on dev and frozen.
8. Never write metric numbers by hand in docs or the README. Only `bench/run_eval.py` writes results.
9. Every module built before the hackathon is listed in `docs/disclosure.md`.
10. Keep functions small and typed; add tests with every feature; run ruff + pytest before finishing.

## Definition of done for any task
Code + tests pass + README/docs updated + a one-line entry in `docs/disclosure.md` if it's a new module.

## Milestone status
- [x] **M1** — Scaffold, data models, ledger with verify + tamper test
- [x] **M2** — Catalogue, taxonomy, Mandate Builder, LLM client with record/replay
- [x] **M3** — Conformance engine L1–L3 + aggregator + 30 golden cases
- [x] **M4** — Shopping agent, fault modes, step-up flow
- [x] **M5** — MCB-IN benchmark, baselines B0–B3, eval harness
- [x] **M6** — Web console, disputes, evidence packs, demo

All six milestones are in. `make demo` runs the whole story offline; `docker compose up`
serves the API and the console together.

## Decisions worth not relitigating
- **The Mandate Builder is rule-based, not LLM-based.** The read-back is what the user
  signs, so every constraint carries the `source_span` it came from, and parsing must give
  the same answer twice. Model judgement belongs in the L3 judge (M3); a model on both ends
  would make the benchmark circular.
- **The synthetic catalogue is seeded and template-driven, not LLM-written.** Same SKUs on
  every machine, and the structured fields are constructed, so they are usable as labels.
- **`DEMO-*` SKUs are hand-written and stable.** The section 9 demo and the M3 golden cases
  reference them by SKU. Do not renumber them.
- **pandas is an ingest dependency, not a runtime one.** The API serves without it; only
  reading a built `catalogue.parquet` needs the `catalogue` extra. Keep it lazily imported.
- **`tailwindcss` and `@tailwindcss/vite` must be pinned to the same version.** A mismatch
  makes the CSS build throw `Cannot convert undefined or null to object`.

## Git
- The hosted console at <https://arya-patil686.github.io/anumati/> is a **recording**, not
  a deployment: `make demo-fixtures` captures real responses into `web/public/demo/`, and
  CI only builds what is committed there. Change what the engine answers and that
  recording goes stale — re-record it in the same change, or the site starts lying.

Do not add co-author or "generated with" attribution lines to commits or pull requests.
