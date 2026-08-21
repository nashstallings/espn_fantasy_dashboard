"""HTTP-level behaviour of the ESPN client, driven by a mock transport.

These cover the failure modes that actually bit in production: an auth redirect
masquerading as success, and an unhelpful error when ESPN answers with HTML.
"""

from __future__ import annotations

import httpx
import pytest

from espn_dashboard.espn.client import ESPNClient, ESPNCredentials, normalize_swid
from espn_dashboard.espn.errors import (
    ESPNAuthError,
    ESPNNotFoundError,
    ESPNSchemaError,
    ESPNUnavailableError,
)

CREDENTIALS = ESPNCredentials(swid="{SWID}", espn_s2="cookie")


def client_for(handler) -> ESPNClient:
    return ESPNClient(timeout=5.0, transport=httpx.MockTransport(handler))


async def test_successful_fetch_returns_the_payload():
    def handler(request):
        return httpx.Response(200, json={"id": 1, "teams": []})

    payload = await client_for(handler).fetch_league(
        2026, "123", ["mTeam"], credentials=CREDENTIALS
    )
    assert payload["id"] == 1


async def test_login_redirect_is_an_auth_error_not_a_schema_error():
    """The production bug: redirects were followed, so a 302 to ESPN's login page
    arrived as a 200 full of HTML and got reported as 'non-JSON response'."""

    def handler(request):
        return httpx.Response(302, headers={"location": "https://cdn.registerdisney.go.com/login?x=1"})

    with pytest.raises(ESPNAuthError, match="registerdisney"):
        await client_for(handler).fetch_league(2026, "123", ["mTeam"], credentials=CREDENTIALS)


async def test_redirect_error_does_not_leak_the_query_string():
    def handler(request):
        return httpx.Response(302, headers={"location": "https://login.espn.com/?token=SECRETVALUE"})

    with pytest.raises(ESPNAuthError) as caught:
        await client_for(handler).fetch_league(2026, "123", ["mTeam"], credentials=CREDENTIALS)
    assert "SECRETVALUE" not in str(caught.value)
    assert "login.espn.com" in str(caught.value)


async def test_html_body_reports_status_content_type_and_excerpt():
    def handler(request):
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            text="<html><body>Sign in to continue</body></html>",
        )

    with pytest.raises(ESPNSchemaError) as caught:
        await client_for(handler).fetch_league(2026, "123", ["mTeam"], credentials=CREDENTIALS)
    message = str(caught.value)
    assert "text/html" in message
    assert "status 200" in message
    assert "Sign in to continue" in message


async def test_auth_status_is_reported_as_auth():
    def handler(request):
        return httpx.Response(401)

    with pytest.raises(ESPNAuthError):
        await client_for(handler).fetch_league(2026, "123", ["mTeam"], credentials=CREDENTIALS)


async def test_missing_league_is_not_found():
    def handler(request):
        return httpx.Response(404)

    with pytest.raises(ESPNNotFoundError):
        await client_for(handler).fetch_league(2026, "123", ["mTeam"], credentials=CREDENTIALS)


async def test_server_error_falls_back_to_the_other_host_then_gives_up():
    seen: list[str] = []

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(503)

    with pytest.raises(ESPNUnavailableError):
        await client_for(handler).fetch_league(2026, "123", ["mTeam"], credentials=CREDENTIALS)
    assert len(seen) == 2, "both read hosts should be tried"
    assert len(set(seen)) == 2


async def test_auth_failure_does_not_retry_the_other_host():
    """An auth answer is about the request, not the host — retrying is pointless."""
    seen: list[str] = []

    def handler(request):
        seen.append(request.url.host)
        return httpx.Response(403)

    with pytest.raises(ESPNAuthError):
        await client_for(handler).fetch_league(2026, "123", ["mTeam"], credentials=CREDENTIALS)
    assert len(seen) == 1


async def test_views_and_scoring_period_reach_the_query_string():
    captured: dict[str, list[str]] = {}

    def handler(request):
        captured["view"] = request.url.params.get_list("view")
        captured["period"] = [request.url.params.get("scoringPeriodId", "")]
        return httpx.Response(200, json={})

    await client_for(handler).fetch_league(
        2026, "123", ["mTeam", "mRoster"], credentials=CREDENTIALS, scoring_period=3
    )
    assert captured["view"] == ["mTeam", "mRoster"]
    assert captured["period"] == ["3"]


async def test_cookies_are_sent_and_never_appear_in_repr():
    captured: dict[str, str] = {}

    def handler(request):
        captured["cookie"] = request.headers.get("cookie", "")
        return httpx.Response(200, json={})

    await client_for(handler).fetch_league(2026, "123", ["mTeam"], credentials=CREDENTIALS)
    assert "espn_s2=cookie" in captured["cookie"]
    assert "cookie" not in repr(CREDENTIALS)
    assert "redacted" in repr(CREDENTIALS)


def test_swid_normalization_matches_espns_format():
    assert normalize_swid("abc-def") == "{ABC-DEF}"
