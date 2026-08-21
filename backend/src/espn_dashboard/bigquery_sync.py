"""Scheduled snapshots of every connected league into BigQuery.

ESPN only shows you now. The point of keeping history is the things ESPN will
not tell you: how a team's scoring trended across a season, what the power
rankings looked like in week 4, how this year compares to last. So once a day we
walk every connected account, snapshot each league, and append dated rows.

Design notes:

* **One snapshot per league, not per user.** Several users in the same league
  would otherwise produce identical rows; leagues are de-duplicated by
  ``(league_id, season)`` and fetched with the first account that can read them.
* **Idempotent per day.** Each run deletes the current ``snapshot_date`` for the
  leagues it is about to write, then appends. Re-running fixes a bad run instead
  of doubling it.
* **One league's failure is not the run's failure.** ESPN 500s, an expired
  cookie, or a schema surprise is recorded per-league and the walk continues.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, Protocol

from . import analytics
from .config import Settings
from .espn import transform
from .espn.errors import ESPNError
from .service import OVERVIEW_VIEWS, DashboardService

logger = logging.getLogger(__name__)

TABLE_LEAGUES = "leagues"
TABLE_TEAM_WEEK = "team_week"
TABLE_MATCHUPS = "matchups"
TABLE_POWER_RANKINGS = "power_rankings"
ALL_TABLES = (TABLE_LEAGUES, TABLE_TEAM_WEEK, TABLE_MATCHUPS, TABLE_POWER_RANKINGS)


class SnapshotWriter(Protocol):
    def write(self, table: str, rows: list[dict[str, Any]]) -> None: ...

    def delete_snapshot(self, table: str, snapshot_date: str, league_keys: list[str]) -> None: ...


class NullWriter:
    """Collects rows instead of writing them. Used locally and by tests."""

    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {}
        self.deletions: list[tuple[str, str, list[str]]] = []

    def write(self, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:  # mirrors BigQueryWriter, which no-ops on an empty load
            return
        self.tables.setdefault(table, []).extend(rows)

    def delete_snapshot(self, table: str, snapshot_date: str, league_keys: list[str]) -> None:
        self.deletions.append((table, snapshot_date, league_keys))


class BigQueryWriter:
    def __init__(self, project: str, dataset: str) -> None:
        from google.cloud import bigquery  # lazy: not installed paths should still import

        self._bigquery = bigquery
        self._client = bigquery.Client(project=project or None)
        self._dataset = dataset
        self._project = project or self._client.project

    def _table_id(self, table: str) -> str:
        return f"{self._project}.{self._dataset}.{table}"

    def write(self, table: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        job = self._client.load_table_from_json(
            rows,
            self._table_id(table),
            job_config=self._bigquery.LoadJobConfig(
                write_disposition="WRITE_APPEND",
                schema_update_options=["ALLOW_FIELD_ADDITION"],
            ),
        )
        job.result()

    def delete_snapshot(self, table: str, snapshot_date: str, league_keys: list[str]) -> None:
        if not league_keys:
            return
        query = (
            f"DELETE FROM `{self._table_id(table)}` "
            "WHERE snapshot_date = @snapshot_date AND league_key IN UNNEST(@league_keys)"
        )
        job_config = self._bigquery.QueryJobConfig(
            query_parameters=[
                self._bigquery.ScalarQueryParameter("snapshot_date", "DATE", snapshot_date),
                self._bigquery.ArrayQueryParameter("league_keys", "STRING", league_keys),
            ]
        )
        self._client.query(query, job_config=job_config).result()


def build_writer(settings: Settings) -> SnapshotWriter:
    if settings.gcp_project and settings.bigquery_dataset:
        return BigQueryWriter(settings.gcp_project, settings.bigquery_dataset)
    logger.warning("no GCP project configured; snapshots will be discarded")
    return NullWriter()


def league_key(league_id: str, season: int) -> str:
    return f"{season}:{league_id}"


def build_snapshot_rows(
    payload: dict[str, Any],
    *,
    league_id: str,
    season: int,
    snapshot_ts: datetime,
) -> dict[str, list[dict[str, Any]]]:
    """Normalize one league payload into the four table shapes."""
    key = league_key(league_id, season)
    snapshot_date = snapshot_ts.date().isoformat()
    stamp = {
        "snapshot_date": snapshot_date,
        "snapshot_ts": snapshot_ts.isoformat(),
        "league_key": key,
        "league_id": league_id,
        "season": season,
    }

    meta = transform.league_meta(payload, league_id=league_id, season=season)
    standings_rows = transform.standings(payload)
    weekly = transform.weekly_scores(payload)
    rankings = analytics.power_rankings(standings_rows, weekly)

    leagues = [
        {
            **stamp,
            "name": meta["name"],
            "size": meta["size"],
            "current_week": meta["current_week"],
            "final_week": meta["final_week"],
            "playoff_team_count": meta["playoff_team_count"],
            "is_active": meta["is_active"],
        }
    ]

    standings_by_team = {row["team_id"]: row for row in standings_rows}
    team_week: list[dict[str, Any]] = []
    for team_id, games in weekly.items():
        identity = standings_by_team.get(team_id, {})
        running_total = 0.0
        for index, game in enumerate(games, start=1):
            running_total += game["points"]
            team_week.append(
                {
                    **stamp,
                    "team_id": team_id,
                    "team_name": identity.get("name", f"Team {team_id}"),
                    "week": game["week"],
                    "points": game["points"],
                    "opponent_id": game["opponent_id"],
                    "opponent_points": game["opponent_points"],
                    "result": _result(game),
                    "cumulative_points": round(running_total, 2),
                    "points_per_game_to_date": round(running_total / index, 2),
                }
            )

    matchup_rows = [
        {
            **stamp,
            "matchup_id": game["matchup_id"],
            "week": game["week"],
            "playoff_tier": game["playoff_tier"],
            "winner": game["winner"],
            "is_complete": game["is_complete"],
            "home_team_id": (game["home"] or {}).get("team_id"),
            "home_team_name": (game["home"] or {}).get("name", ""),
            "home_points": (game["home"] or {}).get("points", 0.0),
            "away_team_id": (game["away"] or {}).get("team_id"),
            "away_team_name": (game["away"] or {}).get("name", ""),
            "away_points": (game["away"] or {}).get("points", 0.0),
        }
        for game in transform.matchups(payload)
    ]

    power_rows = [
        {
            **stamp,
            "week": meta["current_week"],
            "team_id": row["team_id"],
            "team_name": row["name"],
            "power_rank": row["rank"],
            "standings_rank": row["standings_rank"],
            "power_score": row["power_score"],
            "all_play_win_pct": row["components"]["all_play_win_pct"],
            "scoring_component": row["components"]["scoring"],
            "recent_form_component": row["components"]["recent_form"],
            "win_pct": row["components"]["win_pct"],
            "points_per_game": row["points_per_game"],
            "recent_points_per_game": row["recent_points_per_game"],
        }
        for row in rankings
    ]

    return {
        TABLE_LEAGUES: leagues,
        TABLE_TEAM_WEEK: team_week,
        TABLE_MATCHUPS: matchup_rows,
        TABLE_POWER_RANKINGS: power_rows,
    }


def _result(game: dict[str, Any]) -> str:
    opponent_points = game.get("opponent_points")
    if opponent_points is None:
        return "BYE"
    if game["points"] > opponent_points:
        return "W"
    if game["points"] < opponent_points:
        return "L"
    return "T"


async def sync_all_leagues(
    service: DashboardService,
    settings: Settings,
    *,
    season: int,
    writer: SnapshotWriter | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    writer = writer or build_writer(settings)
    snapshot_ts = now or datetime.now(UTC)
    snapshot_date = snapshot_ts.date().isoformat()

    # (league_id, season) -> a SWID that can read it.
    targets: dict[str, str] = {}
    for record in service.store.list_all():
        for ref in record.leagues:
            if ref.season == season:
                targets.setdefault(ref.league_id, record.swid)

    collected: dict[str, list[dict[str, Any]]] = {table: [] for table in ALL_TABLES}
    synced: list[str] = []
    failures: list[dict[str, str]] = []

    for league_id, swid in sorted(targets.items()):
        try:
            payload = await service.raw_league(swid, league_id, season, OVERVIEW_VIEWS)
            rows = build_snapshot_rows(
                payload, league_id=league_id, season=season, snapshot_ts=snapshot_ts
            )
        except ESPNError as exc:
            logger.warning("snapshot failed for league %s: %s", league_id, exc)
            failures.append({"league_id": league_id, "error": str(exc)})
            continue
        except Exception as exc:  # noqa: BLE001 - one league must not kill the run
            logger.exception("unexpected snapshot failure for league %s", league_id)
            failures.append({"league_id": league_id, "error": str(exc)})
            continue
        for table, table_rows in rows.items():
            collected[table].extend(table_rows)
        synced.append(league_key(league_id, season))

    for table in ALL_TABLES:
        writer.delete_snapshot(table, snapshot_date, synced)
        writer.write(table, collected[table])

    return {
        "season": season,
        "snapshot_date": snapshot_date,
        "leagues_synced": len(synced),
        "rows_written": {table: len(rows) for table, rows in collected.items()},
        "failures": failures,
    }
