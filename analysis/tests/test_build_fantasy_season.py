"""Tests for the parts of the export that are computed rather than copied.

nflverse hands us passing, rushing, and receiving points already totalled, so
those need no test. Kicking, D/ST, half PPR, and the club-code normalization are
arithmetic this script does itself, and each one is quietly wrong in a way that
looks plausible in a spreadsheet — a kicker at zero, a defense ranked by sacks
alone, a player reported as traded because two files spell Arizona differently.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from build_fantasy_season import (  # noqa: E402
    PlayerSeason,
    kicker_points,
    normalize_team,
    num,
    points_allowed_points,
    roster_movement,
    score_week,
    team_rows,
)


def kicker_week(**stats: object) -> dict[str, str]:
    return {key: str(value) for key, value in stats.items()}


class TestParsing:
    @pytest.mark.parametrize("missing", ["", "NA", "NaN", "None", "NULL"])
    def test_missing_markers_read_as_zero(self, missing: str) -> None:
        assert num({"targets": missing}, "targets") == 0.0

    def test_unparseable_value_does_not_raise(self) -> None:
        assert num({"targets": "seven"}, "targets") == 0.0

    def test_absent_column_uses_the_default(self) -> None:
        assert num({}, "targets", default=-1.0) == -1.0


class TestKickerScoring:
    def test_field_goals_are_worth_more_from_distance(self) -> None:
        short = kicker_points(kicker_week(fg_made_30_39=1))
        medium = kicker_points(kicker_week(fg_made_40_49=1))
        long = kicker_points(kicker_week(fg_made_50_59=1))
        assert (short, medium, long) == (3, 4, 5)

    def test_sixty_plus_scores_like_a_fifty(self) -> None:
        assert kicker_points(kicker_week(fg_made_60_=1)) == 5

    def test_extra_points_and_misses(self) -> None:
        row = kicker_week(pat_made=4, pat_missed=1, fg_made_20_29=2, fg_missed=1)
        assert kicker_points(row) == 4 - 1 + 6 - 1

    def test_a_blocked_kick_costs_the_same_as_a_miss(self) -> None:
        assert kicker_points(kicker_week(fg_blocked=1)) == -1

    def test_a_full_game_line(self) -> None:
        # 2 short, 1 from 40-49, 1 from 50+, 3 extra points, 1 miss.
        row = kicker_week(
            fg_made_20_29=1, fg_made_30_39=1, fg_made_40_49=1, fg_made_50_59=1,
            fg_missed=1, pat_made=3,
        )
        assert kicker_points(row) == 3 + 3 + 4 + 5 - 1 + 3

    def test_nflverse_fantasy_points_are_ignored_for_kickers(self) -> None:
        """The reason this module scores kickers at all: nflverse reports ~0."""
        row = kicker_week(fg_made_40_49=2, pat_made=3, fantasy_points=0, fantasy_points_ppr=0)
        assert score_week(row, "K") == (11.0, 11.0, 11.0)


class TestSkillScoring:
    def test_half_ppr_sits_midway_between_the_two_formats(self) -> None:
        row = {"fantasy_points": "10.0", "fantasy_points_ppr": "18.0"}
        standard, half, ppr = score_week(row, "WR")
        assert (standard, half, ppr) == (10.0, 14.0, 18.0)

    def test_a_player_with_no_catches_scores_the_same_in_every_format(self) -> None:
        row = {"fantasy_points": "22.5", "fantasy_points_ppr": "22.5"}
        assert score_week(row, "QB") == (22.5, 22.5, 22.5)

    def test_negative_weeks_survive_the_midpoint(self) -> None:
        row = {"fantasy_points": "-2.0", "fantasy_points_ppr": "-1.0"}
        assert score_week(row, "RB") == (-2.0, -1.5, -1.0)


class TestPointsAllowed:
    @pytest.mark.parametrize(
        ("allowed", "expected"),
        [(0, 5), (6, 4), (7, 3), (13, 3), (14, 1), (17, 1), (18, 0), (27, 0),
         (28, -1), (34, -1), (35, -3), (45, -3), (46, -5), (70, -5)],
    )
    def test_tier_boundaries(self, allowed: int, expected: int) -> None:
        assert points_allowed_points(allowed) == expected

    def test_tiers_never_increase_as_the_defense_gets_worse(self) -> None:
        scores = [points_allowed_points(allowed) for allowed in range(0, 80)]
        assert scores == sorted(scores, reverse=True)


class TestTeamCodes:
    def test_the_roster_spelling_of_arizona_matches_the_stats_spelling(self) -> None:
        assert normalize_team("AZ") == normalize_team("ARI") == "ARI"

    def test_relocated_clubs_map_to_their_current_code(self) -> None:
        assert normalize_team("OAK") == "LV"
        assert normalize_team("SD") == "LAC"

    def test_an_unknown_code_passes_through_untouched(self) -> None:
        assert normalize_team("KC") == "KC"

    def test_a_spelling_difference_is_not_reported_as_a_trade(self) -> None:
        """The bug this normalization exists to prevent."""
        _, _, movement = roster_movement("ARI", {"team": normalize_team("AZ")})
        assert movement == "same team"


class TestRosterMovement:
    def test_a_player_on_a_different_team_is_flagged(self) -> None:
        team, status, movement = roster_movement("NO", {"team": "SEA", "status": "ACT"})
        assert (team, status, movement) == ("SEA", "ACT", "new team")

    def test_a_player_on_no_roster_is_flagged(self) -> None:
        assert roster_movement("KC", None)[2] == "not rostered"
        assert roster_movement("KC", {"team": ""})[2] == "not rostered"


class TestPlayerSeason:
    def _season(self, *weeks: dict[str, str]) -> PlayerSeason:
        player = PlayerSeason("00-0000001", "Test Player", "RB")
        for week in weeks:
            player.add_week(week)
        return player

    def test_a_single_game_reports_no_spread_rather_than_raising(self) -> None:
        player = self._season({"fantasy_points": "8", "fantasy_points_ppr": "12", "team": "SF"})
        assert player.consistency()["stdev"] == 0.0

    def test_boom_and_bust_weeks_are_counted_in_ppr(self) -> None:
        player = self._season(
            {"fantasy_points": "18", "fantasy_points_ppr": "24", "team": "SF"},
            {"fantasy_points": "1", "fantasy_points_ppr": "2", "team": "SF"},
            {"fantasy_points": "9", "fantasy_points_ppr": "12", "team": "SF"},
        )
        spread = player.consistency()
        assert (spread["boom"], spread["bust"]) == (1, 1)
        assert (spread["best"], spread["worst"]) == (24.0, 2.0)

    def test_a_trade_records_both_teams_in_order(self) -> None:
        player = self._season(
            {"fantasy_points": "5", "fantasy_points_ppr": "7", "team": "NO"},
            {"fantasy_points": "5", "fantasy_points_ppr": "7", "team": "NO"},
            {"fantasy_points": "5", "fantasy_points_ppr": "7", "team": "SEA"},
        )
        assert player.all_teams == "NO/SEA"
        assert player.primary_team == "NO"  # more games there, not the last one

    def test_arizona_is_recorded_under_one_code(self) -> None:
        player = self._season(
            {"fantasy_points": "5", "fantasy_points_ppr": "7", "team": "ARI"},
            {"fantasy_points": "5", "fantasy_points_ppr": "7", "team": "AZ"},
        )
        assert player.all_teams == "ARI"


class TestTeamRollup:
    def week(self, player_id: str, name: str, team: str, ppr: float) -> dict[str, str]:
        return {
            "season_type": "REG",
            "position": "RB",
            "team": team,
            "player_id": player_id,
            "player_display_name": name,
            "fantasy_points": str(ppr - 2),
            "fantasy_points_ppr": str(ppr),
        }

    def test_production_follows_the_player_to_the_new_team(self) -> None:
        """A midseason trade splits the season between two offenses."""
        rows = team_rows(
            [
                self.week("1", "Traded Back", "NO", 100.0),
                self.week("1", "Traded Back", "SEA", 40.0),
            ],
            "REG",
            current={"1": {"team": "SEA"}},
        )
        by_team = {row["team"]: row for row in rows}
        assert by_team["NO"]["fantasy_points_ppr"] == 100.0
        assert by_team["SEA"]["fantasy_points_ppr"] == 40.0
        # New Orleans keeps the points it got and loses all of them for next year.
        assert by_team["NO"]["returning_ppr"] == 0.0
        assert by_team["SEA"]["returning_pct"] == 1.0

    def test_returning_share_counts_only_players_still_on_that_roster(self) -> None:
        rows = team_rows(
            [
                self.week("1", "Stays", "KC", 150.0),
                self.week("2", "Leaves", "KC", 50.0),
            ],
            "REG",
            current={"1": {"team": "KC"}, "2": {"team": "LV"}},
        )
        (row,) = rows
        assert row["fantasy_points_ppr"] == 200.0
        assert row["returning_ppr"] == 150.0
        assert row["returning_pct"] == 0.75
        assert row["departed_ppr"] == 50.0
        assert row["players_used"] == 2
        assert row["top_producer"] == "Stays"

    def test_postseason_weeks_are_excluded_from_a_regular_season_rollup(self) -> None:
        playoff = self.week("1", "Playoff Only", "KC", 30.0) | {"season_type": "POST"}
        assert team_rows([playoff], "REG", current={}) == []
