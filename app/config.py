"""Runtime configuration.

Everything the app needs to run is expressed here so that a missing secret is a
startup error with a name attached, rather than a 500 an hour later.

The app is deliberately runnable with no third-party credentials at all: with no
DocuSeal key it falls back to filling the PDF locally, and with no OpenAI key the
voice lane turns itself off and the UI says so. A reviewer who clones this
without any keys still gets a working seller flow and a completed PDF.
"""

from __future__ import annotations

import secrets
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "Loqol Disclosures"
    environment: str = "development"

    # A generated default keeps local dev frictionless. In production the value
    # must be supplied, or every restart would log every agent out - see
    # `check_production_ready`.
    secret_key: str = secrets.token_urlsafe(32)
    database_url: str = "sqlite:///./loqol.db"

    session_cookie: str = "loqol_agent"
    session_ttl_hours: int = 12
    seller_link_ttl_days: int = 14

    # DocuSeal. Test-mode keys are separate from live keys and cost nothing.
    docuseal_api_key: str | None = None
    docuseal_base_url: str = "https://api.docuseal.com"
    docuseal_template_id: int | None = None

    # Voice. Ephemeral client secrets are minted server-side; the standing API
    # key never reaches the browser.
    openai_api_key: str | None = None
    openai_realtime_model: str = "gpt-realtime-2.1-mini"
    openai_reasoning_model: str = "gpt-5-mini"
    #: Hard ceiling on a single browser voice session, in seconds. A public demo
    #: with an unbounded realtime socket is an unbounded bill.
    voice_session_max_seconds: int = 600
    voice_sessions_per_hour: int = 12

    @property
    def voice_enabled(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def docuseal_enabled(self) -> bool:
        return bool(self.docuseal_api_key)

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    def check_production_ready(self) -> list[str]:
        problems: list[str] = []
        if self.is_production:
            import os

            if not os.getenv("SECRET_KEY"):
                problems.append("SECRET_KEY must be set in production (sessions would not survive a restart)")
            if self.database_url.startswith("sqlite"):
                problems.append("DATABASE_URL is SQLite in production (Render's disk is ephemeral)")
        return problems


@lru_cache(maxsize=1)
def settings() -> Settings:
    return Settings()
