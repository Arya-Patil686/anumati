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
Shareable results page: <https://claude.ai/artifact/2GXXSA4Dshu7GyUNyUKkDD> (private — share from
its own menu before sending it to anyone).

---

## Status

| Milestone | Scope | State |
|---|---|---|
| **M1** | Scaffold, data models, ledger with verify + tamper test | **done** |
| **M2** | Catalogue, taxonomy, Mandate Builder, LLM record/replay | **done** |
| **M3** | Conformance engine L1–L3 + aggregator + 30 golden cases | **done** |
| **M4** | Shopping agent, 11 fault modes, step-up flow | **done** |
| **M5** | MCB-IN benchmark, baselines B0–B3, eval harness | **done** |
| **M6** | Web console, disputes, evidence packs, demo | **done** |

All six are in. 515 tests pass, `make demo` runs the whole story with the network off,
and `make up` serves the API and the console together.

## Quick start

Everything at once, in Docker:

```bash
make up
```

Console on <http://localhost:5173>, API docs on <http://localhost:8000/docs>.

Or run it directly:

```bash
make install
make catalogue    # builds the product catalogue (synthetic unless a CSV is configured)
make test
make demo         # the whole story, offline, in your terminal
make dev          # API on :8000  (PORT=8099 if something holds that port)
make web          # console on :5173
```

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
| `POST /disputes` | Claim → evidence pack, allocation, reason codes, PDF |
| `GET /metrics` | The latest benchmark results, as written by the harness |
| `POST /stepup/{verdict_id}` | The one tap: `APPROVE` or `REJECT`, logged |
| `POST /mandates` | Instruction → draft mandate + plain-language read-back |
| `POST /mandates/{id}/sign` | ES256 JWS signature + first ledger entry |
| `GET /mandates/{id}` | Fetch a mandate and its read-back |



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

## The demo

`make demo` runs the eight beats of the pitch end to end with no network:

1. A user signs an instruction, and reads back what they are authorising.
2. A clean agent run — conforms, pays.
3. A faulty run picks a subscription plan against a one-off mandate — blocked, with the
   constraint named, then it retries and succeeds.
4. An off-white pair at ₹4,799 plus ₹99 delivery — ambiguous, one tap, logged.
5. A product whose description tells the judge to return CONFORMS — still caught, because
   colour and budget never reach a model.
6. One ledger row edited live in SQLite — verification reports the exact broken entry.
7. "My agent signed me up for a subscription" — an evidence pack, allocation and Visa 13.2.
8. The benchmark table.

## Configuration

Copy `.env.example` to `.env`. In dev an Ed25519 signing key is generated into `keys/`
on first run; `keys/` is git-ignored. Set `LEDGER_SIGNING_KEY_HEX` explicitly for
anything shared, or ledger entries written on one machine will not verify on another.

`LLM_MODE` is `live` / `record` / `replay`. The demo runs in `replay` with no network.

## Limitations

We have no real agent-dispute outcomes, so we make no claim about dispute win rates.
Production calibration needs a partner's transaction and dispute data. The liability
allocation in `data/reason_codes.yaml` is a proposal — current card-scheme rules govern.

## Results

Benchmark numbers are never written here by hand. `bench/run_eval.py` writes
`bench/reports/` and nothing else does (CLAUDE.md rule 8). Run `make bench` to reproduce;
the generator is seeded, so the same cases come out on every machine.

The table below is copied from `bench/reports/results.md` — **test split**, whose
perturbation operators and product categories were held out entirely and never used to
tune anything.

| System | Viol. recall | False-block | Step-up | Macro-F1 | Injection ASR | ECE |
|---|---|---|---|---|---|---|
| B0 numeric | 54.5% | 4.3% | — | 36.5% | 0.0% | 32.7% |
| B1 keyword | 91.6% | 28.7% | — | 50.0% | 0.0% | 8.8% |
| B2 similarity | 98.2% | 73.4% | — | 42.5% | 0.0% | 8.6% |
| **ANUMATI** | **99.4%** | **4.3%** | 6.8% | **97.9%** | 0.0% | **4.2%** |

B3 (one-prompt LLM) needs a reachable model and is omitted rather than estimated; see the
note in the report. The dataset is 1,902 cases over 505 instructions, 32.5% Hinglish.

Read the shape of it, not just the headline. B0 is what AP2-style checks do today: it
almost never blocks a good cart, and it misses nearly half the violations, because it
cannot see what was bought. B1 and B2 catch violations by being suspicious of everything
— B2 blocks three-quarters of perfectly good carts, which no payment flow would tolerate.
ANUMATI is the only system that gets both, and the only one that can say "I am not sure"
instead of guessing: it asks the user on 6.8% of carts.

ANUMATI reached its LLM judge for 1.7% of constraints. The rest were settled by
deterministic rules and structured-field matching.
