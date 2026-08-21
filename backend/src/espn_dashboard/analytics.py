"""Power rankings.

ESPN shows you a standings table; the point of this dashboard is to show what
the standings hide. A 7-3 team that squeaked past bad opponents is not the same
as a 6-4 team that scored the most points in the league, so the ranking blends
four signals:

* **All-play win %** (weight .35) — for every completed week, how often this
  team's score would have beaten each other team's score. This is the single
  best schedule-luck-free measure of how a team has actually played.
* **Scoring** (.25) — points per game, min-max normalized across the league.
* **Recent form** (.25) — the same, over the last ``RECENT_WEEKS`` completed weeks.
* **Actual win %** (.15) — record still counts for something; it is what the
  playoff seeding runs on.

Weights live in ``WEIGHTS`` so they can be tuned in one place, and every
component is returned alongside the score so the UI can explain a ranking rather
than just assert it.
"""

from __future__ import annotations

from typing import Any

RECENT_WEEKS = 3

WEIGHTS: dict[str, float] = {
    "all_play_win_pct": 0.35,
    "scoring": 0.25,
    "recent_form": 0.25,
    "win_pct": 0.15,
}


def _normalize(values: dict[int, float]) -> dict[int, float]:
    """Min-max to 0..1. A league where everyone is equal maps to 0.5 across."""
    if not values:
        return {}
    low, high = min(values.values()), max(values.values())
    if high - low < 1e-9:
        return dict.fromkeys(values, 0.5)
    return {key: (value - low) / (high - low) for key, value in values.items()}


def all_play_records(
    weekly: dict[int, list[dict[str, Any]]],
) -> dict[int, dict[str, int]]:
    """Compare every team's weekly score against every other team's that week."""
    by_week: dict[int, list[tuple[int, float]]] = {}
    for team_id, rows in weekly.items():
        for row in rows:
            by_week.setdefault(row["week"], []).append((team_id, row["points"]))

    records: dict[int, dict[str, int]] = {
        team_id: {"wins": 0, "losses": 0, "ties": 0} for team_id in weekly
    }
    for scores in by_week.values():
        for team_id, points in scores:
            for other_id, other_points in scores:
                if other_id == team_id:
                    continue
                if points > other_points:
                    records[team_id]["wins"] += 1
                elif points < other_points:
                    records[team_id]["losses"] += 1
                else:
                    records[team_id]["ties"] += 1
    return records


def power_rankings(
    standings_rows: list[dict[str, Any]],
    weekly: dict[int, list[dict[str, Any]]],
    *,
    recent_weeks: int = RECENT_WEEKS,
) -> list[dict[str, Any]]:
    """Rank teams best-first, carrying each component through for display."""
    if not standings_rows:
        return []

    all_play = all_play_records(weekly)

    ppg: dict[int, float] = {}
    recent_ppg: dict[int, float] = {}
    for row in standings_rows:
        team_id = row["team_id"]
        games = weekly.get(team_id, [])
        ppg[team_id] = (
            sum(g["points"] for g in games) / len(games)
            if games
            else row.get("points_for_avg", 0.0)
        )
        window = games[-recent_weeks:]
        recent_ppg[team_id] = (
            sum(g["points"] for g in window) / len(window) if window else ppg[team_id]
        )

    normalized_ppg = _normalize(ppg)
    normalized_recent = _normalize(recent_ppg)

    ranked: list[dict[str, Any]] = []
    for row in standings_rows:
        team_id = row["team_id"]
        record = all_play.get(team_id, {"wins": 0, "losses": 0, "ties": 0})
        played = record["wins"] + record["losses"] + record["ties"]
        all_play_pct = (record["wins"] + 0.5 * record["ties"]) / played if played else 0.0

        components = {
            "all_play_win_pct": round(all_play_pct, 3),
            "scoring": round(normalized_ppg.get(team_id, 0.5), 3),
            "recent_form": round(normalized_recent.get(team_id, 0.5), 3),
            "win_pct": round(row.get("win_pct", 0.0), 3),
        }
        score = sum(WEIGHTS[name] * value for name, value in components.items())

        ranked.append(
            {
                "team_id": team_id,
                "name": row.get("name", f"Team {team_id}"),
                "abbrev": row.get("abbrev", ""),
                "logo": row.get("logo", ""),
                "owners": row.get("owners", []),
                "record": f"{row.get('wins', 0)}-{row.get('losses', 0)}"
                + (f"-{row['ties']}" if row.get("ties") else ""),
                "standings_rank": row.get("rank", 0),
                "power_score": round(score * 100, 1),
                "components": components,
                "all_play_record": f"{record['wins']}-{record['losses']}"
                + (f"-{record['ties']}" if record["ties"] else ""),
                "points_per_game": round(ppg.get(team_id, 0.0), 2),
                "recent_points_per_game": round(recent_ppg.get(team_id, 0.0), 2),
                "weeks_counted": len(weekly.get(team_id, [])),
            }
        )

    ranked.sort(key=lambda r: (-r["power_score"], r["standings_rank"]))
    for rank, row in enumerate(ranked, start=1):
        row["rank"] = rank
        # Positive means the power ranking likes them more than their record does.
        row["delta_vs_standings"] = (row["standings_rank"] - rank) if row["standings_rank"] else 0
    return ranked
