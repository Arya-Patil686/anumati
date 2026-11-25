"""Typed settings, loaded from the environment / .env.

Nothing in the codebase reads os.environ directly — it goes through `settings`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]

ENGINE_VERSION = "0.1.0"

LLMMode = Literal["live", "record", "replay"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Service
    anumati_env: str = "dev"
    api_host: str = "0.0.0.0"
    api_port: int = 8000
    database_url: str = "sqlite:///./anumati.db"

    # Ledger signing
    ledger_signing_key_hex: str = ""
    ledger_key_path: Path = REPO_ROOT / "keys" / "ledger_ed25519.key"

    # Mandate signing (M2)
    mandate_key_path: Path = REPO_ROOT / "keys" / "mandate_es256.pem"

    # LLM
    llm_mode: LLMMode = "replay"
    llm_cache_path: Path = REPO_ROOT / "data" / "llm_cache.sqlite"
    llm_provider: str = "openai_compatible"
    llm_base_url: str = "http://localhost:11434/v1"
    llm_model_id: str = "qwen2.5:7b-instruct"
    llm_api_key: str = ""
    llm_timeout_s: int = 30

    # Embeddings
    embedding_model: str = "intfloat/multilingual-e5-small"

    # Catalogue (M2)
    catalogue_csv_path: Path = REPO_ROOT / "data" / "raw" / "bigbasket_products.csv"
    catalogue_parquet_path: Path = REPO_ROOT / "data" / "catalogue" / "catalogue.parquet"

    # Engine thresholds (M3) — tuned on dev, then frozen
    t_violate: float = 0.70
    t_conform: float = 0.70


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
