"""Export a season's fantasy scoring, per player and per NFL team.

The dashboard answers "how did my league go". This answers the question that
comes before the draft: *where did fantasy points actually come from last
season, and how much of that is still on the same NFL roster*.

Source is nflverse — the same public data behind the ``nflreadpy`` tables in
BigQuery — pulled straight from its GitHub releases, so this runs with no
credentials, no GCP project, and nothing but the standard library.

    python analysis/build_fantasy_season.py --season 2025

Three files land in ``analysis/data``:

``player_fantasy_<season>.csv``
    One row per player: season totals in three scoring formats, per-game rates,
    positional rank, week-to-week consistency, the volume behind the points,
    and where that player is rostered *now*.
``team_fantasy_<season>.csv``
    One row per NFL team and position: what the team's QB/RB/WR/TE room
    produced, and how much of it returns.
``dst_fantasy_<season>.csv``
    Team defense/special teams, scored the way a fantasy site scores it.

A note on why the kicker numbers are computed here: nflverse's
``fantasy_points`` covers passing, rushing, and receiving only, so it reports
every kicker at roughly zero. Kicking and D/ST are scored below from the raw
components, on ESPN's default rules.
"""

from __future__ import annotations

import argparse
import csv
import io
import logging
import statistics
import sys
import urllib.request
from collections import defaultdict
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

logger = logging.getLogger("fantasy-export")

NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"
GAMES_URL = "https://raw.githubusercontent.com/nflverse/nfldata/master/data/games.csv"

# nflverse is not internally consistent about a handful of club codes — the
# roster release says AZ where the stats release says ARI, and the historical
# schedule still carries the pre-relocation names. Everything is normalized on
# read so a player is not reported as changing teams when only the spelling did.
TEAM_ALIASES = {
    "AZ": "ARI",
    "BLT": "BAL",
    "CLV": "CLE",
    "HST": "HOU",
    "LAR": "LA",
    "SL": "STL",
    "OAK": "LV",
    "SD": "LAC",
    "STL": "LA",
}


def normalize_team(code: str) -> str:
    return TEAM_ALIASES.get(code, code)


# Positions that occupy a fantasy roster spot. FB is here because a fullback
# who catches passes scores like a bad running back, not because anyone drafts
# one on purpose.
FANTASY_POSITIONS = ("QB", "RB", "WR", "TE", "K", "FB")

# ESPN's default kicker scoring.
FG_POINTS = {"0_19": 3, "20_29": 3, "30_39": 3, "40_49": 4, "50_59": 5, "60_": 5}
FG_MISS_POINTS = -1
PAT_POINTS = 1
PAT_MISS_POINTS = -1

# ESPN's default D/ST scoring. Points-allowed tiers are (max_allowed, points),
# read in order.
DST_POINTS_ALLOWED = ((0, 5), (6, 4), (13, 3), (17, 1), (27, 0), (34, -1), (45, -3))
DST_POINTS_ALLOWED_FLOOR = -5

# A "boom" week wins you the matchup on its own; a "bust" week is a hole the
# rest of your lineup has to cover. Both in PPR, which is what the thresholds
# are calibrated to.
BOOM_THRESHOLD = 20.0
BUST_THRESHOLD = 5.0


# --------------------------------------------------------------------------
# fetching
# --------------------------------------------------------------------------


def fetch_csv(url: str, cache_dir: Path) -> list[dict[str, str]]:
    """Download a CSV once, then read it from disk on later runs."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / url.rsplit("/", 1)[-1]
    if cached.exists():
        logger.info("cached  %s", cached.name)
        text = cached.read_text(encoding="utf-8")
    else:
        logger.info("fetching %s", url)
        with urllib.request.urlopen(url, timeout=300) as response:
            text = response.read().decode("utf-8")
        cached.write_text(text, encoding="utf-8")
    return list(csv.DictReader(io.StringIO(text)))


# --------------------------------------------------------------------------
# parsing helpers
#
# nflverse writes missing values as "", "NA", or "NaN" depending on the column's
# origin. Every read goes through these so one odd cell cannot abort an export.
# --------------------------------------------------------------------------

_MISSING = {"", "NA", "NaN", "nan", "None", "NULL"}


def num(row: dict[str, str], key: str, default: float = 0.0) -> float:
    raw = (row.get(key) or "").strip()
    if raw in _MISSING:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def whole(row: dict[str, str], key: str) -> int:
    return int(round(num(row, key)))


def text(row: dict[str, str], key: str) -> str:
    raw = (row.get(key) or "").strip()
    return "" if raw in _MISSING else raw


def rounded(value: float, places: int = 2) -> float:
    return round(value + 0.0, places)


# --------------------------------------------------------------------------
# scoring
# --------------------------------------------------------------------------


def kicker_points(row: dict[str, str]) -> float:
    """Score one kicker-week from the field goal and extra point buckets."""
    points = 0.0
    for bucket, value in FG_POINTS.items():
        points += value * num(row, f"fg_made_{bucket}")
    # A blocked kick is a miss as far as scoring is concerned — it went up and
    # no points came down — and ``fg_missed`` counts only the ones that missed
    # on their own, so blocks are added back here.
    points += FG_MISS_POINTS * (num(row, "fg_missed") + num(row, "fg_blocked"))
    points += PAT_POINTS * num(row, "pat_made")
    points += PAT_MISS_POINTS * num(row, "pat_missed")
    return points


def points_allowed_points(points_allowed: int) -> int:
    for ceiling, value in DST_POINTS_ALLOWED:
        if points_allowed <= ceiling:
            return value
    return DST_POINTS_ALLOWED_FLOOR


def score_week(row: dict[str, str], position: str) -> tuple[float, float, float]:
    """Return (standard, half PPR, full PPR) for one player-week.

    For skill positions nflverse has already done the arithmetic: ``fantasy_points``
    is standard scoring and ``fantasy_points_ppr`` adds a point per reception, so
    half PPR is the midpoint of the two. Kickers get no receptions, so all three
    formats are the same number.
    """
    if position == "K":
        points = kicker_points(row)
        return points, points, points
    standard = num(row, "fantasy_points")
    ppr = num(row, "fantasy_points_ppr")
    return standard, (standard + ppr) / 2, ppr


# --------------------------------------------------------------------------
# aggregation
# --------------------------------------------------------------------------

# Volume columns carried through to the output, summed across the season. These
# are what a projection actually leans on: points are the outcome, opportunity
# is the input that repeats.
COUNTING_STATS = (
    "completions",
    "attempts",
    "passing_yards",
    "passing_tds",
    "passing_interceptions",
    "carries",
    "rushing_yards",
    "rushing_tds",
    "receptions",
    "targets",
    "receiving_yards",
    "receiving_tds",
    "fumbles_lost_total",
    "fg_made",
    "fg_att",
    "fg_missed",
    "pat_made",
    "pat_att",
)

# Rate columns averaged over the games a player actually appeared in.
RATE_STATS = ("target_share", "air_yards_share", "wopr")


class PlayerSeason:
    """One player's season, accumulated a week at a time."""

    def __init__(self, player_id: str, name: str, position: str):
        self.player_id = player_id
        self.name = name
        self.position = position
        self.games = 0
        self.totals: defaultdict[str, float] = defaultdict(float)
        self.rates: defaultdict[str, list[float]] = defaultdict(list)
        self.weekly_ppr: list[float] = []
        self.weekly_std: list[float] = []
        self.weekly_half: list[float] = []
        self.fg_50_plus = 0.0
        # Team by week, so a midseason trade shows up as two teams rather than
        # silently collapsing into whichever one the last row happened to name.
        self.team_games: defaultdict[str, int] = defaultdict(int)
        self.team_order: list[str] = []

    def add_week(self, row: dict[str, str]) -> None:
        self.games += 1
        standard, half, ppr = score_week(row, self.position)
        self.weekly_std.append(standard)
        self.weekly_half.append(half)
        self.weekly_ppr.append(ppr)

        for stat in COUNTING_STATS:
            self.totals[stat] += num(row, stat)
        for stat in RATE_STATS:
            self.rates[stat].append(num(row, stat))
        self.fg_50_plus += num(row, "fg_made_50_59") + num(row, "fg_made_60_")

        team = normalize_team(text(row, "team"))
        if team:
            self.team_games[team] += 1
            if team not in self.team_order:
                self.team_order.append(team)

    # -- derived ----------------------------------------------------------

    @property
    def primary_team(self) -> str:
        if not self.team_games:
            return ""
        return max(self.team_games.items(), key=lambda item: item[1])[0]

    @property
    def all_teams(self) -> str:
        return "/".join(self.team_order)

    def total(self, stat: str) -> float:
        return self.totals[stat]

    def rate(self, stat: str) -> float:
        values = self.rates[stat]
        return sum(values) / len(values) if values else 0.0

    @property
    def total_tds(self) -> float:
        return (
            self.totals["passing_tds"]
            + self.totals["rushing_tds"]
            + self.totals["receiving_tds"]
        )

    def consistency(self) -> dict[str, float]:
        weeks = self.weekly_ppr
        if not weeks:
            return {"best": 0.0, "worst": 0.0, "stdev": 0.0, "boom": 0, "bust": 0}
        return {
            "best": max(weeks),
            "worst": min(weeks),
            # A single-game season has no spread to report, and stdev raises on
            # one sample rather than returning zero.
            "stdev": statistics.stdev(weeks) if len(weeks) > 1 else 0.0,
            "boom": sum(1 for week in weeks if week >= BOOM_THRESHOLD),
            "bust": sum(1 for week in weeks if week < BUST_THRESHOLD),
        }


def collect_players(
    weekly_rows: list[dict[str, str]], season_type: str
) -> dict[str, PlayerSeason]:
    players: dict[str, PlayerSeason] = {}
    for row in weekly_rows:
        if text(row, "season_type") != season_type:
            continue
        position = text(row, "position")
        if position not in FANTASY_POSITIONS:
            continue
        player_id = text(row, "player_id")
        if not player_id:
            continue
        player = players.get(player_id)
        if player is None:
            name = text(row, "player_display_name") or text(row, "player_name")
            player = players[player_id] = PlayerSeason(player_id, name, position)
        player.add_week(row)
    return players


def rank_within_position(
    players: list[PlayerSeason], key: str
) -> dict[str, dict[str, int]]:
    """Rank each player against their own position, by season total."""
    ranks: dict[str, dict[str, int]] = defaultdict(dict)
    by_position: defaultdict[str, list[PlayerSeason]] = defaultdict(list)
    for player in players:
        by_position[player.position].append(player)
    for position, group in by_position.items():
        ordered = sorted(group, key=lambda p: sum(getattr(p, key)), reverse=True)
        for index, player in enumerate(ordered, start=1):
            ranks[player.player_id][position] = index
    return ranks


# --------------------------------------------------------------------------
# where a player is now
# --------------------------------------------------------------------------


def build_current_rosters(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    """Map player id to their team on the *following* season's roster."""
    current: dict[str, dict[str, str]] = {}
    for row in rows:
        player_id = text(row, "gsis_id")
        if not player_id:
            continue
        current[player_id] = {
            "team": normalize_team(text(row, "team")),
            "status": text(row, "status"),
            "years_exp": text(row, "years_exp"),
        }
    return current


def build_player_bios(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {
        text(row, "gsis_id"): {
            "birth_date": text(row, "birth_date"),
            "rookie_season": text(row, "rookie_season"),
            "draft_year": text(row, "draft_year"),
            "draft_round": text(row, "draft_round"),
        }
        for row in rows
        if text(row, "gsis_id")
    }


def age_on(birth_date: str, as_of: date) -> float | None:
    """Age in years, to one decimal, or None if the birth date is unusable."""
    try:
        year, month, day = (int(part) for part in birth_date.split("-")[:3])
        born = date(year, month, day)
    except (ValueError, TypeError):
        return None
    return round((as_of - born).days / 365.25, 1)


def roster_movement(
    team_2025: str, current: dict[str, str] | None
) -> tuple[str, str, str]:
    """Return (team_next, status_next, movement) for one player.

    ``movement`` is the column worth sorting on: it says whether last season's
    production is still attached to the same offense.
    """
    if not current or not current.get("team"):
        return "", "", "not rostered"
    team_next = current["team"]
    status_next = current.get("status", "")
    if not team_2025:
        return team_next, status_next, "unknown"
    return team_next, status_next, "same team" if team_next == team_2025 else "new team"


# --------------------------------------------------------------------------
# outputs
# --------------------------------------------------------------------------

PLAYER_COLUMNS = [
    "player_id",
    "player",
    "position",
    "team",
    "teams_played_for",
    "games",
    "fantasy_points_std",
    "fantasy_points_half_ppr",
    "fantasy_points_ppr",
    "ppg_std",
    "ppg_half_ppr",
    "ppg_ppr",
    "pos_rank_ppr",
    "pos_rank_std",
    "best_week_ppr",
    "worst_week_ppr",
    "stdev_ppr",
    "boom_weeks",
    "bust_weeks",
    "completions",
    "pass_attempts",
    "passing_yards",
    "passing_tds",
    "interceptions",
    "carries",
    "rushing_yards",
    "rushing_tds",
    "targets",
    "receptions",
    "receiving_yards",
    "receiving_tds",
    "total_tds",
    "fumbles_lost",
    "target_share",
    "air_yards_share",
    "wopr",
    "fg_made",
    "fg_att",
    "fg_made_50_plus",
    "pat_made",
    "age_next_season",
    "years_exp",
    "team_next_season",
    "status_next_season",
    "movement",
]


def player_rows(
    players: dict[str, PlayerSeason],
    current: dict[str, dict[str, str]],
    bios: dict[str, dict[str, str]],
    next_season: int,
) -> list[dict[str, Any]]:
    ordered = sorted(
        players.values(), key=lambda p: sum(p.weekly_ppr), reverse=True
    )
    ppr_ranks = rank_within_position(ordered, "weekly_ppr")
    std_ranks = rank_within_position(ordered, "weekly_std")
    # Ages are quoted as of kickoff of the season being projected, which is the
    # number that matters when deciding whether a curve is bending.
    as_of = date(next_season, 9, 1)

    rows = []
    for player in ordered:
        spread = player.consistency()
        team = player.primary_team
        bio = bios.get(player.player_id, {})
        team_next, status_next, movement = roster_movement(
            team, current.get(player.player_id)
        )
        total_ppr = sum(player.weekly_ppr)
        total_std = sum(player.weekly_std)
        total_half = sum(player.weekly_half)
        games = player.games or 1

        rows.append(
            {
                "player_id": player.player_id,
                "player": player.name,
                "position": player.position,
                "team": team,
                "teams_played_for": player.all_teams,
                "games": player.games,
                "fantasy_points_std": rounded(total_std, 1),
                "fantasy_points_half_ppr": rounded(total_half, 1),
                "fantasy_points_ppr": rounded(total_ppr, 1),
                "ppg_std": rounded(total_std / games),
                "ppg_half_ppr": rounded(total_half / games),
                "ppg_ppr": rounded(total_ppr / games),
                "pos_rank_ppr": ppr_ranks[player.player_id][player.position],
                "pos_rank_std": std_ranks[player.player_id][player.position],
                "best_week_ppr": rounded(spread["best"], 1),
                "worst_week_ppr": rounded(spread["worst"], 1),
                "stdev_ppr": rounded(spread["stdev"]),
                "boom_weeks": spread["boom"],
                "bust_weeks": spread["bust"],
                "completions": int(player.total("completions")),
                "pass_attempts": int(player.total("attempts")),
                "passing_yards": int(player.total("passing_yards")),
                "passing_tds": int(player.total("passing_tds")),
                "interceptions": int(player.total("passing_interceptions")),
                "carries": int(player.total("carries")),
                "rushing_yards": int(player.total("rushing_yards")),
                "rushing_tds": int(player.total("rushing_tds")),
                "targets": int(player.total("targets")),
                "receptions": int(player.total("receptions")),
                "receiving_yards": int(player.total("receiving_yards")),
                "receiving_tds": int(player.total("receiving_tds")),
                "total_tds": int(player.total_tds),
                "fumbles_lost": int(player.total("fumbles_lost_total")),
                "target_share": rounded(player.rate("target_share"), 3),
                "air_yards_share": rounded(player.rate("air_yards_share"), 3),
                "wopr": rounded(player.rate("wopr"), 3),
                "fg_made": int(player.total("fg_made")),
                "fg_att": int(player.total("fg_att")),
                "fg_made_50_plus": int(player.fg_50_plus),
                "pat_made": int(player.total("pat_made")),
                "age_next_season": age_on(bio.get("birth_date", ""), as_of) or "",
                "years_exp": current.get(player.player_id, {}).get("years_exp", ""),
                "team_next_season": team_next,
                "status_next_season": status_next,
                "movement": movement,
            }
        )
    return rows


TEAM_COLUMNS = [
    "team",
    "position",
    "fantasy_points_ppr",
    "share_of_team_ppr",
    "players_used",
    "top_producer",
    "top_producer_ppr",
    "top_producer_share",
    "returning_ppr",
    "returning_pct",
    "departed_ppr",
]


def team_rows(
    weekly_rows: list[dict[str, str]],
    season_type: str,
    current: dict[str, dict[str, str]],
) -> list[dict[str, Any]]:
    """Roll production up to the NFL team that employed the player that week.

    Attribution is weekly rather than season-long on purpose: a running back
    traded at the deadline produced for two teams, and a depth chart read that
    credits all of it to one of them is wrong about both.
    """
    by_team_pos: defaultdict[tuple[str, str], float] = defaultdict(float)
    by_player: defaultdict[tuple[str, str, str], float] = defaultdict(float)
    names: dict[str, str] = {}

    for row in weekly_rows:
        if text(row, "season_type") != season_type:
            continue
        position = text(row, "position")
        if position not in FANTASY_POSITIONS:
            continue
        team = normalize_team(text(row, "team"))
        player_id = text(row, "player_id")
        if not team or not player_id:
            continue
        _, _, ppr = score_week(row, position)
        by_team_pos[(team, position)] += ppr
        by_player[(team, position, player_id)] += ppr
        names[player_id] = text(row, "player_display_name") or text(row, "player_name")

    team_totals: defaultdict[str, float] = defaultdict(float)
    for (team, _position), points in by_team_pos.items():
        team_totals[team] += points

    rows = []
    for (team, position), points in sorted(by_team_pos.items()):
        members = {
            player_id: value
            for (member_team, member_pos, player_id), value in by_player.items()
            if member_team == team and member_pos == position
        }
        top_id = max(members, key=lambda pid: members[pid]) if members else ""
        top_points = members.get(top_id, 0.0)
        returning = sum(
            value
            for pid, value in members.items()
            if current.get(pid, {}).get("team") == team
        )
        rows.append(
            {
                "team": team,
                "position": position,
                "fantasy_points_ppr": rounded(points, 1),
                "share_of_team_ppr": rounded(points / team_totals[team], 3)
                if team_totals[team]
                else 0.0,
                "players_used": len(members),
                "top_producer": names.get(top_id, ""),
                "top_producer_ppr": rounded(top_points, 1),
                # A room's total can sit below its best player's, because a
                # backup who threw a pick or lost a fumble scored negative
                # points. That makes the share slightly greater than 1, which is
                # the arithmetic being honest rather than a rounding artifact.
                "top_producer_share": rounded(top_points / points, 3) if points else 0.0,
                "returning_ppr": rounded(returning, 1),
                "returning_pct": rounded(returning / points, 3) if points else 0.0,
                "departed_ppr": rounded(points - returning, 1),
            }
        )
    return rows


DST_COLUMNS = [
    "team",
    "games",
    "fantasy_points",
    "ppg",
    "rank",
    "sacks",
    "interceptions",
    "fumbles_recovered",
    "safeties",
    "defensive_tds",
    "special_teams_tds",
    "blocked_kicks",
    "points_allowed",
    "points_allowed_per_game",
    "shutouts",
]


def dst_rows(
    weekly_rows: list[dict[str, str]],
    games: list[dict[str, str]],
    season: int,
    season_type: str,
) -> list[dict[str, Any]]:
    """Score every team's defense/special teams the way a fantasy site does.

    Two halves: the takeaway and touchdown counts come from the player rows
    summed to the team, and points allowed comes from the box score, because
    nothing in the player table records what the *other* offense did.
    """
    game_type = "REG" if season_type == "REG" else "POST"
    allowed: defaultdict[str, list[int]] = defaultdict(list)
    for row in games:
        if text(row, "season") != str(season):
            continue
        if (text(row, "game_type") == "REG") != (game_type == "REG"):
            continue
        home_score, away_score = text(row, "home_score"), text(row, "away_score")
        if not home_score or not away_score:
            continue  # unplayed
        allowed[normalize_team(text(row, "home_team"))].append(int(float(away_score)))
        allowed[normalize_team(text(row, "away_team"))].append(int(float(home_score)))

    tallies: defaultdict[str, defaultdict[str, float]] = defaultdict(
        lambda: defaultdict(float)
    )
    for row in weekly_rows:
        if text(row, "season_type") != season_type:
            continue
        team = normalize_team(text(row, "team"))
        if not team:
            continue
        tally = tallies[team]
        tally["sacks"] += num(row, "def_sacks")
        tally["interceptions"] += num(row, "def_interceptions")
        tally["fumbles_recovered"] += num(row, "fumble_recovery_opp")
        tally["safeties"] += num(row, "def_safeties")
        tally["defensive_tds"] += num(row, "def_tds") + num(row, "fumble_recovery_tds")
        tally["special_teams_tds"] += num(row, "special_teams_tds")
        tally["blocked_kicks"] += (
            num(row, "def_punt_blocks") + num(row, "def_pat_blocks") + num(row, "def_fg_blocks")
        )

    rows = []
    for team, weekly_allowed in allowed.items():
        tally = tallies[team]
        points = (
            1 * tally["sacks"]
            + 2 * tally["interceptions"]
            + 2 * tally["fumbles_recovered"]
            + 2 * tally["safeties"]
            + 6 * (tally["defensive_tds"] + tally["special_teams_tds"])
            + 2 * tally["blocked_kicks"]
            + sum(points_allowed_points(scored) for scored in weekly_allowed)
        )
        games_played = len(weekly_allowed) or 1
        rows.append(
            {
                "team": team,
                "games": len(weekly_allowed),
                "fantasy_points": rounded(points, 1),
                "ppg": rounded(points / games_played),
                "rank": 0,  # filled in below, once every team is scored
                "sacks": rounded(tally["sacks"], 1),
                "interceptions": int(tally["interceptions"]),
                "fumbles_recovered": int(tally["fumbles_recovered"]),
                "safeties": int(tally["safeties"]),
                "defensive_tds": int(tally["defensive_tds"]),
                "special_teams_tds": int(tally["special_teams_tds"]),
                "blocked_kicks": int(tally["blocked_kicks"]),
                "points_allowed": sum(weekly_allowed),
                "points_allowed_per_game": rounded(sum(weekly_allowed) / games_played),
                "shutouts": sum(1 for scored in weekly_allowed if scored == 0),
            }
        )

    rows.sort(key=lambda row: row["fantasy_points"], reverse=True)
    for index, row in enumerate(rows, start=1):
        row["rank"] = index
    return rows


WEEKLY_COLUMNS = [
    "player_id",
    "player",
    "position",
    "team",
    "opponent",
    "week",
    "fantasy_points_std",
    "fantasy_points_half_ppr",
    "fantasy_points_ppr",
]


def weekly_long_rows(
    weekly_rows: list[dict[str, str]], season_type: str
) -> Iterator[dict[str, Any]]:
    for row in weekly_rows:
        if text(row, "season_type") != season_type:
            continue
        position = text(row, "position")
        if position not in FANTASY_POSITIONS:
            continue
        standard, half, ppr = score_week(row, position)
        yield {
            "player_id": text(row, "player_id"),
            "player": text(row, "player_display_name") or text(row, "player_name"),
            "position": position,
            "team": normalize_team(text(row, "team")),
            "opponent": normalize_team(text(row, "opponent_team")),
            "week": whole(row, "week"),
            "fantasy_points_std": rounded(standard, 1),
            "fantasy_points_half_ppr": rounded(half, 1),
            "fantasy_points_ppr": rounded(ppr, 1),
        }


def write_csv(path: Path, columns: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    logger.info("wrote %s (%s rows)", path, len(rows))


# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, default=2025)
    parser.add_argument(
        "--season-type",
        default="REG",
        choices=("REG", "POST"),
        help="REG is what you draft on; POST is four weeks of survivors.",
    )
    parser.add_argument("--out-dir", type=Path, default=Path("analysis/data"))
    parser.add_argument("--cache-dir", type=Path, default=Path("analysis/.cache"))
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    next_season = args.season + 1
    weekly = fetch_csv(
        f"{NFLVERSE}/stats_player/stats_player_week_{args.season}.csv", args.cache_dir
    )
    roster_next = fetch_csv(
        f"{NFLVERSE}/rosters/roster_{next_season}.csv", args.cache_dir
    )
    bios_raw = fetch_csv(f"{NFLVERSE}/players/players.csv", args.cache_dir)
    games = fetch_csv(GAMES_URL, args.cache_dir)

    current = build_current_rosters(roster_next)
    bios = build_player_bios(bios_raw)
    players = collect_players(weekly, args.season_type)
    if not players:
        logger.error("no %s player weeks found for %s", args.season_type, args.season)
        return 1

    suffix = "" if args.season_type == "REG" else f"_{args.season_type.lower()}"
    write_csv(
        args.out_dir / f"player_fantasy_{args.season}{suffix}.csv",
        PLAYER_COLUMNS,
        player_rows(players, current, bios, next_season),
    )
    write_csv(
        args.out_dir / f"player_fantasy_{args.season}{suffix}_weekly.csv",
        WEEKLY_COLUMNS,
        sorted(
            weekly_long_rows(weekly, args.season_type),
            key=lambda row: (row["player"], row["week"]),
        ),
    )
    write_csv(
        args.out_dir / f"team_fantasy_{args.season}{suffix}.csv",
        TEAM_COLUMNS,
        team_rows(weekly, args.season_type, current),
    )
    write_csv(
        args.out_dir / f"dst_fantasy_{args.season}{suffix}.csv",
        DST_COLUMNS,
        dst_rows(weekly, games, args.season, args.season_type),
    )

    logger.info(
        "%s %s: %s players across %s teams",
        args.season,
        args.season_type,
        len(players),
        len({player.primary_team for player in players.values() if player.primary_team}),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
