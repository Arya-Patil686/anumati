"""Database engine and session handling.

SQLite for the hackathon; the URL is the only thing that has to change for Postgres.
`check_same_thread=False` is needed because FastAPI serves requests on a threadpool.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings

# Importing the model modules registers their tables on SQLModel.metadata.
from app.models import ledger as _ledger  # noqa: F401
from app.models import store as _store  # noqa: F401

_engine: Engine | None = None


def _ensure_parent_dir(database_url: str) -> None:
    prefix = "sqlite:///"
    if database_url.startswith(prefix):
        path = Path(database_url[len(prefix) :])
        if path.parent and str(path.parent) not in ("", "."):
            path.parent.mkdir(parents=True, exist_ok=True)


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        _ensure_parent_dir(settings.database_url)
        connect_args = (
            {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
        )
        _engine = create_engine(settings.database_url, echo=False, connect_args=connect_args)
    return _engine


def set_engine(engine: Engine) -> None:
    """Point the app at another engine. Used by the tests to run against a temp file."""
    global _engine
    _engine = engine


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(get_engine())


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with Session(get_engine()) as session:
        yield session
