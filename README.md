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
| **M2** | Catalogue, taxonomy, Mandate Builder, LLM record/replay | **done** |
| **M3** | Conformance engine L1–L3 + aggregator + 30 golden cases | **done** |
| **M4** | Shopping agent, 11 fault modes, step-up flow | **done** |
| M5 | Benchmark (MCB-IN) and baselines | not started |
| M6 | Web console, disputes, evidence PDF, demo | not started |

Modules for later milestones exist as stub files carrying a `TODO(Mn)` marker. Their
tests skip at module level, so the suite stays green.

## Quick start

```bash
make install
make catalogue    # builds the product catalogue (synthetic unless a CSV is configured)
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
| `POST /check` | `{mandate_id, cart}` → verdict, reasons, step-up question |
| `POST /agent/run` | Instruction → agent builds a cart → verdict → act |
| `POST /stepup/{verdict_id}` | The one tap: `APPROVE` or `REJECT`, logged |
| `POST /mandates` | Instruction → draft mandate + plain-language read-back |
| `POST /mandates/{id}/sign` | ES256 JWS signature + first ledger entry |
| `GET /mandates/{id}` | Fetch a mandate and its read-back |

Endpoints for `/check`, `/agent/run`, `/stepup`, `/disputes` and `/metrics` are wired in
at their milestones.

### Tamper demo

Append entries, edit one payload directly in the database, and verification names the
entry that broke:

```bash
cd backend && ../.venv/bin/python -m pytest tests/test_ledger.py -q
```

The suite covers an edited payload, a payload edited *and* re-hashed (caught by the
signature, since the key is not in the database), and a deleted entry.

## What M2 adds

**Mandate Builder** — an instruction becomes a typed `IntentMandate`. It is **rule-based,
not model-based**, on purpose: the read-back is what the user signs, so every constraint
carries the `source_span` it was read from, and the same sentence must parse the same way
every time. English and Hinglish are handled by the same patterns, because real
instructions code-mix inside one sentence:

```
"5000 se kam ke white running shoes, Friday tak chahiye, no substitutes"

  BUDGET_TOTAL  lte  500000                     span='5000 se kam'
  ATTRIBUTE     eq   {colour: white}            span='white'
  CATEGORY      eq   Footwear > Sports > Running  span='running shoes'
  DELIVERY_BY   lte  2026-11-27                 span='Friday tak'
  SUBSTITUTION  is_false  False                 span='no substitutes'
```

All 14 constraint types parse. A constraint the user hedged ("preferably rubber") is
marked soft and will not block a purchase. A spending cap never is. When no budget is
stated the builder supplies a default and says so in the read-back, rather than
authorising an uncapped agent quietly.

**Read-back** — second person, plain English, quoting the user's own words. This text is
what the user agrees to, and at dispute time it is the record of what they understood.

**Signing** — ES256 JWS over the mandate's canonical payload, with a key separate from the
ledger's Ed25519 key. They attest different things: this one that *the user authorised
this*, the ledger's that *this service recorded that*. In production they belong to
different parties.

**Catalogue** — normalises a Kaggle product CSV to the section 7 schema, with a
deterministic synthetic fallback of ~2,000 SKUs so nothing blocks on a download. Rupee
floats become integer paise once, at ingest, and never reappear. A handful of stable
`DEMO-*` SKUs back the demo and the M3 golden cases: a white running shoe, an off-white
one at ₹4,799, a treadmill, a subscription variant, a 4.9 kg atta pack, and one product
whose description carries a prompt-injection payload.

**Taxonomy** — `data/taxonomy.yaml` maps what people type onto category paths, Hinglish
included ("daudne wale shoes", "sarson ka tel"). Terms listed under `near_misses`
("trainers", "sports shoes") resolve with low confidence on purpose — that is how a
genuinely unclear case reaches the AMBIGUOUS band instead of a confident wrong answer.

**LLM client** — every model call goes through one client with a record/replay cache
(`LLM_MODE=live|record|replay`). A cache miss in replay mode raises rather than reaching
the network, because a silent network call during the demo is exactly the failure we are
trying to avoid. Untrusted product text is passed in a delimited data block with an
injection guard, never inside the instruction.

## Layout

```
backend/app/
  canonical.py          canonical JSON + hashing — one definition, used everywhere
  config.py             typed settings; nothing reads os.environ directly
  db.py                 engine and session
  models/               domain models (Pydantic) + store.py (SQLModel rows)
  services/ledger.py    append and verify; no update, no delete
  services/mandate_builder.py   instruction -> IntentMandate, read-back, ES256 signing
  services/catalogue.py         schema, CSV ingest, lookup and search
  services/catalogue_synth.py   seeded synthetic catalogue + stable DEMO-* SKUs
  services/engine/      taxonomy; L1 rules, L2 attributes, L3 judge, aggregator  [M3]
  services/llm/         client with record/replay cache
  services/agent/       tool-calling shopping agent and fault modes     [M4]
  api/                  routers
bench/                  MCB-IN generator, operators, baselines, eval     [M5]
data/                   catalogue, taxonomy.yaml, reason_codes.yaml
web/                    React + Vite console                            [M6]
```

Two deliberate departures from the layout in spec §11.1. Domain models are plain Pydantic
and their database rows live in `models/store.py`, so each file uses one library;
`LedgerEntry` is the exception and is itself a table, because for the ledger the stored
bytes *are* the evidence. And catalogue *generation* sits in `catalogue_synth.py` apart
from catalogue *serving*, which keeps both modules to one job.

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
