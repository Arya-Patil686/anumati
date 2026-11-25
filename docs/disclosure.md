# Pre-built component disclosure

K! Hacks 4.0 Round 2 uses **fresh problem statements**, and bringing an existing project
without disclosure means disqualification. Everything in this repository was built before
the 17–18 Dec hackathon and is listed here. If any of it is reused in Chennai, this file
goes to the organisers.

Keep it current from day one — add a line in the same change that adds a module
(CLAUDE.md rule 9).

| Module | Path | Built | What it does |
|---|---|---|---|
| Canonical JSON + hashing | `backend/app/canonical.py` | 25 Nov 2026 | Sorted-key UTF-8 JSON encoding; SHA-256 helpers for payload and prompt hashes |
| Domain models | `backend/app/models/` | 25 Nov 2026 | Pydantic v2 models from spec section 5: mandate, cart, verdict, ledger, dispute |
| Persistence rows | `backend/app/models/store.py` | 25 Nov 2026 | SQLModel tables for mandates, carts, verdicts, step-ups, disputes |
| Settings | `backend/app/config.py` | 25 Nov 2026 | Typed settings loaded from `.env` |
| Database | `backend/app/db.py` | 25 Nov 2026 | SQLite engine and session handling, Postgres-ready |
| **Decision ledger** | `backend/app/services/ledger.py` | 25 Nov 2026 | Append-only SHA-256 hash chain, Ed25519 signing, chain verification |
| Ledger API | `backend/app/api/routes_ledger.py` | 25 Nov 2026 | `GET /ledger`, `GET /ledger/{seq}`, `GET /ledger/verify` |
| API app | `backend/app/main.py` | 25 Nov 2026 | FastAPI app, CORS, `/health` |
| Ledger tests | `backend/tests/test_ledger.py` | 25 Nov 2026 | Chain, signing and tamper-detection tests |
| Packaging | `Makefile`, `docker-compose.yml`, `backend/Dockerfile` | 25 Nov 2026 | One-command run |

## Not yet built

Mandate Builder, catalogue and taxonomy, conformance engine (L1–L3 and aggregator),
shopping agent and fault modes, LLM client and record/replay cache, benchmark harness and
baselines, dispute and evidence modules, web console. Stub files carry a `TODO(Mn)` marker.

## Third-party components

| Component | Licence | Use |
|---|---|---|
| FastAPI, Pydantic, SQLModel, Uvicorn | MIT / BSD | API and models |
| `cryptography` | Apache-2.0 / BSD | Ed25519 signing |
| Product catalogue datasets (Kaggle) | per-dataset — **check before use** | Benchmark catalogue (M2) |
| AP2 reference implementation | Apache-2.0 | Naming reference only; the SDK is not vendored |

Nothing from AP2 is copied into this repository; `IntentMandate` / `CartMandate` naming
follows the public protocol so the output is recognisable.
