"""Normalization of ESPN payloads, including the shapes that break naive parsers."""

from __future__ import annotations

import pytest

from espn_dashboard.espn import transform
from espn_dashboard.espn.errors import ESPNSchemaError

from .fixtures import SWID, league_payload


@pytest.fixture
def payload():
    return league_payload()


# --- meta and identity -------------------------------------------------------


def test_league_meta(payload):
    meta = transform.league_meta(payload, league_id="123456", season=2026)
    assert meta["name"] == "Test League"
    assert meta["size"] == 4
    assert meta["current_week"] == 4
    assert meta["final_week"] == 14
    assert meta["playoff_team_count"] == 2


def test_team_naming_supports_both_espn_shapes(payload):
    teams = transform.team_index(payload)
    assert teams[1]["name"] == "Gridiron Giants"  # location + nickname
    assert teams[2]["name"] == "Team 2"  # newer `name` field


def test_owners_resolve_to_member_display_names(payload):
    assert transform.team_index(payload)[1]["owners"] == ["nash"]


def test_unknown_owner_does_not_break_the_row():
    payload = league_payload()
    payload["members"] = []
    assert transform.team_index(payload)[1]["owners"] == ["Unknown manager"]


def test_missing_team_name_falls_back_to_the_id():
    assert transform.team_display_name({"id": 7}) == "Team 7"


# --- standings ---------------------------------------------------------------


def test_standings_are_ranked_and_totalled(payload):
    rows = transform.standings(payload)
    assert [row["rank"] for row in rows] == [1, 2, 3, 4]
    leader = rows[0]
    assert leader["wins"] + leader["losses"] == 3
    assert leader["points_for"] == pytest.approx(283.0)
    assert leader["points_for_avg"] == pytest.approx(94.33)  # rounded to 2dp for display


def test_standings_fall_back_to_record_when_espn_omits_seeds():
    payload = league_payload()
    for team in payload["teams"]:
        team["playoffSeed"] = 0
    rows = transform.standings(payload)
    win_pcts = [row["win_pct"] for row in rows]
    assert win_pcts == sorted(win_pcts, reverse=True)


def test_standings_without_teams_is_a_schema_error():
    with pytest.raises(ESPNSchemaError):
        transform.standings({"teams": []})


def test_garbage_field_types_do_not_raise():
    """ESPN occasionally returns nulls where numbers belong."""
    payload = league_payload()
    payload["teams"][0]["record"]["overall"]["pointsFor"] = None
    payload["teams"][1]["record"] = "unexpected"
    rows = transform.standings(payload)
    assert len(rows) == 4
    assert rows[0]["points_for"] >= 0


# --- schedule ----------------------------------------------------------------


def test_matchups_filter_by_week(payload):
    games = transform.matchups(payload, week=1)
    assert len(games) == 2
    assert all(game["week"] == 1 for game in games)
    assert all(game["is_complete"] for game in games)


def test_in_progress_week_is_not_marked_complete(payload):
    assert all(not game["is_complete"] for game in transform.matchups(payload, week=4))


def test_bye_week_has_no_away_side():
    payload = league_payload()
    payload["schedule"].append(
        {"id": 99, "matchupPeriodId": 5, "home": {"teamId": 1, "totalPoints": 90.0}}
    )
    game = transform.matchups(payload, week=5)[0]
    assert game["away"] is None
    assert game["home"]["team_id"] == 1


def test_weekly_scores_exclude_the_live_week(payload):
    weekly = transform.weekly_scores(payload)
    assert sorted(weekly) == [1, 2, 3, 4]
    for rows in weekly.values():
        assert [row["week"] for row in rows] == [1, 2, 3]


def test_weekly_scores_record_the_opponent(payload):
    week_one = transform.weekly_scores(payload)[1][0]
    assert week_one["opponent_id"] == 2
    assert week_one["opponent_points"] == pytest.approx(90.0)


# --- rosters -----------------------------------------------------------------


def test_roster_splits_starters_from_bench_and_ir(payload):
    roster = transform.roster(payload, team_id=1, scoring_period=4)
    assert [player["slot"] for player in roster["starters"]] == ["QB", "RB", "WR"]
    assert {player["slot"] for player in roster["bench"]} == {"BE", "IR"}


def test_roster_totals_only_count_starters(payload):
    roster = transform.roster(payload, team_id=1, scoring_period=4)
    assert roster["starter_points"] == pytest.approx(47.75)
    assert roster["starter_projected_points"] == pytest.approx(55.25)


def test_roster_reads_the_requested_week_not_the_season(payload):
    roster = transform.roster(payload, team_id=1, scoring_period=99)
    # No stats for week 99, but the season total still resolves.
    assert roster["starters"][0]["points"] == 0.0
    assert roster["starters"][0]["season_points"] == pytest.approx(80.0)


def test_roster_for_unknown_team_is_a_schema_error(payload):
    with pytest.raises(ESPNSchemaError):
        transform.roster(payload, team_id=42)


def test_unknown_lineup_slot_is_surfaced_not_dropped():
    payload = league_payload()
    payload["teams"][0]["roster"]["entries"][0]["lineupSlotId"] = 77
    roster = transform.roster(payload, team_id=1, scoring_period=4)
    assert any(player["slot"] == "SLOT_77" for player in roster["starters"])


# --- transactions ------------------------------------------------------------


def test_transactions_are_newest_first_and_exclude_lineup_churn():
    payload = league_payload(include_transactions=True)
    rows = transform.transactions(payload)
    assert [row["transaction_id"] for row in rows] == ["txn-1", "txn-2"]
    assert rows[0]["label"] == "Waiver claim"
    assert rows[0]["bid_amount"] == pytest.approx(17.0)


def test_transaction_players_resolve_to_names_from_rosters():
    payload = league_payload(include_transactions=True)
    names = transform.player_name_index(payload)
    rows = transform.transactions(payload, player_names=names)
    assert rows[0]["items"][0]["player_name"] == "QB 1"


def test_transaction_player_missing_from_rosters_falls_back_to_id():
    payload = league_payload(include_transactions=True)
    payload["transactions"][0]["items"][0]["playerId"] = 999999
    rows = transform.transactions(payload, player_names=transform.player_name_index(payload))
    assert rows[0]["items"][0]["player_name"] == "Player 999999"


def test_transaction_limit_is_honoured():
    payload = league_payload(include_transactions=True)
    assert len(transform.transactions(payload, limit=1)) == 1


def test_owner_swid_matching_is_case_insensitive():
    """ESPN is inconsistent about SWID casing between `members` and `owners`."""
    payload = league_payload()
    payload["members"][0]["id"] = SWID.lower()
    assert transform.member_index(payload)[SWID] == "nash"
    assert transform.team_index(payload)[1]["owners"] == ["nash"]
