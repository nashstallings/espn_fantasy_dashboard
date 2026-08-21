"""Power rankings.

The fixture is built so the answer is knowable by hand: team 3 scores the most
but keeps drawing the high scorer, team 1 wins games it should not. A ranking
worth shipping has to prefer team 3.
"""

from __future__ import annotations

import pytest

from espn_dashboard import analytics
from espn_dashboard.espn import transform

from .fixtures import league_payload


@pytest.fixture
def context():
    payload = league_payload()
    rows = transform.standings(payload)
    return rows, transform.weekly_scores(payload)


def test_all_play_counts_every_pairing_each_week(context):
    _, weekly = context
    records = analytics.all_play_records(weekly)
    # 4 teams, 3 completed weeks => 3 opponents x 3 weeks each.
    for record in records.values():
        assert record["wins"] + record["losses"] + record["ties"] == 9
    # Team 3 outscored 120/125/110 against the field.
    assert records[3]["wins"] == 7


def test_unlucky_high_scorer_outranks_the_lucky_winner(context):
    rows, weekly = context
    rankings = analytics.power_rankings(rows, weekly)
    order = [row["team_id"] for row in rankings]
    assert order.index(3) < order.index(1)


def test_delta_flags_teams_over_and_under_their_record(context):
    rows, weekly = context
    by_team = {row["team_id"]: row for row in analytics.power_rankings(rows, weekly)}
    assert by_team[3]["delta_vs_standings"] > 0  # better than its record
    assert by_team[1]["delta_vs_standings"] < 0  # worse than its record


def test_components_and_weights_stay_in_range(context):
    rows, weekly = context
    assert sum(analytics.WEIGHTS.values()) == pytest.approx(1.0)
    for row in analytics.power_rankings(rows, weekly):
        assert 0.0 <= row["power_score"] <= 100.0
        assert set(row["components"]) == set(analytics.WEIGHTS)
        for value in row["components"].values():
            assert 0.0 <= value <= 1.0


def test_ranks_are_dense_and_ordered(context):
    rows, weekly = context
    rankings = analytics.power_rankings(rows, weekly)
    assert [row["rank"] for row in rankings] == [1, 2, 3, 4]
    scores = [row["power_score"] for row in rankings]
    assert scores == sorted(scores, reverse=True)


def test_recent_form_uses_only_the_last_n_weeks(context):
    rows, weekly = context
    ranked = {r["team_id"]: r for r in analytics.power_rankings(rows, weekly, recent_weeks=1)}
    # Week 3 only: team 2 put up 140.
    assert ranked[2]["recent_points_per_game"] == pytest.approx(140.0)


def test_preseason_league_with_no_completed_games(context):
    """Week 1 has not been played: everything must still rank without dividing by zero."""
    rows, _ = context
    rankings = analytics.power_rankings(rows, {})
    assert len(rankings) == 4
    assert all(row["weeks_counted"] == 0 for row in rankings)
    assert all(row["components"]["all_play_win_pct"] == 0.0 for row in rankings)


def test_empty_league_returns_nothing():
    assert analytics.power_rankings([], {}) == []


def test_identical_teams_normalize_to_the_midpoint():
    rows = [
        {"team_id": i, "name": f"T{i}", "win_pct": 0.5, "points_for_avg": 100.0, "rank": i}
        for i in (1, 2)
    ]
    weekly = {
        i: [{"week": 1, "points": 100.0, "opponent_id": 3 - i, "opponent_points": 100.0}]
        for i in (1, 2)
    }
    rankings = analytics.power_rankings(rows, weekly)
    assert {row["components"]["scoring"] for row in rankings} == {0.5}
