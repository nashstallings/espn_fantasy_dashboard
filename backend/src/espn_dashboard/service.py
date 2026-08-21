"""The layer between HTTP routes and ESPN.

Routes stay thin: they authenticate a request, call one method here, and
serialize the result. This module owns the rules that matter — decrypting a
cookie for exactly one call, collapsing repeat views onto a cached payload, and
never handing cookie material back out.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from . import analytics
from .cache import TTLCache
from .config import Settings
from .crypto import decrypt, encrypt
from .espn import transform
from .espn.client import ESPNClient, ESPNCredentials, normalize_swid
from .espn.errors import ESPNAuthError, ESPNError
from .store import CredentialRecord, CredentialStore, LeagueRef, utcnow

logger = logging.getLogger(__name__)

# View bundles. Asking for several views in one request is what ESPN's own web
# app does and keeps us to a single upstream call per dashboard page.
OVERVIEW_VIEWS = ["mSettings", "mTeam", "mMatchup"]
ROSTER_VIEWS = ["mSettings", "mTeam", "mRoster"]
TRANSACTION_VIEWS = ["mSettings", "mTeam", "mRoster", "mTransactions2"]

TRANSACTION_FILTER = {
    "transactions": {
        "filterType": {"value": ["WAIVER", "TRADE_ACCEPTED", "FREEAGENT", "TRADE_UPHELD"]},
        "offset": 0,
        "limit": 150,
        "sortDate": {"sortPriority": 1, "sortAsc": False},
    }
}


class NotConnectedError(Exception):
    """No stored credentials for this SWID — the session outlived the record."""


class LeagueAccessError(Exception):
    """The caller asked for a league that is not linked to their account."""


def current_season(today: datetime | None = None) -> int:
    """The NFL fantasy season a given date belongs to.

    Seasons are named for the year they start in, and ESPN rolls a new season
    over in the offseason, so anything from June onward is the new year.
    """
    now = today or datetime.now(UTC)
    return now.year if now.month >= 6 else now.year - 1


@dataclass
class ConnectResult:
    swid: str
    leagues: list[LeagueRef]
    discovery_failed: bool = False


class DashboardService:
    def __init__(
        self, settings: Settings, store: CredentialStore, client: ESPNClient | None = None
    ) -> None:
        self._settings = settings
        self._store = store
        self._client = client or ESPNClient(timeout=settings.espn_request_timeout_seconds)
        self._cache = TTLCache(settings.espn_cache_ttl_seconds)

    @property
    def store(self) -> CredentialStore:
        """Exposed for the scheduled sync, which walks every connected account."""
        return self._store

    # --- connect / disconnect -------------------------------------------------

    async def connect(
        self, *, swid: str, espn_s2: str, season: int | None = None, league_id: str = ""
    ) -> ConnectResult:
        """Prove the pasted cookies work, then store them encrypted.

        Validation is the point of this call: we never store credentials we have
        not just seen ESPN accept.
        """
        swid = normalize_swid(swid)
        espn_s2 = (espn_s2 or "").strip()
        if not swid or not espn_s2:
            raise ValueError("both SWID and espn_s2 are required")
        season = season or current_season()
        credentials = ESPNCredentials(swid=swid, espn_s2=espn_s2)

        leagues: list[LeagueRef] = []
        discovery_failed = False

        if league_id:
            leagues.append(await self._validate_league(credentials, league_id.strip(), season))
        else:
            try:
                discovered = await self._client.discover_leagues(credentials, season=season)
                leagues = [LeagueRef.from_dict(row) for row in discovered]
            except ESPNAuthError:
                raise
            except ESPNError as exc:
                # The fan API is the flakiest thing we touch. Failing it should
                # not block a user who knows their league id.
                logger.warning("league discovery failed for a connect attempt: %s", exc)
                discovery_failed = True

        if not leagues and not discovery_failed:
            discovery_failed = True

        record = self._store.get(swid) or CredentialRecord(swid=swid, espn_s2_encrypted="")
        record.espn_s2_encrypted = encrypt(
            espn_s2, self._settings.credential_encryption_key, aad=swid
        )
        record.leagues = self._merge_leagues(record.leagues, leagues)
        record.last_validated_at = utcnow()
        self._store.put(record)
        self._cache.invalidate_prefix(f"{swid}|")

        return ConnectResult(
            swid=swid, leagues=record.leagues, discovery_failed=discovery_failed
        )

    async def add_league(self, swid: str, league_id: str, season: int | None = None) -> LeagueRef:
        """Attach a league the user typed in by hand (discovery miss, or old season)."""
        season = season or current_season()
        record = self._require_record(swid)
        credentials = self._credentials(record)
        league = await self._validate_league(credentials, league_id.strip(), season)
        record.leagues = self._merge_leagues(record.leagues, [league])
        self._store.put(record)
        return league

    def remove_league(self, swid: str, league_id: str, season: int) -> bool:
        record = self._require_record(swid)
        before = len(record.leagues)
        record.leagues = [
            ref
            for ref in record.leagues
            if not (ref.league_id == league_id and ref.season == season)
        ]
        if len(record.leagues) == before:
            return False
        self._store.put(record)
        self._cache.invalidate_prefix(f"{swid}|")
        return True

    def disconnect(self, swid: str) -> bool:
        """Delete everything we hold for this account."""
        self._cache.invalidate_prefix(f"{swid}|")
        return self._store.delete(swid)

    def account(self, swid: str) -> CredentialRecord:
        return self._require_record(swid)

    # --- league data ----------------------------------------------------------

    async def overview(self, swid: str, league_id: str, season: int) -> dict[str, Any]:
        payload = await self._league_payload(swid, league_id, season, OVERVIEW_VIEWS)
        meta = transform.league_meta(payload, league_id=league_id, season=season)
        rows = transform.standings(payload)
        weekly = transform.weekly_scores(payload)
        return {
            "league": meta,
            "standings": rows,
            "power_rankings": analytics.power_rankings(rows, weekly),
            "current_matchups": transform.matchups(payload, week=meta["current_week"]),
        }

    async def standings(self, swid: str, league_id: str, season: int) -> dict[str, Any]:
        payload = await self._league_payload(swid, league_id, season, OVERVIEW_VIEWS)
        return {
            "league": transform.league_meta(payload, league_id=league_id, season=season),
            "standings": transform.standings(payload),
        }

    async def matchups(
        self, swid: str, league_id: str, season: int, week: int | None = None
    ) -> dict[str, Any]:
        payload = await self._league_payload(swid, league_id, season, OVERVIEW_VIEWS)
        meta = transform.league_meta(payload, league_id=league_id, season=season)
        target_week = week or meta["current_week"]
        return {
            "league": meta,
            "week": target_week,
            "matchups": transform.matchups(payload, week=target_week),
        }

    async def roster(
        self, swid: str, league_id: str, season: int, team_id: int, week: int | None = None
    ) -> dict[str, Any]:
        meta_payload = await self._league_payload(swid, league_id, season, OVERVIEW_VIEWS)
        meta = transform.league_meta(meta_payload, league_id=league_id, season=season)
        scoring_period = week or meta["latest_scoring_period"]
        payload = await self._league_payload(
            swid, league_id, season, ROSTER_VIEWS, scoring_period=scoring_period
        )
        return {
            "league": meta,
            "week": scoring_period,
            "roster": transform.roster(payload, team_id=team_id, scoring_period=scoring_period),
        }

    async def teams(self, swid: str, league_id: str, season: int) -> dict[str, Any]:
        payload = await self._league_payload(swid, league_id, season, OVERVIEW_VIEWS)
        return {
            "league": transform.league_meta(payload, league_id=league_id, season=season),
            "teams": list(transform.team_index(payload).values()),
        }

    async def transactions(
        self, swid: str, league_id: str, season: int, limit: int = 100
    ) -> dict[str, Any]:
        payload = await self._league_payload(
            swid,
            league_id,
            season,
            TRANSACTION_VIEWS,
            headers={"x-fantasy-filter": json.dumps(TRANSACTION_FILTER)},
        )
        return {
            "league": transform.league_meta(payload, league_id=league_id, season=season),
            "transactions": transform.transactions(
                payload, limit=limit, player_names=transform.player_name_index(payload)
            ),
        }

    async def power_rankings(self, swid: str, league_id: str, season: int) -> dict[str, Any]:
        payload = await self._league_payload(swid, league_id, season, OVERVIEW_VIEWS)
        rows = transform.standings(payload)
        weekly = transform.weekly_scores(payload)
        return {
            "league": transform.league_meta(payload, league_id=league_id, season=season),
            "power_rankings": analytics.power_rankings(rows, weekly),
            "weights": analytics.WEIGHTS,
        }

    async def raw_league(
        self, swid: str, league_id: str, season: int, views: list[str]
    ) -> dict[str, Any]:
        """Uncached fetch used by the BigQuery sync job."""
        record = self._require_record(swid)
        self._assert_linked(record, league_id, season)
        return await self._client.fetch_league(
            season, league_id, views, credentials=self._credentials(record)
        )

    # --- internals ------------------------------------------------------------

    async def _league_payload(
        self,
        swid: str,
        league_id: str,
        season: int,
        views: list[str],
        *,
        scoring_period: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        record = self._require_record(swid)
        self._assert_linked(record, league_id, season)

        # SWID leads the key so one user's private payload can never be served
        # to another, and so disconnect can drop a whole account by prefix.
        key = f"{swid}|{season}|{league_id}|{','.join(views)}|{scoring_period}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        payload = await self._client.fetch_league(
            season,
            league_id,
            views,
            credentials=self._credentials(record),
            scoring_period=scoring_period,
            headers=headers,
        )
        self._cache.set(key, payload)
        return payload

    async def _validate_league(
        self, credentials: ESPNCredentials, league_id: str, season: int
    ) -> LeagueRef:
        if not league_id:
            raise ValueError("league id is required")
        payload = await self._client.fetch_league(
            season, league_id, ["mSettings", "mTeam"], credentials=credentials
        )
        meta = transform.league_meta(payload, league_id=league_id, season=season)
        team_id, team_name = self._team_for_owner(payload, credentials.swid)
        is_private = True
        try:
            is_private = await self._client.league_is_private(season, league_id)
        except ESPNError as exc:
            logger.info("visibility probe failed for league %s: %s", league_id, exc)
        return LeagueRef(
            league_id=league_id,
            season=season,
            name=meta["name"],
            team_id=team_id,
            team_name=team_name,
            is_private=is_private,
        )

    @staticmethod
    def _team_for_owner(payload: dict[str, Any], swid: str) -> tuple[int | None, str]:
        target = swid.upper()
        for team in payload.get("teams") or []:
            if not isinstance(team, dict):
                continue
            owners = [str(o).upper() for o in (team.get("owners") or [])]
            if str(team.get("primaryOwner") or "").upper():
                owners.append(str(team.get("primaryOwner")).upper())
            if target in owners:
                return team.get("id"), transform.team_display_name(team)
        return None, ""

    @staticmethod
    def _merge_leagues(existing: list[LeagueRef], incoming: list[LeagueRef]) -> list[LeagueRef]:
        """Union by (league_id, season), preferring the freshly fetched row."""
        merged = {(ref.league_id, ref.season): ref for ref in existing}
        for ref in incoming:
            merged[(ref.league_id, ref.season)] = ref
        return sorted(merged.values(), key=lambda r: (-r.season, r.name.lower(), r.league_id))

    def _require_record(self, swid: str) -> CredentialRecord:
        record = self._store.get(normalize_swid(swid))
        if record is None:
            raise NotConnectedError("no stored ESPN credentials for this session")
        return record

    @staticmethod
    def _assert_linked(record: CredentialRecord, league_id: str, season: int) -> None:
        """A session may only read leagues its own account is linked to.

        Without this, a valid token plus a guessed league id would turn the proxy
        into an open reader of other people's private leagues, using this user's
        cookies.
        """
        for ref in record.leagues:
            if ref.league_id == str(league_id) and ref.season == int(season):
                return
        raise LeagueAccessError(
            f"league {league_id} ({season}) is not linked to this account; add it first"
        )

    def _credentials(self, record: CredentialRecord) -> ESPNCredentials:
        espn_s2 = decrypt(
            record.espn_s2_encrypted, self._settings.credential_encryption_key, aad=record.swid
        )
        return ESPNCredentials(swid=record.swid, espn_s2=espn_s2)
