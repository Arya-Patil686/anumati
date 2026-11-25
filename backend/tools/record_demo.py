"""Record a real API session into static fixtures for the hosted demo.

GitHub Pages serves files, not Python, so the public console cannot talk to a live
API. Instead of mocking one, this drives the real app in-process against a throwaway
database and freshly generated keys, and writes down exactly what it answered. Every
verdict, hash, signature and benchmark figure in the hosted demo is therefore a real
response from this code — just a recorded one.

Two places deliberately record a branch the committed chain does not take:

* a step-up is recorded with both answers, by snapshotting the database, recording
  REJECT, restoring, then taking APPROVE forward. Both responses are what the API
  really returns from that chain position; only one of them can also be in the chain.
* disputes are recorded against a snapshot and rolled back, so opening sixty-odd
  evidence packs does not bury the flow records in the ledger the viewer reads.

Run with `make demo-fixtures`.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import SQLModel, create_engine

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "web" / "public" / "demo"
PACK_DIR = OUT_DIR / "packs"

# The five buttons on the checkout screen, with the body the console actually posts.
PRESETS = [
    ("white running shoes under 5000, delivered by Friday", "normal", None),
    ("white running shoes under 5000", "faulty", "recurrence_flip"),
    ("white running shoes under 30000", "faulty", "wrong_category_far"),
    ("white running shoes under 5000", "faulty", "hidden_fees"),
    ("5000 se kam ke white running shoes, Friday tak chahiye", "normal", None),
]

# Every fault mode the dropdown offers, on the instruction it defaults to.
FAULTS = [
    "price_over",
    "hidden_fees",
    "wrong_category_near",
    "wrong_category_far",
    "attribute_swap",
    "quantity_off",
    "recurrence_flip",
    "merchant_off_list",
    "delivery_late",
    "substitution_unapproved",
    "dietary_violation",
]
FAULT_INSTRUCTION = "white running shoes under 5000"

# Extra instructions worth having on record: the atta case from the spec's demo, and a
# Hinglish grocery order, so the free-text box is not limited to footwear.
EXTRA = [
    ("5kg atta under 400, veg only", "normal", None),
    ("5kg atta under 400", "faulty", "dietary_violation"),
    ("2 kg aata 400 se kam, agle hafte tak", "normal", None),
]

CLAIMS = [
    "My agent signed me up for a subscription. I only wanted one pair.",
    "I never ordered this — the agent bought the wrong thing.",
    "It never arrived.",
    "I changed my mind, I do not want these.",
]


class Recorder:
    """Holds the fixture list and the client, and keeps the two in step."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.entries: list[dict[str, Any]] = []
        self.packs: dict[str, str] = {}  # dispute_id -> file name

    def get(self, path: str) -> Any:
        response = self.client.get(path)
        return self._record("GET", path, None, response)

    def post(self, path: str, body: dict[str, Any]) -> Any:
        response = self.client.post(path, json=body)
        return self._record("POST", path, body, response)

    def _record(self, method: str, path: str, body: Any, response: Any) -> Any:
        try:
            payload = response.json()
        except ValueError:  # pragma: no cover - every recorded route returns JSON
            raise SystemExit(f"{method} {path} did not return JSON: {response.text[:200]}")
        self.entries.append(
            {
                "method": method,
                "path": path,
                "body": body,
                "status": response.status_code,
                "response": payload,
            }
        )
        if response.status_code >= 400:
            print(f"  ! {method} {path} -> {response.status_code}", file=sys.stderr)
        return payload

    def save_pack(self, dispute_id: str) -> None:
        """Fetch the real evidence PDF and keep it under a content-addressed name."""
        response = self.client.get(f"/disputes/{dispute_id}/pack.pdf")
        if response.status_code != 200:
            print(f"  ! pack.pdf for {dispute_id} -> {response.status_code}", file=sys.stderr)
            return
        digest = hashlib.sha256(response.content).hexdigest()[:16]
        name = f"{digest}.pdf"
        path = PACK_DIR / name
        if not path.exists():
            path.write_bytes(response.content)
        self.packs[dispute_id] = name


def _snapshot(db_path: Path, backup: Path) -> None:
    from app import db

    db.get_engine().dispose()
    shutil.copy2(db_path, backup)


def _restore(db_path: Path, backup: Path) -> None:
    from app import db
    from sqlalchemy import Engine

    db.get_engine().dispose()
    shutil.copy2(backup, db_path)
    engine: Engine = create_engine(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    db.set_engine(engine)


def main() -> None:
    from app import db
    from app.config import ENGINE_VERSION
    from app.services.catalogue import CatalogueService, set_catalogue
    from app.services.ledger import LedgerSigner, set_signer
    from app.services.mandate_builder import MandateSigner, set_mandate_signer

    work = REPO_ROOT / "data" / "demo-recording"
    work.mkdir(parents=True, exist_ok=True)
    db_path = work / "recording.db"
    backup = work / "recording.backup.db"
    db_path.unlink(missing_ok=True)

    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    db.set_engine(engine)

    # Throwaway keys: the recording never touches the dev keys in keys/, and the public
    # half travels with the fixtures so the chain still verifies in the browser.
    set_signer(LedgerSigner.generate())
    set_mandate_signer(MandateSigner.generate())
    set_catalogue(CatalogueService.synthetic())

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    PACK_DIR.mkdir(parents=True, exist_ok=True)
    for stale in PACK_DIR.glob("*.pdf"):
        stale.unlink()

    from app.main import app

    with TestClient(app) as client:
        rec = Recorder(client)
        rec.get("/health")

        flows = [*PRESETS, *[(FAULT_INSTRUCTION, "faulty", f) for f in FAULTS], *EXTRA]
        seen: set[tuple[str, str, str | None]] = set()
        step_ups: list[str] = []

        for instruction, mode, fault in flows:
            if (instruction, mode, fault) in seen:
                continue
            seen.add((instruction, mode, fault))
            label = fault or mode
            print(f"- agent run: {label} :: {instruction[:48]}")
            body = {"instruction": instruction, "mode": mode, "fault": fault}
            run = rec.post("/agent/run", body)
            attempts = run.get("run", {}).get("attempts", [])
            last = attempts[-1] if attempts else {}
            step_up = last.get("step_up")
            if not step_up:
                continue

            verdict_id = step_up["verdict_id"]
            step_ups.append(verdict_id)
            # Record the refusal against a snapshot, then take the approval forward so
            # the committed chain has a payment to dispute.
            _snapshot(db_path, backup)
            rec.post(f"/stepup/{verdict_id}", {"answer": "REJECT"})
            _restore(db_path, backup)
            rec.post(f"/stepup/{verdict_id}", {"answer": "APPROVE"})

        print(f"- {len(step_ups)} step-ups, both answers recorded for each")

        # Disputes: every cart on record against every preset claim, rolled back after,
        # so the ledger the viewer reads stays the record of the flows above.
        ledger = rec.client.get("/ledger?limit=500").json()
        carts = []
        for entry in ledger:
            if entry["kind"] == "cart_created":
                cart_id = str(entry["payload"].get("cart_id", ""))
                if cart_id and cart_id not in carts:
                    carts.append(cart_id)

        _snapshot(db_path, backup)
        for cart_id in carts:
            for claim in CLAIMS:
                body = {"cart_id": cart_id, "claim_text": claim}
                result = rec.post("/disputes", body)
                dispute = result.get("pack", {}).get("dispute", {})
                if dispute.get("dispute_id"):
                    rec.save_pack(dispute["dispute_id"])
        print(f"- {len(carts)} carts x {len(CLAIMS)} claims = {len(carts) * len(CLAIMS)} packs")
        _restore(db_path, backup)

        # Captured last, so they describe the chain the demo actually ships.
        rec.get("/ledger?limit=200")
        verify = rec.get("/ledger/verify")
        metrics = rec.get("/metrics")

        bundle = {
            "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "engine_version": ENGINE_VERSION,
            "chain_ok": verify.get("ok"),
            "entries_checked": verify.get("entries_checked"),
            "metrics_available": metrics.get("available"),
            "packs": rec.packs,
            "entries": rec.entries,
        }

    out = OUT_DIR / "fixtures.json"
    out.write_text(json.dumps(bundle, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    size = out.stat().st_size / 1024
    packs = len(list(PACK_DIR.glob("*.pdf")))
    print(
        f"\nwrote {out.relative_to(REPO_ROOT)} "
        f"({len(rec.entries)} responses, {size:.0f} kB) and {packs} evidence PDFs"
    )
    if not verify.get("ok"):
        raise SystemExit("the recorded chain does not verify — refusing to ship it")
    if not metrics.get("available"):
        print("! no benchmark reports on disk: the Benchmark screen will say so", file=sys.stderr)


if __name__ == "__main__":
    main()
