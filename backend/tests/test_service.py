"""The connect flow and the access rules around stored credentials."""

from __future__ import annotations

import pytest

from espn_dashboard.espn.errors import ESPNAuthError, ESPNUnavailableError
from espn_dashboard.service import (
    LeagueAccessError,
    NotConnectedError,
    current_season,
)
from espn_dashboard.store import document_id

from .conftest import VALID_COOKIE
from .fixtures import OTHER_SWID, SWID

# --- connect -----------------------------------------------------------------


async def test_connect_discovers_leagues_and_stores_them(service, store):
    result = await service.connect(swid=SWID, espn_s2=VALID_COOKIE)
    assert result.swid == SWID
    assert [league.league_id for league in result.leagues] == ["123456"]
    assert store.get(SWID) is not None


async def test_connect_normalizes_a_swid_pasted_without_braces(service, store):
    bare = SWID.strip("{}").lower()
    result = await service.connect(swid=bare, espn_s2=VALID_COOKIE)
    assert result.swid == SWID
    assert store.get(SWID) is not None


async def test_connect_stores_the_cookie_encrypted_never_in_the_clear(service, store):
    await service.connect(swid=SWID, espn_s2=VALID_COOKIE)
    record = store.get(SWID)
    assert VALID_COOKIE not in record.espn_s2_encrypted
    assert VALID_COOKIE not in str(record.to_dict())


async def test_connect_rejects_cookies_espn_will_not_accept(service, store):
    with pytest.raises(ESPNAuthError):
        await service.connect(swid=SWID, espn_s2="expired-cookie")
    assert store.get(SWID) is None, "invalid credentials must never be persisted"


@pytest.mark.parametrize(
    ("swid", "espn_s2"), [("", VALID_COOKIE), (SWID, ""), ("", "")]
)
async def test_connect_requires_both_cookies(service, swid, espn_s2):
    with pytest.raises(ValueError):
        await service.connect(swid=swid, espn_s2=espn_s2)


async def test_connect_falls_back_to_a_supplied_league_id(service, espn):
    """Discovery is the flakiest call we make; a known league id must still work."""
    espn.discovery_error = ESPNUnavailableError("fan API down")
    result = await service.connect(swid=SWID, espn_s2=VALID_COOKIE, league_id="778899")
    assert [league.league_id for league in result.leagues] == ["778899"]
    assert result.discovery_failed is False


async def test_connect_reports_a_discovery_failure_without_blocking(service, espn):
    espn.discovery_error = ESPNUnavailableError("fan API down")
    result = await service.connect(swid=SWID, espn_s2=VALID_COOKIE)
    assert result.discovery_failed is True
    assert result.leagues == []


async def test_reconnect_refreshes_the_cookie_and_keeps_existing_leagues(service, store):
    await service.connect(swid=SWID, espn_s2=VALID_COOKIE, league_id="778899")
    first = store.get(SWID).espn_s2_encrypted
    await service.connect(swid=SWID, espn_s2=VALID_COOKIE)
    record = store.get(SWID)
    assert record.espn_s2_encrypted != first, "re-encrypted with a fresh nonce"
    assert {league.league_id for league in record.leagues} == {"778899", "123456"}


async def test_connect_records_the_users_own_team(service):
    result = await service.connect(swid=SWID, espn_s2=VALID_COOKIE, league_id="123456")
    assert result.leagues[0].team_id == 1
    assert result.leagues[0].team_name == "Gridiron Giants"


# --- league access -----------------------------------------------------------


async def test_reading_a_league_you_have_not_linked_is_refused(connected):
    """Otherwise a valid session plus a guessed id reads strangers' private leagues."""
    with pytest.raises(LeagueAccessError):
        await connected.standings(SWID, "999999", current_season())


async def test_reading_a_linked_league_from_another_season_is_refused(connected):
    with pytest.raises(LeagueAccessError):
        await connected.standings(SWID, "123456", 2019)


async def test_unknown_session_is_not_connected(service):
    with pytest.raises(NotConnectedError):
        await service.standings(OTHER_SWID, "123456", current_season())


async def test_add_league_validates_before_linking(connected, espn):
    league = await connected.add_league(SWID, "778899")
    assert league.league_id == "778899"
    assert league.name == "Test League"
    data = await connected.standings(SWID, "778899", current_season())
    assert len(data["standings"]) == 4


async def test_remove_league_unlinks_it(connected):
    assert connected.remove_league(SWID, "123456", current_season()) is True
    assert connected.remove_league(SWID, "123456", current_season()) is False
    with pytest.raises(LeagueAccessError):
        await connected.standings(SWID, "123456", current_season())


# --- disconnect --------------------------------------------------------------


async def test_disconnect_deletes_everything(connected, store):
    assert connected.disconnect(SWID) is True
    assert store.get(SWID) is None
    assert store.list_all() == []
    assert connected.disconnect(SWID) is False


async def test_disconnect_drops_cached_league_data(connected, espn, store):
    await connected.standings(SWID, "123456", current_season())
    calls_before = len(espn.calls)
    connected.disconnect(SWID)
    await connected.connect(swid=SWID, espn_s2=VALID_COOKIE)
    await connected.standings(SWID, "123456", current_season())
    assert len(espn.calls) > calls_before, "cache must not survive a disconnect"


# --- caching -----------------------------------------------------------------


async def test_repeat_views_hit_the_cache_not_espn(connected, espn):
    await connected.standings(SWID, "123456", current_season())
    calls = len(espn.calls)
    await connected.standings(SWID, "123456", current_season())
    await connected.overview(SWID, "123456", current_season())
    assert len(espn.calls) == calls, "same views + season should be served from cache"


async def test_cache_is_keyed_per_user(settings, store, espn):
    """Two users in one league must not share a cache entry."""
    from espn_dashboard.service import DashboardService

    service = DashboardService(settings, store, espn)
    await service.connect(swid=SWID, espn_s2=VALID_COOKIE)
    await service.connect(swid=OTHER_SWID, espn_s2=VALID_COOKIE)
    await service.standings(SWID, "123456", current_season())
    calls = len(espn.calls)
    await service.standings(OTHER_SWID, "123456", current_season())
    assert len(espn.calls) == calls + 1


# --- views -------------------------------------------------------------------


async def test_overview_bundles_standings_rankings_and_this_week(connected):
    data = await connected.overview(SWID, "123456", current_season())
    assert len(data["standings"]) == 4
    assert len(data["power_rankings"]) == 4
    assert data["league"]["current_week"] == 4
    assert len(data["current_matchups"]) == 2


async def test_matchups_default_to_the_current_week(connected):
    data = await connected.matchups(SWID, "123456", current_season())
    assert data["week"] == 4


async def test_roster_requests_the_scoring_period_from_espn(connected, espn):
    await connected.roster(SWID, "123456", current_season(), team_id=1, week=2)
    roster_calls = [c for c in espn.calls if "mRoster" in c["views"]]
    assert roster_calls[-1]["scoring_period"] == 2


async def test_transactions_send_espns_filter_header(connected, espn):
    data = await connected.transactions(SWID, "123456", current_season())
    assert [row["type"] for row in data["transactions"]] == ["WAIVER", "TRADE_ACCEPTED"]
    call = [c for c in espn.calls if "mTransactions2" in c["views"]][-1]
    assert "x-fantasy-filter" in (call["headers"] or {})


async def test_document_ids_do_not_leak_the_swid():
    assert SWID not in document_id(SWID)
    assert document_id(SWID) != document_id(OTHER_SWID)
