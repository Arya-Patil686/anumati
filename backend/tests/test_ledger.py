"""Ledger tests, including the M1 acceptance criterion.

Acceptance: append 10 entries, then edit the payload of entry 3; /ledger/verify must
return {ok: false, broken_at: 3}.
"""

from __future__ import annotations

import itertools
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlmodel import Session

from app.canonical import canonical_json, sha256_hex
from app.models.common import LedgerKind
from app.models.ledger import GENESIS_HASH, compute_entry_hash
from app.services.ledger import LedgerService, LedgerSigner


def _append_n(ledger: LedgerService, n: int) -> None:
    for i in range(1, n + 1):
        ledger.append(LedgerKind.VERDICT, {"i": i, "note": f"entry {i}", "amount_paise": i * 100})


def _tamper_payload(engine: Engine, seq: int, payload: dict) -> None:
    """Edit a stored payload directly, the way an attacker with the DB file would."""
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE ledger_entries SET payload = :p WHERE seq = :s"),
            {"p": json.dumps(payload), "s": seq},
        )


# --- canonical JSON -----------------------------------------------------------


def test_canonical_json_is_key_order_independent() -> None:
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})


def test_canonical_json_has_no_spaces_and_keeps_unicode() -> None:
    assert canonical_json({"x": "चप्पल", "y": 1}) == '{"x":"चप्पल","y":1}'.encode()


# --- signing ------------------------------------------------------------------


def test_signer_roundtrip() -> None:
    signer = LedgerSigner.generate()
    digest = sha256_hex(b"hello")
    assert signer.verify(digest, signer.sign(digest))


def test_signer_rejects_another_keys_signature() -> None:
    a, b = LedgerSigner.generate(), LedgerSigner.generate()
    digest = sha256_hex(b"hello")
    assert not b.verify(digest, a.sign(digest))


# --- chain construction -------------------------------------------------------


def test_first_entry_links_to_genesis(ledger: LedgerService) -> None:
    entry = ledger.append(LedgerKind.MANDATE_SIGNED, {"mandate_id": "mnd_1"})
    assert entry.seq == 1
    assert entry.prev_hash == GENESIS_HASH
    assert entry.entry_hash == compute_entry_hash(GENESIS_HASH, {"mandate_id": "mnd_1"})


def test_each_entry_links_to_the_one_before(ledger: LedgerService) -> None:
    _append_n(ledger, 5)
    entries = ledger.entries()
    assert [e.seq for e in entries] == [1, 2, 3, 4, 5]
    for prev, cur in itertools.pairwise(entries):
        assert cur.prev_hash == prev.entry_hash


def test_identical_payloads_get_different_hashes(ledger: LedgerService) -> None:
    """Position in the chain is part of the hash, so a replayed payload is distinguishable."""
    a = ledger.append(LedgerKind.VERDICT, {"same": True})
    b = ledger.append(LedgerKind.VERDICT, {"same": True})
    assert a.payload_hash == b.payload_hash
    assert a.entry_hash != b.entry_hash


def test_append_signs_every_entry(ledger: LedgerService, signer: LedgerSigner) -> None:
    _append_n(ledger, 3)
    for entry in ledger.entries():
        assert signer.verify(entry.entry_hash, entry.signature)


# --- verification -------------------------------------------------------------


def test_empty_chain_verifies(ledger: LedgerService) -> None:
    result = ledger.verify()
    assert result.ok
    assert result.broken_at is None
    assert result.entries_checked == 0


def test_intact_chain_verifies(ledger: LedgerService) -> None:
    _append_n(ledger, 10)
    result = ledger.verify()
    assert result.ok
    assert result.broken_at is None
    assert result.entries_checked == 10
    assert result.head_hash == ledger.head_hash()


def test_editing_a_payload_breaks_verification_at_that_entry(
    ledger: LedgerService, session: Session, engine: Engine
) -> None:
    """M1 acceptance: 10 entries, edit entry 3, verify -> {ok: false, broken_at: 3}."""
    _append_n(ledger, 10)
    assert ledger.verify().ok

    _tamper_payload(engine, 3, {"i": 3, "note": "entry 3", "amount_paise": 999_999})
    session.expire_all()

    result = ledger.verify()
    assert result.ok is False
    assert result.broken_at == 3


@pytest.mark.parametrize("seq", [1, 5, 10])
def test_tampering_is_caught_wherever_it_happens(
    ledger: LedgerService, session: Session, engine: Engine, seq: int
) -> None:
    _append_n(ledger, 10)
    _tamper_payload(engine, seq, {"i": seq, "note": "rewritten"})
    session.expire_all()

    result = ledger.verify()
    assert result.ok is False
    assert result.broken_at == seq


def test_forged_signature_is_caught(
    ledger: LedgerService, session: Session, engine: Engine
) -> None:
    """A payload edited *and* re-hashed still fails, because the key is not available."""
    _append_n(ledger, 5)
    forged = {"i": 2, "note": "rewritten"}
    entry = ledger.get(2)
    assert entry is not None
    new_hash = compute_entry_hash(entry.prev_hash, forged)

    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE ledger_entries SET payload = :p, payload_hash = :ph, entry_hash = :eh "
                "WHERE seq = :s"
            ),
            {
                "p": json.dumps(forged),
                "ph": sha256_hex(canonical_json(forged)),
                "eh": new_hash,
                "s": 2,
            },
        )
    session.expire_all()

    result = ledger.verify()
    assert result.ok is False
    assert result.broken_at == 2
    assert result.reason is not None
    assert "signature" in result.reason


def test_deleting_an_entry_is_caught(
    ledger: LedgerService, session: Session, engine: Engine
) -> None:
    _append_n(ledger, 6)
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM ledger_entries WHERE seq = 4"))
    session.expire_all()

    result = ledger.verify()
    assert result.ok is False
    assert result.broken_at == 4


# --- API ----------------------------------------------------------------------


def test_api_lists_and_verifies(client: TestClient, signer: LedgerSigner) -> None:
    from app.db import get_engine

    with Session(get_engine()) as s:
        _append_n(LedgerService(s, signer=signer), 10)

    listed = client.get("/ledger").json()
    assert [e["seq"] for e in listed] == list(range(1, 11))

    verified = client.get("/ledger/verify").json()
    assert verified["ok"] is True
    assert verified["broken_at"] is None


def test_api_reports_broken_at_after_tamper(client: TestClient, signer: LedgerSigner) -> None:
    from app.db import get_engine

    engine = get_engine()
    with Session(engine) as s:
        _append_n(LedgerService(s, signer=signer), 10)

    _tamper_payload(engine, 3, {"i": 3, "note": "entry 3", "amount_paise": 999_999})

    body = client.get("/ledger/verify").json()
    assert body["ok"] is False
    assert body["broken_at"] == 3


def test_api_404_for_unknown_entry(client: TestClient) -> None:
    assert client.get("/ledger/4242").status_code == 404


def test_health(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert len(body["ledger_public_key"]) == 64
