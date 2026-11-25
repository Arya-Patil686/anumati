"""Shared fixtures.

Every test runs against a throwaway SQLite file and a freshly generated signing key,
so nothing here reads or writes the dev key in keys/.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

from app import db
from app.services.ledger import LedgerService, LedgerSigner, set_signer


@pytest.fixture
def signer() -> LedgerSigner:
    key = LedgerSigner.generate()
    set_signer(key)
    return key


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(eng)
    db.set_engine(eng)
    yield eng
    db.set_engine(None)  # type: ignore[arg-type]
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    with Session(engine) as s:
        yield s


@pytest.fixture
def ledger(session: Session, signer: LedgerSigner) -> LedgerService:
    return LedgerService(session, signer=signer)


@pytest.fixture
def client(engine: Engine, signer: LedgerSigner) -> Iterator[TestClient]:
    from app.main import app

    with TestClient(app) as c:
        yield c
