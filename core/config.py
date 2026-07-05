"""
core/config.py
Centralised settings via pydantic-settings v2.
All secrets come from environment / .env — never hardcoded.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Core ────────────────────────────────────────────────────────
    app_name: str = "DevPulse"
    app_version: str = "1.0.0"
    environment: Literal["development", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    # ── Paths ───────────────────────────────────────────────────────
    tools_dir: Path = Path("tools")
    db_path: Path = Path("devpulse.db")

    # ── API security ────────────────────────────────────────────────
    # Master key used to create/revoke child API keys via /v1/keys.
    # Set in .env — never hardcode.
    admin_api_key: str = Field(..., min_length=32)

    # ── Rate limiting ────────────────────────────────────────────────
    rate_limit_per_minute: int = Field(default=60, ge=1, le=1000)
    rate_limit_burst: int = Field(default=10, ge=1, le=100)

    # ── Request constraints ──────────────────────────────────────────
    max_request_body_bytes: int = Field(default=65_536, ge=1024)  # 64 KB

    # ── LLM (Groq) ──────────────────────────────────────────────────
    groq_api_key: str = Field(..., min_length=10)
    groq_model: str = "openai/gpt-oss-120b"
    groq_temperature: float = Field(default=0.0, ge=0.0, le=2.0)
    agent_max_turns: int = Field(default=8, ge=1, le=20)

    # ── HTTP executor defaults ───────────────────────────────────────
    http_timeout_seconds: float = Field(default=10.0, ge=1.0, le=60.0)
    http_max_response_bytes: int = Field(default=512_000, ge=1024)  # 512 KB

    # ── SSRF protection ─────────────────────────────────────────────
    # Comma-separated allowed URL prefixes for http_get/http_post executors.
    # Empty string = allow all (dev mode). Set explicit values in production.
    allowed_url_prefixes: str = ""

    @field_validator("tools_dir")
    @classmethod
    def tools_dir_must_exist(cls, v: Path) -> Path:
        v.mkdir(parents=True, exist_ok=True)
        return v

    @property
    def allowed_prefixes_list(self) -> list[str]:
        if not self.allowed_url_prefixes.strip():
            return []
        return [p.strip() for p in self.allowed_url_prefixes.split(",") if p.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached singleton — safe to call anywhere without re-parsing .env."""
    return Settings()  # type: ignore[call-arg]
