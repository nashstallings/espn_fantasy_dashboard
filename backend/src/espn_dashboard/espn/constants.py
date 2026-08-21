"""ESPN's integer enums, spelled out.

These ids are not published anywhere official — they are the values ESPN's own
fantasy client sends. Unknown values are rendered as ``SLOT_<n>`` / ``POS_<n>``
rather than dropped, so a new position type shows up in the UI as an oddity
instead of vanishing.
"""

from __future__ import annotations

LINEUP_SLOTS: dict[int, str] = {
    0: "QB",
    1: "TQB",
    2: "RB",
    3: "RB/WR",
    4: "WR",
    5: "WR/TE",
    6: "TE",
    7: "OP",
    8: "DT",
    9: "DE",
    10: "LB",
    11: "DL",
    12: "CB",
    13: "S",
    14: "DB",
    15: "DP",
    16: "D/ST",
    17: "K",
    18: "P",
    19: "HC",
    20: "BE",
    21: "IR",
    23: "FLEX",
    24: "EDR",
}

BENCH_SLOTS = frozenset({20, 21})

POSITIONS: dict[int, str] = {
    1: "QB",
    2: "RB",
    3: "WR",
    4: "TE",
    5: "K",
    7: "P",
    9: "DT",
    10: "DE",
    11: "LB",
    12: "CB",
    13: "S",
    14: "HC",
    16: "D/ST",
}

PRO_TEAMS: dict[int, str] = {
    0: "FA",
    1: "ATL",
    2: "BUF",
    3: "CHI",
    4: "CIN",
    5: "CLE",
    6: "DAL",
    7: "DEN",
    8: "DET",
    9: "GB",
    10: "TEN",
    11: "IND",
    12: "KC",
    13: "LV",
    14: "LAR",
    15: "MIA",
    16: "MIN",
    17: "NE",
    18: "NO",
    19: "NYG",
    20: "NYJ",
    21: "PHI",
    22: "ARI",
    23: "PIT",
    24: "LAC",
    25: "SF",
    26: "SEA",
    27: "TB",
    28: "WSH",
    29: "CAR",
    30: "JAX",
    33: "BAL",
    34: "HOU",
}

TRANSACTION_LABELS: dict[str, str] = {
    "WAIVER": "Waiver claim",
    "FREEAGENT": "Free agent",
    "TRADE_ACCEPTED": "Trade",
    "TRADE_PROPOSAL": "Trade proposed",
    "TRADE_DECLINED": "Trade declined",
    "TRADE_UPHELD": "Trade upheld",
    "ROSTER": "Lineup change",
    "DRAFT": "Draft pick",
    "LINEUP": "Lineup change",
}

ITEM_LABELS: dict[str, str] = {
    "ADD": "added",
    "DROP": "dropped",
    "LINEUP": "moved",
    "TRADE": "traded",
}


def lineup_slot(slot_id: int | None) -> str:
    if slot_id is None:
        return "?"
    return LINEUP_SLOTS.get(slot_id, f"SLOT_{slot_id}")


def position(position_id: int | None) -> str:
    if position_id is None:
        return "?"
    return POSITIONS.get(position_id, f"POS_{position_id}")


def pro_team(team_id: int | None) -> str:
    if team_id is None:
        return "FA"
    return PRO_TEAMS.get(team_id, f"TEAM_{team_id}")
