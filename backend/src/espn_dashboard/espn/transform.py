"""Turn raw ESPN payloads into the shapes the dashboard renders.

Everything here assumes the upstream response is *approximately* right and
nothing more. ESPN can rename a field, move a team's name from
``location``+``nickname`` to ``name``, or omit a block entirely for a league
that has not drafted yet. So every accessor is total: missing values become
sensible defaults, and a malformed sub-object is skipped rather than raising.
The one thing we refuse to guess at is the league object itself — if there are
no teams, the caller gets an explicit schema error.
"""

from __future__ import annotations

from typing import Any

from .constants import (
    BENCH_SLOTS,
    ITEM_LABELS,
    TRANSACTION_LABELS,
    lineup_slot,
    position,
    pro_team,
)
from .errors import ESPNSchemaError

UNDECIDED = "UNDECIDED"


# --- total accessors ---------------------------------------------------------


def _obj(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return default


def _int(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    text = str(value).strip()
    return text or default


# --- teams and members -------------------------------------------------------


def team_display_name(team: dict[str, Any]) -> str:
    """ESPN moved team naming around; support both shapes."""
    name = _text(team.get("name"))
    if name:
        return name
    parts = [_text(team.get("location")), _text(team.get("nickname"))]
    combined = " ".join(p for p in parts if p)
    return combined or f"Team {_int(team.get('id'), 0)}"


def member_index(payload: dict[str, Any]) -> dict[str, str]:
    """SWID -> display name, for attributing teams and transactions to people."""
    index: dict[str, str] = {}
    for member in _list(payload.get("members")):
        member = _obj(member)
        swid = _text(member.get("id")).upper()
        if not swid:
            continue
        display = _text(member.get("displayName"))
        if not display:
            display = " ".join(
                p for p in (_text(member.get("firstName")), _text(member.get("lastName"))) if p
            )
        index[swid] = display or "Unknown manager"
    return index


def _owner_names(team: dict[str, Any], members: dict[str, str]) -> list[str]:
    owners = _list(team.get("owners")) or [team.get("primaryOwner")]
    names = []
    for owner in owners:
        swid = _text(owner).upper()
        if swid:
            names.append(members.get(swid, "Unknown manager"))
    return names


def team_index(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """team_id -> lightweight identity, used to label matchups and transactions."""
    members = member_index(payload)
    index: dict[int, dict[str, Any]] = {}
    for team in _list(payload.get("teams")):
        team = _obj(team)
        team_id = _int(team.get("id"))
        if team_id is None:
            continue
        index[team_id] = {
            "team_id": team_id,
            "name": team_display_name(team),
            "abbrev": _text(team.get("abbrev")),
            "logo": _text(team.get("logo")),
            "owners": _owner_names(team, members),
        }
    return index


# --- league meta -------------------------------------------------------------


def league_meta(payload: dict[str, Any], *, league_id: str, season: int) -> dict[str, Any]:
    settings = _obj(payload.get("settings"))
    schedule_settings = _obj(settings.get("scheduleSettings"))
    status = _obj(payload.get("status"))
    current_week = (
        _int(status.get("currentMatchupPeriod"))
        or _int(payload.get("scoringPeriodId"))
        or 1
    )
    return {
        "league_id": _text(payload.get("id"), league_id),
        "season": _int(payload.get("seasonId"), season),
        "name": _text(settings.get("name"), f"League {league_id}"),
        "size": _int(settings.get("size"), 0),
        "current_week": current_week,
        "latest_scoring_period": _int(status.get("latestScoringPeriod"), current_week),
        "final_week": _int(schedule_settings.get("matchupPeriodCount"), 0),
        "playoff_team_count": _int(schedule_settings.get("playoffTeamCount"), 0),
        "is_active": bool(status.get("isActive", True)),
    }


# --- standings ---------------------------------------------------------------


def standings(payload: dict[str, Any]) -> list[dict[str, Any]]:
    teams = _list(payload.get("teams"))
    if not teams:
        raise ESPNSchemaError("league response contained no teams")

    members = member_index(payload)
    rows: list[dict[str, Any]] = []
    for team in teams:
        team = _obj(team)
        team_id = _int(team.get("id"))
        if team_id is None:
            continue
        overall = _obj(_obj(team.get("record")).get("overall"))
        wins = _int(overall.get("wins"), 0) or 0
        losses = _int(overall.get("losses"), 0) or 0
        ties = _int(overall.get("ties"), 0) or 0
        played = wins + losses + ties
        rows.append(
            {
                "team_id": team_id,
                "name": team_display_name(team),
                "abbrev": _text(team.get("abbrev")),
                "logo": _text(team.get("logo")),
                "owners": _owner_names(team, members),
                "wins": wins,
                "losses": losses,
                "ties": ties,
                "win_pct": round((wins + 0.5 * ties) / played, 3) if played else 0.0,
                "points_for": _num(overall.get("pointsFor")),
                "points_against": _num(overall.get("pointsAgainst")),
                "points_for_avg": round(_num(overall.get("pointsFor")) / played, 2)
                if played
                else 0.0,
                "streak": _streak(overall),
                "playoff_seed": _int(team.get("playoffSeed"), 0),
                "games_played": played,
            }
        )

    # ESPN's own seed is authoritative when present (it encodes tiebreakers we
    # cannot reproduce); otherwise fall back to record then points.
    if all(row["playoff_seed"] for row in rows):
        rows.sort(key=lambda r: r["playoff_seed"])
    else:
        rows.sort(key=lambda r: (-r["win_pct"], -r["points_for"]))
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    return rows


def _streak(overall: dict[str, Any]) -> str:
    length = _int(overall.get("streakLength"), 0) or 0
    kind = _text(overall.get("streakType"))
    if not length or not kind:
        return "-"
    letter = {"WIN": "W", "LOSS": "L", "TIE": "T"}.get(kind.upper(), kind[:1].upper())
    return f"{letter}{length}"


# --- schedule and matchups ---------------------------------------------------


def _side(raw: Any) -> dict[str, Any] | None:
    side = _obj(raw)
    team_id = _int(side.get("teamId"))
    if team_id is None:
        return None
    return {"team_id": team_id, "points": _num(side.get("totalPoints"))}


def matchups(payload: dict[str, Any], *, week: int | None = None) -> list[dict[str, Any]]:
    teams = team_index(payload)
    results: list[dict[str, Any]] = []
    for game in _list(payload.get("schedule")):
        game = _obj(game)
        matchup_week = _int(game.get("matchupPeriodId"))
        if matchup_week is None or (week is not None and matchup_week != week):
            continue
        home = _side(game.get("home"))
        away = _side(game.get("away"))
        if home is None and away is None:
            continue
        winner = _text(game.get("winner"), UNDECIDED).upper()
        results.append(
            {
                "matchup_id": _int(game.get("id")),
                "week": matchup_week,
                "playoff_tier": _text(game.get("playoffTierType"), "NONE"),
                "winner": winner,
                "is_complete": winner not in (UNDECIDED, ""),
                "home": _decorate_side(home, teams),
                # A bye week has no away side; the UI renders that as "BYE".
                "away": _decorate_side(away, teams),
            }
        )
    results.sort(key=lambda m: (m["week"], m["matchup_id"] or 0))
    return results


def _decorate_side(side: dict[str, Any] | None, teams: dict[int, dict[str, Any]]) -> Any:
    if side is None:
        return None
    identity = teams.get(side["team_id"], {})
    return {
        "team_id": side["team_id"],
        "name": identity.get("name", f"Team {side['team_id']}"),
        "abbrev": identity.get("abbrev", ""),
        "logo": identity.get("logo", ""),
        "owners": identity.get("owners", []),
        "points": side["points"],
    }


def weekly_scores(payload: dict[str, Any]) -> dict[int, list[dict[str, Any]]]:
    """team_id -> completed weekly scores, oldest first.

    Only completed matchups count: an in-progress week would otherwise drag every
    team's averages toward zero and scramble the power rankings mid-Sunday.
    """
    scores: dict[int, list[dict[str, Any]]] = {}
    for game in _list(payload.get("schedule")):
        game = _obj(game)
        winner = _text(game.get("winner"), UNDECIDED).upper()
        if winner in (UNDECIDED, ""):
            continue
        week = _int(game.get("matchupPeriodId"))
        if week is None:
            continue
        home, away = _side(game.get("home")), _side(game.get("away"))
        for side, opponent in ((home, away), (away, home)):
            if side is None:
                continue
            scores.setdefault(side["team_id"], []).append(
                {
                    "week": week,
                    "points": side["points"],
                    "opponent_id": opponent["team_id"] if opponent else None,
                    "opponent_points": opponent["points"] if opponent else None,
                }
            )
    for rows in scores.values():
        rows.sort(key=lambda r: r["week"])
    return scores


# --- rosters -----------------------------------------------------------------


def _player_stats(player: dict[str, Any], *, scoring_period: int | None) -> dict[str, float]:
    """Applied points for the requested week, plus the season total.

    statSourceId 0 = actual, 1 = projection. statSplitTypeId 1 = single week.
    """
    actual = projected = season_total = 0.0
    for stat in _list(player.get("stats")):
        stat = _obj(stat)
        source = _int(stat.get("statSourceId"), -1)
        period = _int(stat.get("scoringPeriodId"))
        applied = _num(stat.get("appliedTotal"))
        if source == 0 and period == 0:
            season_total = applied
        if scoring_period is not None and period == scoring_period:
            if source == 0:
                actual = applied
            elif source == 1:
                projected = applied
    return {"points": actual, "projected_points": projected, "season_points": season_total}


def _roster_entry(entry: Any, *, scoring_period: int | None) -> dict[str, Any] | None:
    entry = _obj(entry)
    pool = _obj(entry.get("playerPoolEntry"))
    player = _obj(pool.get("player"))
    player_id = _int(player.get("id")) or _int(entry.get("playerId"))
    if player_id is None:
        return None
    slot_id = _int(entry.get("lineupSlotId"))
    stats = _player_stats(player, scoring_period=scoring_period)
    return {
        "player_id": player_id,
        "name": _text(player.get("fullName"), f"Player {player_id}"),
        "slot": lineup_slot(slot_id),
        "slot_id": slot_id,
        "is_starter": slot_id not in BENCH_SLOTS if slot_id is not None else False,
        "position": position(_int(player.get("defaultPositionId"))),
        "pro_team": pro_team(_int(player.get("proTeamId"))),
        "injured": bool(player.get("injured", False)),
        "injury_status": _text(player.get("injuryStatus"), "ACTIVE"),
        "acquisition_type": _text(entry.get("acquisitionType")),
        **stats,
    }


def roster(
    payload: dict[str, Any], *, team_id: int, scoring_period: int | None = None
) -> dict[str, Any]:
    members = member_index(payload)
    for team in _list(payload.get("teams")):
        team = _obj(team)
        if _int(team.get("id")) != team_id:
            continue
        entries = [
            _roster_entry(e, scoring_period=scoring_period)
            for e in _list(_obj(team.get("roster")).get("entries"))
        ]
        players = [e for e in entries if e is not None]
        starters = [p for p in players if p["is_starter"]]
        bench = [p for p in players if not p["is_starter"]]
        starters.sort(key=lambda p: (p["slot_id"] if p["slot_id"] is not None else 99))
        bench.sort(key=lambda p: (p["slot_id"] if p["slot_id"] is not None else 99, p["name"]))
        return {
            "team_id": team_id,
            "name": team_display_name(team),
            "abbrev": _text(team.get("abbrev")),
            "logo": _text(team.get("logo")),
            "owners": _owner_names(team, members),
            "scoring_period": scoring_period,
            "starters": starters,
            "bench": bench,
            "starter_points": round(sum(p["points"] for p in starters), 2),
            "starter_projected_points": round(
                sum(p["projected_points"] for p in starters), 2
            ),
        }
    raise ESPNSchemaError(f"league response has no team {team_id}")


# --- transactions ------------------------------------------------------------


def player_name_index(payload: dict[str, Any]) -> dict[int, str]:
    """player_id -> name, gathered from every roster in the league.

    ESPN's transaction feed carries player ids and no names. Players still on
    some roster resolve here; anyone dropped out of the league entirely does
    not, and the UI falls back to the id.
    """
    names: dict[int, str] = {}
    for team in _list(payload.get("teams")):
        for entry in _list(_obj(_obj(team).get("roster")).get("entries")):
            entry = _obj(entry)
            player = _obj(_obj(entry.get("playerPoolEntry")).get("player"))
            player_id = _int(player.get("id")) or _int(entry.get("playerId"))
            name = _text(player.get("fullName"))
            if player_id is not None and name:
                names[player_id] = name
    return names


def transactions(
    payload: dict[str, Any],
    *,
    limit: int = 100,
    player_names: dict[int, str] | None = None,
) -> list[dict[str, Any]]:
    """Flatten ESPN's transaction blob into a readable activity log.

    ESPN returns the log under different keys depending on the view that
    populated it, and lineup-only churn dwarfs the interesting moves, so those
    are dropped here rather than in the UI.
    """
    teams = team_index(payload)
    raw = _list(payload.get("transactions")) or _list(payload.get("topics"))
    rows: list[dict[str, Any]] = []
    for txn in raw:
        txn = _obj(txn)
        kind = _text(txn.get("type"), "UNKNOWN").upper()
        if kind in ("ROSTER", "LINEUP"):
            continue
        items = [
            item
            for item in (
                _transaction_item(i, teams, player_names or {}) for i in _list(txn.get("items"))
            )
            if item is not None
        ]
        if not items:
            continue
        team_id = _int(txn.get("teamId"))
        rows.append(
            {
                "transaction_id": _text(txn.get("id")),
                "type": kind,
                "label": TRANSACTION_LABELS.get(kind, kind.replace("_", " ").title()),
                "status": _text(txn.get("status"), "EXECUTED"),
                "team_id": team_id,
                "team_name": teams.get(team_id, {}).get("name", "") if team_id else "",
                "bid_amount": _num(txn.get("bidAmount")),
                "proposed_date_ms": _int(txn.get("proposedDate"), 0),
                "processed_date_ms": _int(txn.get("processDate"), 0)
                or _int(txn.get("proposedDate"), 0),
                "scoring_period": _int(txn.get("scoringPeriodId")),
                "items": items,
            }
        )
    rows.sort(key=lambda r: r["processed_date_ms"] or 0, reverse=True)
    return rows[:limit]


def _transaction_item(
    raw: Any, teams: dict[int, dict[str, Any]], player_names: dict[int, str]
) -> dict[str, Any] | None:
    item = _obj(raw)
    player_id = _int(item.get("playerId"))
    if player_id is None:
        return None
    action = _text(item.get("type"), "UNKNOWN").upper()
    from_id, to_id = _int(item.get("fromTeamId")), _int(item.get("toTeamId"))
    return {
        "player_id": player_id,
        # ESPN omits names from the transaction feed; resolve from rosters where
        # possible and fall back to the raw id for players no longer in the league.
        "player_name": player_names.get(player_id, f"Player {player_id}"),
        "action": action,
        "action_label": ITEM_LABELS.get(action, action.title()),
        "from_team_id": from_id or None,
        "from_team_name": teams.get(from_id, {}).get("name", "") if from_id else "",
        "to_team_id": to_id or None,
        "to_team_name": teams.get(to_id, {}).get("name", "") if to_id else "",
    }
