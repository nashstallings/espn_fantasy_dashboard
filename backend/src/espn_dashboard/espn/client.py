"""Thin async client over ESPN's unofficial Fantasy API.

Two hosts serve the same v3 league payloads. ``lm-api-reads.espn.com`` is what
espn.com's own fantasy web app calls today; ``fantasy.espn.com`` is the older
path that most third-party tooling documents. We try the current one first and
fall back, because either can start refusing traffic without notice.

Nothing in this module logs cookie values, and cookies are attached per-request
rather than held on a shared client, so a client instance is never implicitly
authenticated as some previous caller.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import httpx

from .errors import (
    ESPNAuthError,
    ESPNError,
    ESPNNotFoundError,
    ESPNSchemaError,
    ESPNUnavailableError,
)

logger = logging.getLogger(__name__)

READ_HOSTS = ("https://lm-api-reads.espn.com", "https://fantasy.espn.com")
FAN_API = "https://fan.api.espn.com/apis/v2/fans"
GAME = "ffl"

# ESPN rejects requests without a browser-shaped UA from some edges.
BASE_HEADERS = {
    "Accept": "application/json",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
}


@dataclass(frozen=True)
class ESPNCredentials:
    """A user's live ESPN session cookies. Never serialize this."""

    swid: str
    espn_s2: str

    def as_cookies(self) -> dict[str, str]:
        return {"SWID": self.swid, "espn_s2": self.espn_s2}

    def __repr__(self) -> str:  # keep cookies out of tracebacks and logs
        return f"ESPNCredentials(swid={self.swid!r}, espn_s2=<redacted>)"


def normalize_swid(raw: str) -> str:
    """ESPN's SWID cookie is a brace-wrapped GUID; users paste it both ways."""
    swid = (raw or "").strip().strip('"')
    if not swid:
        return ""
    if not swid.startswith("{"):
        swid = "{" + swid
    if not swid.endswith("}"):
        swid = swid + "}"
    return swid.upper()


def league_path(season: int, league_id: str) -> str:
    return f"/apis/v3/games/{GAME}/seasons/{season}/segments/0/leagues/{league_id}"


class ESPNClient:
    def __init__(self, *, timeout: float = 15.0, transport: httpx.AsyncBaseTransport | None = None):
        self._timeout = timeout
        self._transport = transport

    async def _request(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        credentials: ESPNCredentials | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        cookies = credentials.as_cookies() if credentials else None
        request_headers = {**BASE_HEADERS, **(headers or {})}
        # Redirects are NOT followed. ESPN answers an unauthenticated API request
        # with a 302 to a login page; following it turns an auth failure into a
        # 200 full of HTML, which then surfaces as a confusing "non-JSON
        # response" instead of "your cookies do not work". A 3xx from a JSON API
        # is an auth problem, and is reported as one below.
        async with httpx.AsyncClient(
            timeout=self._timeout, transport=self._transport, follow_redirects=False
        ) as client:
            try:
                response = await client.get(
                    url, params=params, headers=request_headers, cookies=cookies
                )
            except httpx.TimeoutException as exc:
                raise ESPNUnavailableError("ESPN timed out") from exc
            except httpx.HTTPError as exc:
                raise ESPNUnavailableError("could not reach ESPN") from exc

        if response.status_code in (401, 403):
            raise ESPNAuthError(
                "ESPN rejected these credentials for this league "
                "(cookies expired, or this account has no access)"
            )
        if 300 <= response.status_code < 400:
            # Only the host is reported: a redirect URL can carry query
            # parameters, and those are not ours to log.
            target = urlsplit(response.headers.get("location", "")).netloc or "an unknown host"
            raise ESPNAuthError(
                f"ESPN redirected to {target} instead of returning data — "
                "this normally means the cookies are expired or not entitled to this league"
            )
        if response.status_code == 404:
            raise ESPNNotFoundError("ESPN has no such league or season")
        if response.status_code >= 500:
            raise ESPNUnavailableError(f"ESPN returned {response.status_code}")
        if response.status_code >= 400:
            raise ESPNError(f"ESPN returned {response.status_code}")

        try:
            return response.json()
        except ValueError as exc:
            # Carry enough detail to diagnose without another deploy. The body is
            # ESPN's own response, so a short excerpt is safe to surface; the
            # request's cookies are never part of it.
            content_type = response.headers.get("content-type", "unknown")
            excerpt = " ".join(response.text[:200].split())
            logger.warning(
                "non-JSON from %s (status %s, content-type %s): %s",
                urlsplit(str(response.url)).path,
                response.status_code,
                content_type,
                excerpt,
            )
            raise ESPNSchemaError(
                f"ESPN returned {content_type} instead of JSON "
                f"(status {response.status_code}): {excerpt[:120]}"
            ) from exc

    async def _request_any_host(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        credentials: ESPNCredentials | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        last_error: ESPNError | None = None
        for host in READ_HOSTS:
            try:
                return await self._request(
                    f"{host}{path}", params=params, credentials=credentials, headers=headers
                )
            except (ESPNUnavailableError, ESPNSchemaError) as exc:
                # Host-level trouble: worth trying the other host.
                logger.warning("ESPN host %s failed: %s", host, exc)
                last_error = exc
            except ESPNError:
                # Auth/404 are answers about the request, not the host.
                raise
        raise last_error or ESPNUnavailableError("no ESPN host responded")

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
        params: dict[str, Any] = {"view": views}
        if scoring_period is not None:
            params["scoringPeriodId"] = scoring_period
        payload = await self._request_any_host(
            league_path(season, league_id),
            params=params,
            credentials=credentials,
            headers=headers,
        )
        # Some views return a single-element list rather than an object.
        if isinstance(payload, list):
            payload = payload[0] if payload else {}
        if not isinstance(payload, dict):
            raise ESPNSchemaError("ESPN league response was not an object")
        return payload

    async def league_is_private(self, season: int, league_id: str) -> bool:
        """Probe without cookies. 401/403 means the league needs credentials."""
        try:
            await self._request_any_host(
                league_path(season, league_id), params={"view": ["mSettings"]}
            )
        except ESPNAuthError:
            return True
        return False

    async def discover_leagues(
        self, credentials: ESPNCredentials, *, season: int
    ) -> list[dict[str, Any]]:
        """List the user's fantasy football teams via ESPN's fan preferences API.

        This endpoint is even less documented than the league API, so a failure
        here is not fatal: callers fall back to letting the user type a league id.
        """
        payload = await self._request(
            f"{FAN_API}/{credentials.swid}",
            params={
                "configuration": "SITE_DEFAULT",
                "platform": "web",
                "displayHiddenPrefs": "true",
                "source": "ESPN.com - FAM",
                "lang": "en",
                "section": "espn",
            },
            credentials=credentials,
        )
        if not isinstance(payload, dict):
            raise ESPNSchemaError("fan API response was not an object")
        return _parse_fan_preferences(payload, season=season)


def _parse_fan_preferences(payload: dict[str, Any], *, season: int) -> list[dict[str, Any]]:
    """Pull (league id, team id, names) out of the fan preferences blob.

    Preference entries for fantasy football carry ``typeId`` 9; the league itself
    is the first entry in ``metaData.entry.groups``. Both of those are
    conventions rather than contract, so anything unrecognized is skipped rather
    than raising — one odd preference row should not break connect.
    """
    found: dict[str, dict[str, Any]] = {}
    for pref in payload.get("preferences") or []:
        if not isinstance(pref, dict):
            continue
        entry = (pref.get("metaData") or {}).get("entry")
        if not isinstance(entry, dict):
            continue
        is_football = pref.get("typeId") == 9 or entry.get("gameId") == 1
        if not is_football:
            continue
        entry_season = entry.get("seasonId")
        if entry_season is not None and int(entry_season) != season:
            continue
        groups = entry.get("groups")
        if not isinstance(groups, list) or not groups:
            continue
        group = groups[0] if isinstance(groups[0], dict) else {}
        league_id = str(group.get("groupId") or "").strip()
        if not league_id:
            continue
        team_name = " ".join(
            str(part) for part in (entry.get("teamLocation"), entry.get("teamNickname")) if part
        ).strip()
        found[league_id] = {
            "league_id": league_id,
            "season": int(entry_season or season),
            "name": str(group.get("groupName") or "").strip(),
            "team_id": entry.get("entryId"),
            "team_name": team_name or str(entry.get("name") or "").strip(),
        }
    return sorted(found.values(), key=lambda row: row["league_id"])
