"""Shared test doubles.

``FakeESPNClient`` stands in for the network. It answers with the fixture league
payload and can be told to fail, so the tests can exercise expired cookies and
ESPN outages without touching ESPN.
"""

from __future__ import annotations

from typing import Any

import pytest

from espn_dashboard.config import Settings, get_settings
from espn_dashboard.crypto import generate_key
from espn_dashboard.espn.client import ESPNCredentials
from espn_dashboard.espn.errors import ESPNAuthError, ESPNError
from espn_dashboard.service import DashboardService
from espn_dashboard.store import MemoryCredentialStore

from .fixtures import SWID, fan_api_payload, league_payload

VALID_COOKIE = "AEB-valid-espn-s2-cookie"


class FakeESPNClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.discovery_error: ESPNError | None = None
        self.fetch_error: ESPNError | None = None
        self.private = True
        self.payload = league_payload(include_transactions=True)

    async def fetch_league(
        self,
        season: int,
        league_id: str,
        views: list[str],
        *,
        credentials: ESPNCredentials | None = None,
        scoring_period: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        if self.fetch_error:
            raise self.fetch_error
        if credentials and credentials.espn_s2 != VALID_COOKIE:
            raise ESPNAuthError("ESPN rejected these credentials")
        self.calls.append(
            {
                "season": season,
                "league_id": league_id,
                "views": list(views),
                "scoring_period": scoring_period,
                "headers": headers,
            }
        )
        return self.payload

    async def league_is_private(self, season: int, league_id: str) -> bool:
        return self.private

    async def discover_leagues(
        self, credentials: ESPNCredentials, *, season: int
    ) -> list[dict[str, Any]]:
        if credentials.espn_s2 != VALID_COOKIE:
            raise ESPNAuthError("ESPN rejected these credentials")
        if self.discovery_error:
            raise self.discovery_error
        from espn_dashboard.espn.client import _parse_fan_preferences

        return _parse_fan_preferences(fan_api_payload(), season=season)


@pytest.fixture
def settings() -> Settings:
    return Settings(
        credential_encryption_key=generate_key(),
        jwt_secret="test-jwt-secret",
        sync_token="test-sync-token",
        credential_store="memory",
        espn_cache_ttl_seconds=60,
        jwt_ttl_hours=1,
        allowed_origins="http://localhost:8080",
    )


@pytest.fixture
def espn() -> FakeESPNClient:
    return FakeESPNClient()


@pytest.fixture
def store() -> MemoryCredentialStore:
    return MemoryCredentialStore()


@pytest.fixture
def service(settings, store, espn) -> DashboardService:
    return DashboardService(settings, store, espn)


@pytest.fixture
async def connected(service) -> DashboardService:
    """A service with one account already connected to league 123456."""
    await service.connect(swid=SWID, espn_s2=VALID_COOKIE)
    return service


@pytest.fixture(autouse=True)
def _reset_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
