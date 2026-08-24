"""Unauthenticated public league viewing.

The security property under test: public routes are pinned to the configured
league and cannot be steered anywhere else. Everything here runs without a
session token, because that is the whole point of the mode.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from espn_dashboard import main
from espn_dashboard.config import Settings, get_settings
from espn_dashboard.crypto import generate_key
from espn_dashboard.service import DashboardService, PublicLeagueError, current_season

from .conftest import VALID_COOKIE
from .fixtures import SWID

PUBLIC_LEAGUE = "123456"


def settings_with_public(**overrides) -> Settings:
    return Settings(
        credential_encryption_key=generate_key(),
        jwt_secret="test-jwt-secret",
        sync_token="test-sync-token",
        credential_store="memory",
        public_league_id=PUBLIC_LEAGUE,
        public_league_season=current_season(),
        **overrides,
    )


@pytest.fixture
def public_settings() -> Settings:
    return settings_with_public()


@pytest.fixture
async def public_service(public_settings, store, espn) -> DashboardService:
    service = DashboardService(public_settings, store, espn)
    await service.connect(swid=SWID, espn_s2=VALID_COOKIE)
    return service


@pytest.fixture
def public_client(public_settings, public_service):
    main.set_service(public_service)
    main.app.dependency_overrides[get_settings] = lambda: public_settings
    with TestClient(main.app) as client:
        yield client
    main.app.dependency_overrides.clear()
    main.set_service(None)


# --- the mode is off unless configured ---------------------------------------


def test_public_is_off_by_default(client):
    """Nothing is ever published by accident."""
    body = client.get("/api/public/config").json()
    assert body == {"enabled": False}


def test_public_routes_404_when_not_configured(client):
    for path in ("/api/public/overview", "/api/public/standings", "/api/public/teams"):
        response = client.get(path)
        assert response.status_code == 404
        assert response.json()["error"] == "public_league_unavailable"


# --- serving the configured league -------------------------------------------


def test_public_config_reports_the_published_league(public_client):
    body = public_client.get("/api/public/config").json()
    assert body["enabled"] is True
    assert body["league_id"] == PUBLIC_LEAGUE


PUBLIC_VIEWS = [
    ("/api/public/overview", "standings"),
    ("/api/public/standings", "standings"),
    ("/api/public/matchups", "matchups"),
    ("/api/public/teams", "teams"),
    ("/api/public/transactions", "transactions"),
    ("/api/public/power-rankings", "power_rankings"),
]


@pytest.mark.parametrize(("path", "key"), PUBLIC_VIEWS)
def test_every_public_view_serves_without_a_token(public_client, path, key):
    response = public_client.get(path)
    assert response.status_code == 200, response.text
    assert response.json()[key]


def test_public_roster_serves_without_a_token(public_client):
    body = public_client.get("/api/public/teams/1/roster").json()
    assert body["roster"]["name"] == "Gridiron Giants"


def test_public_matchups_accept_a_week(public_client):
    assert public_client.get("/api/public/matchups?week=1").json()["week"] == 1


# --- the security properties --------------------------------------------------


def test_public_routes_take_no_league_id(public_client):
    """There is no parameter to point these at another league, so there is no
    way to walk from the published league to someone else's private one."""
    for path, _ in PUBLIC_VIEWS:
        assert "{" not in path
    assert public_client.get("/api/public/999999/standings").status_code == 404


def test_public_responses_never_carry_credential_material(public_client):
    for path, _ in PUBLIC_VIEWS:
        body = public_client.get(path).text
        assert VALID_COOKIE not in body
        assert SWID not in body, "the borrowed account's SWID must not be exposed"


def test_public_responses_ask_not_to_be_indexed(public_client):
    response = public_client.get("/api/public/standings")
    assert response.headers["x-robots-tag"] == "noindex, nofollow"


def test_indexing_can_be_allowed_explicitly(store, espn):
    settings = settings_with_public(public_league_noindex=False)
    service = DashboardService(settings, store, espn)
    main.set_service(service)
    main.app.dependency_overrides[get_settings] = lambda: settings
    try:
        with TestClient(main.app) as client:
            assert "x-robots-tag" not in client.get("/api/public/config").headers
    finally:
        main.app.dependency_overrides.clear()
        main.set_service(None)


def test_authenticated_routes_are_unaffected_by_public_mode(public_client):
    """Turning on public viewing must not open the per-account endpoints."""
    assert public_client.get("/api/me").status_code == 401
    assert public_client.delete("/api/me").status_code == 401
    assert public_client.get(f"/api/leagues/{PUBLIC_LEAGUE}/standings").status_code == 401


# --- borrowed credentials -----------------------------------------------------


async def test_public_view_needs_a_connected_account(public_settings, store, espn):
    """The public view has no cookies of its own — it borrows a member's."""
    service = DashboardService(public_settings, store, espn)
    with pytest.raises(PublicLeagueError, match="no connected account"):
        await service.public_view("standings")


async def test_owner_is_reresolved_after_a_disconnect(public_settings, store, espn):
    service = DashboardService(public_settings, store, espn)
    await service.connect(swid=SWID, espn_s2=VALID_COOKIE)
    assert service.public_owner_swid() == SWID

    service.disconnect(SWID)
    with pytest.raises(PublicLeagueError):
        service.public_owner_swid()


async def test_owner_lookup_is_memoized(public_settings, store, espn, monkeypatch):
    service = DashboardService(public_settings, store, espn)
    await service.connect(swid=SWID, espn_s2=VALID_COOKIE)

    scans = {"count": 0}
    original = store.list_all

    def counting_list_all():
        scans["count"] += 1
        return original()

    monkeypatch.setattr(store, "list_all", counting_list_all)
    for _ in range(5):
        service.public_owner_swid()
    assert scans["count"] == 1, "public requests must not rescan the credential store"
