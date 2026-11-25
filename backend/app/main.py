"""ANUMATI API.

M1 serves the ledger. Routers for mandates, check, agent, disputes and metrics are
wired in as their milestones land.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    routes_agent,
    routes_check,
    routes_disputes,
    routes_ledger,
    routes_mandates,
    routes_metrics,
)
from app.config import ENGINE_VERSION, settings
from app.db import create_db_and_tables
from app.services.ledger import get_signer


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    create_db_and_tables()
    get_signer()  # fail fast if the signing key is unusable
    yield


app = FastAPI(
    title="ANUMATI",
    description="Conformance and dispute-evidence engine for AI-agent payments",
    version=ENGINE_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(routes_mandates.router)
app.include_router(routes_check.router)
app.include_router(routes_agent.router)
app.include_router(routes_ledger.router)
app.include_router(routes_disputes.router)
app.include_router(routes_metrics.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {
        "status": "ok",
        "engine_version": ENGINE_VERSION,
        "llm_mode": settings.llm_mode,
        "ledger_public_key": get_signer().public_key_hex,
    }
