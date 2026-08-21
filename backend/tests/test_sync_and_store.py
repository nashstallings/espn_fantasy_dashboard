"""The scheduled BigQuery snapshot, the credential store, and the response cache."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from espn_dashboard.bigquery_sync import (
    ALL_TABLES,
    TABLE_LEAGUES,
    TABLE_MATCHUPS,
    TABLE_POWER_RANKINGS,
    TABLE_TEAM_WEEK,
    NullWriter,
    build_snapshot_rows,
    sync_all_leagues,
)
from espn_dashboard.cache import TTLCache
from espn_dashboard.espn.client import normalize_swid
from espn_dashboard.espn.errors import ESPNAuthError
from espn_dashboard.service import current_season
from espn_dashboard.store import CredentialRecord, LeagueRef, MemoryCredentialStore

from .conftest import VALID_COOKIE
from .fixtures import OTHER_SWID, SWID, league_payload

NOW = datetime(2026, 11, 3, 9, 0, tzinfo=UTC)


# --- snapshot shaping --------------------------------------------------------


def test_snapshot_covers_every_table():
    rows = build_snapshot_rows(
        league_payload(), league_id="123456", season=2026, snapshot_ts=NOW
    )
    assert set(rows) == set(ALL_TABLES)
    assert len(rows[TABLE_LEAGUES]) == 1
    assert len(rows[TABLE_TEAM_WEEK]) == 12  # 4 teams x 3 completed weeks
    assert len(rows[TABLE_MATCHUPS]) == 8
    assert len(rows[TABLE_POWER_RANKINGS]) == 4


def test_every_row_carries_the_snapshot_stamp():
    rows = build_snapshot_rows(
        league_payload(), league_id="123456", season=2026, snapshot_ts=NOW
    )
    for table_rows in rows.values():
        for row in table_rows:
            assert row["snapshot_date"] == "2026-11-03"
            assert row["league_key"] == "2026:123456"
            assert row["season"] == 2026


def test_team_week_accumulates_season_to_date_totals():
    rows = build_snapshot_rows(
        league_payload(), league_id="123456", season=2026, snapshot_ts=NOW
    )[TABLE_TEAM_WEEK]
    team_one = sorted(
        (row for row in rows if row["team_id"] == 1), key=lambda row: row["week"]
    )
    assert [row["points"] for row in team_one] == [100.0, 95.0, 88.0]
    assert team_one[-1]["cumulative_points"] == pytest.approx(283.0)
    assert team_one[-1]["points_per_game_to_date"] == pytest.approx(94.33)
    assert [row["result"] for row in team_one] == ["W", "W", "W"]


def test_snapshot_carries_no_credential_material():
    rows = build_snapshot_rows(
        league_payload(), league_id="123456", season=2026, snapshot_ts=NOW
    )
    blob = str(rows)
    assert SWID not in blob and VALID_COOKIE not in blob


# --- the sync walk -----------------------------------------------------------


async def test_sync_writes_every_connected_league(connected, settings):
    writer = NullWriter()
    result = await sync_all_leagues(
        connected, settings, season=current_season(), writer=writer, now=NOW
    )
    assert result["leagues_synced"] == 1
    assert result["failures"] == []
    assert writer.tables[TABLE_POWER_RANKINGS]


async def test_sync_snapshots_a_shared_league_once(settings, store, espn):
    """Two users in one league must not produce duplicate history rows."""
    from espn_dashboard.service import DashboardService

    service = DashboardService(settings, store, espn)
    await service.connect(swid=SWID, espn_s2=VALID_COOKIE)
    await service.connect(swid=OTHER_SWID, espn_s2=VALID_COOKIE)

    writer = NullWriter()
    result = await sync_all_leagues(
        service, settings, season=current_season(), writer=writer, now=NOW
    )
    assert result["leagues_synced"] == 1
    assert len(writer.tables[TABLE_LEAGUES]) == 1


async def test_sync_clears_the_day_before_appending(connected, settings):
    """Re-running a day fixes it instead of doubling it."""
    writer = NullWriter()
    await sync_all_leagues(connected, settings, season=current_season(), writer=writer, now=NOW)
    assert len(writer.deletions) == len(ALL_TABLES)
    for _table, snapshot_date, keys in writer.deletions:
        assert snapshot_date == "2026-11-03"
        assert keys == [f"{current_season()}:123456"]


async def test_one_broken_league_does_not_abort_the_run(connected, settings, espn):
    await connected.add_league(SWID, "778899")
    espn.fetch_error = ESPNAuthError("cookies expired")

    writer = NullWriter()
    result = await sync_all_leagues(
        connected, settings, season=current_season(), writer=writer, now=NOW
    )
    assert result["leagues_synced"] == 0
    assert {failure["league_id"] for failure in result["failures"]} == {"123456", "778899"}


async def test_sync_skips_other_seasons(connected, settings):
    writer = NullWriter()
    result = await sync_all_leagues(connected, settings, season=1999, writer=writer, now=NOW)
    assert result["leagues_synced"] == 0
    assert writer.tables == {}


# --- credential store --------------------------------------------------------


def test_store_round_trip():
    store = MemoryCredentialStore()
    record = CredentialRecord(
        swid=SWID,
        espn_s2_encrypted="ciphertext",
        leagues=[LeagueRef(league_id="1", season=2026, name="L")],
    )
    store.put(record)

    loaded = store.get(SWID)
    assert loaded.swid == SWID
    assert loaded.leagues[0].name == "L"
    assert loaded.created_at and loaded.updated_at


def test_store_preserves_created_at_across_updates():
    store = MemoryCredentialStore()
    store.put(CredentialRecord(swid=SWID, espn_s2_encrypted="a"))
    created = store.get(SWID).created_at
    store.put(CredentialRecord(swid=SWID, espn_s2_encrypted="b"))
    assert store.get(SWID).created_at == created
    assert store.get(SWID).espn_s2_encrypted == "b"


def test_store_isolates_accounts():
    store = MemoryCredentialStore()
    store.put(CredentialRecord(swid=SWID, espn_s2_encrypted="a"))
    assert store.get(OTHER_SWID) is None
    assert store.delete(OTHER_SWID) is False
    assert len(store.list_all()) == 1


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("{abc-DEF}", "{ABC-DEF}"),
        ("abc-def", "{ABC-DEF}"),
        ('"{abc-def}"', "{ABC-DEF}"),
        ("  abc-def  ", "{ABC-DEF}"),
        ("", ""),
    ],
)
def test_swid_normalization(raw, expected):
    assert normalize_swid(raw) == expected


# --- cache -------------------------------------------------------------------


def test_cache_returns_what_it_stored():
    cache = TTLCache(60)
    cache.set("k", {"v": 1})
    assert cache.get("k") == {"v": 1}
    assert cache.get("missing") is None


def test_zero_ttl_disables_caching():
    cache = TTLCache(0)
    cache.set("k", 1)
    assert cache.get("k") is None


def test_cache_evicts_by_prefix():
    cache = TTLCache(60)
    cache.set("{A}|x", 1)
    cache.set("{B}|x", 2)
    cache.invalidate_prefix("{A}|")
    assert cache.get("{A}|x") is None
    assert cache.get("{B}|x") == 2


def test_cache_stays_within_its_bound():
    cache = TTLCache(60, max_entries=8)
    for index in range(50):
        cache.set(f"k{index}", index)
    assert len(cache._entries) <= 8


def test_expired_entries_are_dropped(monkeypatch):
    import espn_dashboard.cache as cache_module

    clock = {"now": 1000.0}
    monkeypatch.setattr(cache_module.time, "monotonic", lambda: clock["now"])
    cache = TTLCache(60)
    cache.set("k", 1)
    clock["now"] += 61
    assert cache.get("k") is None
