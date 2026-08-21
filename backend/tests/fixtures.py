"""A small, realistic ESPN league payload.

Shaped like the real thing — brace-wrapped SWIDs in ``members``/``owners``,
``location``+``nickname`` team naming on one team and the newer ``name`` field on
another, ``statSourceId`` 0/1 splits on player stats — so the transforms are
exercised against the variation they actually have to survive.

Four teams, three completed weeks, one week in progress.
"""

from __future__ import annotations

from typing import Any

SWID = "{11111111-1111-1111-1111-111111111111}"
OTHER_SWID = "{22222222-2222-2222-2222-222222222222}"

# Week-by-week scores; team 1 is the "lucky" team and team 3 the "unlucky" one.
WEEK_SCORES: dict[int, dict[int, float]] = {
    1: {1: 100.0, 2: 90.0, 3: 120.0, 4: 130.0},
    2: {1: 95.0, 2: 80.0, 3: 125.0, 4: 70.0},
    3: {1: 88.0, 2: 140.0, 3: 110.0, 4: 60.0},
}
# Week 4 is live: scores exist but no winner is set yet.
LIVE_WEEK = {1: 40.0, 2: 35.0, 3: 55.0, 4: 20.0}

# (week, home, away) — pairings chosen so team 1 wins low and team 3 loses high.
SCHEDULE_PAIRS = [
    (1, 1, 2),
    (1, 3, 4),
    (2, 1, 4),
    (2, 3, 2),
    (3, 1, 4),
    (3, 2, 3),
    (4, 1, 3),
    (4, 2, 4),
]


def _player(
    player_id: int, name: str, position_id: int, week: int, points: float
) -> dict[str, Any]:
    return {
        "playerPoolEntry": {
            "player": {
                "id": player_id,
                "fullName": name,
                "defaultPositionId": position_id,
                "proTeamId": 12,
                "injured": False,
                "injuryStatus": "ACTIVE",
                "stats": [
                    {"scoringPeriodId": week, "statSourceId": 0, "appliedTotal": points},
                    {"scoringPeriodId": week, "statSourceId": 1, "appliedTotal": points + 2.5},
                    {"scoringPeriodId": 0, "statSourceId": 0, "appliedTotal": points * 4},
                ],
            }
        }
    }


def _roster(team_id: int, week: int) -> dict[str, Any]:
    base = team_id * 100
    slots = [
        (0, 1, "QB", 1, 20.0),
        (2, 2, "RB", 2, 15.5),
        (4, 3, "WR", 3, 12.25),
        (20, 4, "Bench", 2, 5.0),
        (21, 5, "IR", 3, 0.0),
    ]
    return {
        "entries": [
            {
                "lineupSlotId": slot_id,
                "playerId": base + offset,
                **_player(base + offset, f"{label} {team_id}", position_id, week, points),
            }
            for slot_id, offset, label, position_id, points in slots
        ]
    }


def _record(team_id: int) -> dict[str, Any]:
    wins = losses = 0
    points_for = points_against = 0.0
    for week, home, away in SCHEDULE_PAIRS:
        if week not in WEEK_SCORES or team_id not in (home, away):
            continue
        opponent = away if home == team_id else home
        mine, theirs = WEEK_SCORES[week][team_id], WEEK_SCORES[week][opponent]
        points_for += mine
        points_against += theirs
        if mine > theirs:
            wins += 1
        else:
            losses += 1
    return {
        "overall": {
            "wins": wins,
            "losses": losses,
            "ties": 0,
            "pointsFor": round(points_for, 2),
            "pointsAgainst": round(points_against, 2),
            "streakLength": 1,
            "streakType": "WIN" if wins else "LOSS",
        }
    }


def league_payload(*, week: int = 4, include_transactions: bool = False) -> dict[str, Any]:
    teams = []
    for team_id in (1, 2, 3, 4):
        team: dict[str, Any] = {
            "id": team_id,
            "abbrev": f"T{team_id}",
            "logo": f"https://example.test/logo{team_id}.png",
            "owners": [SWID if team_id == 1 else OTHER_SWID],
            "primaryOwner": SWID if team_id == 1 else OTHER_SWID,
            "playoffSeed": team_id,
            "record": _record(team_id),
            "roster": _roster(team_id, week),
        }
        if team_id == 1:
            # Older shape: location + nickname, no `name`.
            team["location"] = "Gridiron"
            team["nickname"] = "Giants"
        else:
            team["name"] = f"Team {team_id}"
        teams.append(team)

    schedule = []
    for index, (game_week, home, away) in enumerate(SCHEDULE_PAIRS, start=1):
        scores = WEEK_SCORES.get(game_week, LIVE_WEEK)
        complete = game_week in WEEK_SCORES
        home_points, away_points = scores[home], scores[away]
        schedule.append(
            {
                "id": index,
                "matchupPeriodId": game_week,
                "playoffTierType": "NONE",
                "home": {"teamId": home, "totalPoints": home_points},
                "away": {"teamId": away, "totalPoints": away_points},
                "winner": ("HOME" if home_points > away_points else "AWAY")
                if complete
                else "UNDECIDED",
            }
        )

    payload: dict[str, Any] = {
        "id": 123456,
        "seasonId": 2026,
        "scoringPeriodId": week,
        "status": {
            "currentMatchupPeriod": week,
            "latestScoringPeriod": week,
            "isActive": True,
        },
        "settings": {
            "name": "Test League",
            "size": 4,
            "scheduleSettings": {"matchupPeriodCount": 14, "playoffTeamCount": 2},
        },
        "members": [
            {"id": SWID, "displayName": "nash", "firstName": "Nash", "lastName": "S"},
            {"id": OTHER_SWID, "displayName": "rival"},
        ],
        "teams": teams,
        "schedule": schedule,
    }

    if include_transactions:
        payload["transactions"] = [
            {
                "id": "txn-1",
                "type": "WAIVER",
                "status": "EXECUTED",
                "teamId": 1,
                "bidAmount": 17,
                "proposedDate": 1_700_000_000_000,
                "processDate": 1_700_086_400_000,
                "scoringPeriodId": 3,
                "items": [
                    {"type": "ADD", "playerId": 101, "toTeamId": 1},
                    {"type": "DROP", "playerId": 104, "fromTeamId": 1},
                ],
            },
            {
                "id": "txn-2",
                "type": "TRADE_ACCEPTED",
                "status": "EXECUTED",
                "teamId": 2,
                "proposedDate": 1_699_000_000_000,
                "items": [{"type": "TRADE", "playerId": 201, "fromTeamId": 2, "toTeamId": 3}],
            },
            # Lineup churn: must not appear in the activity log.
            {
                "id": "txn-3",
                "type": "ROSTER",
                "teamId": 3,
                "proposedDate": 1_699_500_000_000,
                "items": [{"type": "LINEUP", "playerId": 301}],
            },
        ]

    return payload


def fan_api_payload() -> dict[str, Any]:
    return {
        "preferences": [
            {
                "typeId": 9,
                "metaData": {
                    "entry": {
                        "entryId": 1,
                        "gameId": 1,
                        "seasonId": 2026,
                        "teamLocation": "Gridiron",
                        "teamNickname": "Giants",
                        "groups": [{"groupId": 123456, "groupName": "Test League"}],
                    }
                },
            },
            {
                # Different sport: must be ignored.
                "typeId": 1,
                "metaData": {
                    "entry": {
                        "gameId": 2,
                        "seasonId": 2026,
                        "groups": [{"groupId": 999}],
                    }
                },
            },
            {
                # Last season's football team: filtered out by season.
                "typeId": 9,
                "metaData": {
                    "entry": {
                        "gameId": 1,
                        "seasonId": 2025,
                        "groups": [{"groupId": 5555}],
                    }
                },
            },
        ]
    }
