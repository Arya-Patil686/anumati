# ANUMATI — architecture

Source of truth is `docs/spec.pdf` (sections 4–5). This file is the working version that
tracks what is actually built.

## End-to-end flow

```
User instruction (English / Hinglish)
      │
      ▼
[A] Mandate Builder ──► signed IntentMandate (stored)                       [M2]
      │
      ▼
[C] Shopping Agent ◄──search / lookup──► [B] Catalogue                      [M4] [M2]
      │ cart
      ▼
[D] Conformance Engine                                                      [M3]
      L1 rules → L2 attributes → L3 LLM judge → aggregator
      │
      ├── CONFORMS  ──► agent creates Cart Mandate → payment
      ├── VIOLATES  ──► blocked; reasons returned to the agent
      └── AMBIGUOUS ──► one-tap step-up to the user

every mandate, verdict, step-up ──► [E] Ledger (SHA-256 chain, Ed25519-signed)   [M1 ✓]
      │
      ▼
dispute claim ──────────────────► [F] Dispute module: evidence pack,         [M6]
      │                               proposed allocation, reason codes
      ▼
[G] Console / phone UI                                                      [M6]

[H] Benchmark & eval harness runs separately and calls [D] like any client.  [M5]
```

## Where it runs

At cart creation, **before payment authorisation** — not inside the authorisation path,
which has to complete in about a second. Deployed as an API by the PSP, the agent
platform or the issuer.

## Component status

| | Component | Responsibility | Milestone |
|---|---|---|---|
| A | **Mandate Builder** | **Instruction → typed `IntentMandate`, read-back, ES256 JWS signing** | **M2 ✓** |
| B | **Catalogue Service** | **Normalised catalogue, search, lookup, category taxonomy** | **M2 ✓** |
| C | Shopping Agent | Tool-calling loop; fault modes inject realistic mistakes | M4 |
| D | Conformance Engine | L1 rules → L2 attributes → L3 judge → aggregator | M3 |
| E | **Decision Ledger** | **Append-only, hash-chained, Ed25519-signed, verify endpoint** | **M1 ✓** |
| F | Dispute & Evidence | Claim + ledger records → evidence pack, allocation, reason codes | M6 |
| G | Web app | Ops console and phone-style step-up screen | M6 |
| H | Benchmark & Eval | Generates MCB-IN, runs baselines and ANUMATI, writes reports | M5 |

## The ledger (built)

```
entry_hash = SHA256(prev_hash || canonical_json(payload))
```

- `canonical_json` — `json.dumps(obj, sort_keys=True, separators=(",", ":"),
  ensure_ascii=False).encode("utf-8")`. One definition, in `app/canonical.py`, used by
  the ledger and by `prompt_hash`.
- First entry's `prev_hash` is `GENESIS_HASH` (64 zeros).
- `signature` is Ed25519 over the ASCII bytes of `entry_hash`.
- `LedgerService` exposes `append` and `verify`. There is deliberately no update or delete.
- `verify()` walks from seq 1 and stops at the first entry that fails, reporting
  `broken_at`. It checks, in order: sequence continuity, the `prev_hash` link, the
  payload hash, the entry hash, and the signature.

Two independent checks, because they fail in different ways. Editing a payload breaks the
hash. Editing a payload *and* recomputing its hash breaks the signature, because the
signing key is not in the database.

### Threat model, honestly stated

The ledger detects after-the-fact edits by anyone who does not hold the signing key. It
does **not** defend against someone who holds the key, and it does not prove *when* an
entry was written — there is no external anchor. For a production deployment the chain
head would be periodically anchored somewhere the service does not control. Worth saying
out loud if a judge asks.

## Mandate Builder (built)

Rule-based, not model-based. The read-back is what the user signs, so every constraint
carries the `source_span` it was read from, and the parse has to be reproducible —
a mandate that drifts between runs would make the ledger worthless as evidence. Putting a
model here as well as in the L3 judge would also make the benchmark circular (rule 6).

Extractors run over the instruction and each returns the constraints it is sure of, with
the span it read them from. English and Hinglish share the same patterns, because real
instructions code-mix inside one sentence. A hedged constraint ("preferably rubber") is
soft; a spending cap never is. When the user states no budget, the builder supplies a
default and says so in the read-back rather than authorising an uncapped agent quietly.

Two signing keys, deliberately. The mandate's ES256 JWS attests *the user authorised
this*; the ledger's Ed25519 signature attests *this service recorded that*. In production
those are different parties, so they are different keys here too.

## Catalogue and taxonomy (built)

Section 7 schema, with rupee floats converted to integer paise once at ingest. A seeded
synthetic catalogue of ~2,000 SKUs is the fallback when a Kaggle dataset's licence or
columns do not work out — same SKUs on every machine, and the structured fields are
constructed, so they can serve as benchmark labels without circularity.

`near_misses` in `data/taxonomy.yaml` is the part that matters for verdict quality.
"trainers" and "sports shoes" resolve to a primary path at low confidence, which is how a
genuinely unclear instruction reaches the AMBIGUOUS band instead of a confident wrong
answer.

## Conformance engine layers (M3)

**L1 — deterministic rules, no ML.** Signature valid; not expired; mandate within its use
budget. Merchant allow/deny; currency; per-item cap; **total including tax and shipping**
against the budget. Quantity; delivery-by versus ETA; refundability; one-off versus
recurring. Output per constraint: satisfied / violated / not-decidable-here.

**L2 — attribute grounding.** Map instruction terms onto the category taxonomy
("running shoes" → Footwear › Sports › Running) using rules plus multilingual embedding
similarity. Compare structured catalogue attributes (colour, size, brand, material, pack
size, veg/non-veg) against constraint values; handle near-matches (off-white vs white,
4.9 kg vs 5 kg). Clear wins and clear losses are decided here.

**L3 — LLM judge, only for what L1–L2 could not settle.** Structured JSON per constraint:
`{status, evidence_fields, rationale, confidence}`, temperature 0. Product text is
**untrusted data**, passed in a separate delimited field with an instruction to treat it
as data. Injection cases live in the test set. Optional self-consistency: three samples,
majority vote, when confidence is borderline.

**Aggregator.**

```python
hard = [r for r in results if r.hard]
if any(r.status == "violated" and r.confidence >= T_VIOLATE for r in hard):
    decision = "VIOLATES"
elif all(r.status == "satisfied" and r.confidence >= T_CONFORM for r in hard):
    decision = "CONFORMS"
else:
    decision = "AMBIGUOUS"
```

Soft constraints never block; an unmet soft constraint is added to the step-up question.
Thresholds are tuned on dev only — target violation recall ≥ 0.95 while keeping the
step-up rate as low as possible — then frozen before the test run.

## Rules that constrain the design

Integer paise everywhere. All LLM calls through one client with a record/replay cache, so
the demo runs offline. Product text never interpolated into instructions. One canonical
JSON definition. Every verdict stores `engine_version`, `model_id`, `prompt_hash`,
`latency_ms`. Benchmark labels never come from the engine's own LLM path. Full list in
`CLAUDE.md`.
