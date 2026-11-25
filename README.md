# ANUMATI

**A conformance and dispute-evidence engine for AI-agent payments.**

*Anumati* = permission (Hindi/Sanskrit). The engine checks whether what an AI agent
bought is what the user actually permitted.

AI agents can now pay on a user's behalf. AP2, Visa Intelligent Commerce and Mastercard
Agent Pay let an agent buy within limits the user signs in advance, and NPCI is reportedly
preparing a Unified Agent Protocol for UPI. These protocols prove *what the user said*.
But in unattended purchases the agent builds the cart itself, and today's checks only
verify signatures, expiry and price caps. Nothing checks whether the cart actually
**means** what the user asked for.

ANUMATI checks every agent-built cart against the signed instruction before checkout,
returns `CONFORMS` / `VIOLATES` / `AMBIGUOUS` with reasons per constraint, asks the user
only when the call is genuinely unclear, and writes every decision to a tamper-evident
ledger that becomes a ready evidence pack if the purchase is disputed.

Full spec: [`docs/spec.pdf`](docs/spec.pdf). Working rules for contributors: [`CLAUDE.md`](CLAUDE.md).

---

## Status

| Milestone | Scope | State |
|---|---|---|
| **M1** | Scaffold, data models, ledger with verify + tamper test | **done** |
| M2 | Catalogue, taxonomy, Mandate Builder | not started |
| M3 | Conformance engine L1–L3 + aggregator + golden cases | not started |
| M4 | Shopping agent and fault modes | not started |
| M5 | Benchmark (MCB-IN) and baselines | not started |
| M6 | Web console, disputes, evidence PDF, demo | not started |

Modules for later milestones exist as stub files carrying a `TODO(Mn)` marker. Their
tests skip at module level, so the suite stays green.

## Quick start

```bash
make install
make test
make dev
```

`make dev` serves the API on <http://localhost:8000>; interactive docs at `/docs`.
If something already holds that port, use `make dev PORT=8099`.

Via Docker instead:

```bash
docker compose up --build
```

## What M1 gives you

The decision ledger, which everything later writes to.

- **Models** (spec section 5) — `IntentMandate`, `Constraint`, `Cart`, `CartItem`,
  `ConstraintResult`, `Verdict`, `StepUp`, `LedgerEntry`, `DisputeCase`. Money is integer
  paise throughout; a validator rejects floats outright.
- **Persistence** — SQLModel over SQLite, Postgres-ready through `DATABASE_URL`.
- **Ledger** — append-only, SHA-256 hash chain, Ed25519 signature per entry.

### The chain

```
entry_hash = SHA256(prev_hash || canonical_json(payload))
```

`canonical_json` is sorted keys, no spaces, UTF-8, no ASCII escaping — the single byte
encoding this system hashes and signs, so anyone else can re-verify it. The first entry
links to a genesis hash of 64 zeros. Each `entry_hash` is signed with the service's
Ed25519 key; the public key is served at `/health` and on every verify response.

The hash chain shows no earlier entry was changed after the fact. The signature shows the
entries came from this service and not from whoever got hold of the database file. A
dispute pack is worth something only if both hold.

### Endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | Status, engine version, LLM mode, ledger public key |
| `GET /ledger` | Browse entries (`limit`, `offset`) |
| `GET /ledger/{seq}` | One entry |
| `GET /ledger/verify` | Walk the chain → `{ok, broken_at, reason, …}` |

Endpoints for `/mandates`, `/check`, `/agent/run`, `/stepup`, `/disputes` and `/metrics`
are wired in at their milestones.

### Tamper demo

Append entries, edit one payload directly in the database, and verification names the
entry that broke:

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_ledger.py -q
```

The suite covers an edited payload, a payload edited *and* re-hashed (caught by the
signature, since the key is not in the database), and a deleted entry.

## Layout

```
backend/app/
  canonical.py          canonical JSON + hashing — one definition, used everywhere
  config.py             typed settings; nothing reads os.environ directly
  db.py                 engine and session
  models/               domain models (Pydantic) + store.py (SQLModel rows)
  services/ledger.py    append and verify; no update, no delete
  services/engine/      L1 rules, L2 attributes, L3 judge, aggregator   [M3]
  services/llm/         client with record/replay cache                 [M2]
  services/agent/       tool-calling shopping agent and fault modes     [M4]
  api/                  routers
bench/                  MCB-IN generator, operators, baselines, eval     [M5]
data/                   catalogue, taxonomy.yaml, reason_codes.yaml
web/                    React + Vite console                            [M6]
```

Domain models are plain Pydantic and their database rows live in `models/store.py`, so
each file uses one library. `LedgerEntry` is the exception and is itself a table —
for the ledger, the stored bytes *are* the evidence.

## Configuration

Copy `.env.example` to `.env`. In dev an Ed25519 signing key is generated into `keys/`
on first run; `keys/` is git-ignored. Set `LEDGER_SIGNING_KEY_HEX` explicitly for
anything shared, or ledger entries written on one machine will not verify on another.

`LLM_MODE` is `live` / `record` / `replay`. The demo runs in `replay` with no network.

## Limitations

We have no real agent-dispute outcomes, so we make no claim about dispute win rates.
Production calibration needs a partner's transaction and dispute data. The liability
allocation in `data/reason_codes.yaml` is a proposal — current card-scheme rules govern.

## Metrics

Benchmark numbers are never written here by hand. `bench/run_eval.py` writes
`bench/reports/` and nothing else does (CLAUDE.md rule 8).
