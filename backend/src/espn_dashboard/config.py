"""Runtime configuration.

Every secret arrives through the environment. In Cloud Run these are wired to
Secret Manager versions; locally they come from ``backend/.env``.
"""

from __future__ import annotations

import functools

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Secrets -----------------------------------------------------------------
    credential_encryption_key: str = ""
    jwt_secret: str = ""
    sync_token: str = ""

    # Storage -----------------------------------------------------------------
    credential_store: str = "memory"  # "firestore" | "memory"
    gcp_project: str = ""
    firestore_collection: str = "espn_credentials"
    bigquery_dataset: str = "espn_fantasy"

    # Behaviour ---------------------------------------------------------------
    allowed_origins: str = "http://localhost:8080"
    espn_cache_ttl_seconds: int = 60
    jwt_ttl_hours: int = 720
    espn_request_timeout_seconds: float = 15.0

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]


@functools.lru_cache
def get_settings() -> Settings:
    return Settings()
