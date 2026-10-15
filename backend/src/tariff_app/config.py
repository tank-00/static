"""Application configuration (environment prefix ``TARIFF_``)."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TARIFF_", extra="ignore")

    data_dir: Path = _BACKEND_DIR / ".data"
    database_url: str = ""
    ai_model: str = "claude-opus-5-5"
    max_upload_mb: int = 25
    serve_frontend_dist: str = ""

    @property
    def resolved_database_url(self) -> str:
        return self.database_url or f"sqlite:///{self.data_dir / 'tariff.sqlite3'}"

    @property
    def ai_available(self) -> bool:
        return bool(os.environ.get("ANTHROPIC_API_KEY"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
