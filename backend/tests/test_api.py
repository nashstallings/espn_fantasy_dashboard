"""HTTP contract: status codes, the auth boundary, and what must never be echoed."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from espn_dashboard import main
from espn_dashboard.auth import issue_token
from espn_dashboard.config import get_settings
from espn_dashboard.espn.errors import ESPNAuthError, ESPNUnavailableError

from .conftest import VALID_COOKIE
from .fixtures import SWID


@pytest.fixture
def client(settings, service):
    main.set_service(service)
    main.app.dependency_overrides[get_settings] = lambda: settings
    with TestClient(main.app) as test_client:
        test_client.settings = settings
        yield test_client
    main.app.dependency_overrides.clear()
    main.set_service(None)


def auth_header(settings, swid=SWID):
    token, _ = issue_token(swid, settings.jwt_secret, ttl_hours=1)
    return {"Authorization": f"Bearer {token}"}


def connect(client, **overrides):
    body = {"swid": SWID, "espn_s2": VALID_COOKIE, **overrides}
    return client.post("/api/connect", json=body)


# --- health ------------------------------------------------------------------


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


# --- connect -----------------------------------------------------------------


def test_connect_returns_a_token_and_the_league_list(client):
    response = connect(client)
    assert response.status_code == 200
    body = response.json()
    assert body["token"]
    assert body["swid"] == SWID
    assert [league["league_id"] for league in body["leagues"]] == ["123456"]


def test_connect_never_echoes_the_cookie(client):
    body = connect(client).text
    assert VALID_COOKIE not in body
    assert "espn_s2" not in body


def test_connect_with_dead_cookies_is_401(client):
    response = connect(client, espn_s2="expired")
    assert response.status_code == 401
    assert response.json()["error"] == "ESPNAuthError"


def test_connect_validates_its_input(client):
    assert client.post("/api/connect", json={"swid": SWID}).status_code == 422
    assert client.post("/api/connect", json={"swid": "", "espn_s2": "x"}).status_code == 422


def test_connect_explains_a_discovery_failure(client, espn):
    espn.discovery_error = ESPNUnavailableError("fan API down")
    body = connect(client).json()
    assert body["discovery_failed"] is True
    assert "add a league by its id" in body["message"].lower()


# --- auth boundary -----------------------------------------------------------


PROTECTED = [
    ("GET", "/api/me"),
    ("GET", "/api/leagues/123456/overview"),
    ("GET", "/api/leagues/123456/standings"),
    ("GET", "/api/leagues/123456/matchups"),
    ("GET", "/api/leagues/123456/teams"),
    ("GET", "/api/leagues/123456/teams/1/roster"),
    ("GET", "/api/leagues/123456/transactions"),
    ("GET", "/api/leagues/123456/power-rankings"),
    ("DELETE", "/api/me"),
]


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_every_protected_route_requires_a_token(client, method, path):
    assert client.request(method, path).status_code == 401


@pytest.mark.parametrize("header", ["Bearer nonsense", "Basic abc", "Bearer "])
def test_bad_authorization_headers_are_401(client, header):
    assert client.get("/api/me", headers={"Authorization": header}).status_code == 401


def test_token_signed_with_another_secret_is_401(client):
    forged, _ = issue_token(SWID, "some-other-secret", ttl_hours=1)
    response = client.get("/api/me", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


def test_valid_token_for_an_account_that_no_longer_exists_is_401(client):
    """Disconnect must invalidate in-flight sessions, not just delete rows."""
    connect(client)
    headers = auth_header(client.settings)
    assert client.delete("/api/me", headers=headers).json() == {"deleted": True}
    response = client.get("/api/me", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"] == "not_connected"


# --- account -----------------------------------------------------------------


def test_me_returns_the_league_list_and_no_secrets(client):
    connect(client)
    body = client.get("/api/me", headers=auth_header(client.settings)).json()
    assert body["swid"] == SWID
    assert len(body["leagues"]) == 1
    assert "espn_s2" not in str(body).lower()


def test_add_and_remove_a_league(client):
    connect(client)
    headers = auth_header(client.settings)
    added = client.post("/api/me/leagues", json={"league_id": "778899"}, headers=headers)
    assert added.status_code == 200
    assert added.json()["league_id"] == "778899"

    season = added.json()["season"]
    removed = client.delete(f"/api/me/leagues/{season}/778899", headers=headers)
    assert removed.json() == {"removed": True}
    assert client.delete(f"/api/me/leagues/{season}/778899", headers=headers).status_code == 404


# --- league views ------------------------------------------------------------


def test_league_views_render(client):
    connect(client)
    headers = auth_header(client.settings)

    standings = client.get("/api/leagues/123456/standings", headers=headers).json()
    assert len(standings["standings"]) == 4

    matchups = client.get("/api/leagues/123456/matchups?week=1", headers=headers).json()
    assert matchups["week"] == 1
    assert len(matchups["matchups"]) == 2

    roster = client.get("/api/leagues/123456/teams/1/roster", headers=headers).json()
    assert roster["roster"]["name"] == "Gridiron Giants"

    teams = client.get("/api/leagues/123456/teams", headers=headers).json()
    assert len(teams["teams"]) == 4

    transactions = client.get("/api/leagues/123456/transactions", headers=headers).json()
    assert len(transactions["transactions"]) == 2

    power = client.get("/api/leagues/123456/power-rankings", headers=headers).json()
    assert len(power["power_rankings"]) == 4
    assert power["weights"]


def test_league_not_linked_is_403(client):
    connect(client)
    response = client.get("/api/leagues/999999/standings", headers=auth_header(client.settings))
    assert response.status_code == 403
    assert response.json()["error"] == "league_not_linked"


def test_expired_espn_cookies_surface_as_401_so_the_ui_can_reconnect(client, espn):
    connect(client)
    espn.fetch_error = ESPNAuthError("cookies expired")
    response = client.get("/api/leagues/123456/standings", headers=auth_header(client.settings))
    assert response.status_code == 401
    assert response.json()["error"] == "ESPNAuthError"


def test_espn_outage_surfaces_as_503_not_401(client, espn):
    """A 401 would wrongly send the user back to re-paste working cookies."""
    connect(client)
    espn.fetch_error = ESPNUnavailableError("ESPN returned 500")
    response = client.get("/api/leagues/123456/standings", headers=auth_header(client.settings))
    assert response.status_code == 503


def test_out_of_range_week_is_rejected(client):
    connect(client)
    headers = auth_header(client.settings)
    assert client.get("/api/leagues/123456/matchups?week=0", headers=headers).status_code == 422
    assert client.get("/api/leagues/123456/matchups?week=99", headers=headers).status_code == 422


# --- sync --------------------------------------------------------------------


def test_sync_requires_the_shared_secret(client):
    assert client.post("/internal/sync").status_code == 403
    assert client.post("/internal/sync", headers={"X-Sync-Token": "wrong"}).status_code == 403


def test_sync_runs_with_the_shared_secret(client):
    connect(client)
    response = client.post("/internal/sync", headers={"X-Sync-Token": client.settings.sync_token})
    assert response.status_code == 200
    assert response.json()["leagues_synced"] == 1
