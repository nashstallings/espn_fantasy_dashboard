"""Build the dashboard as static JSON, for GitHub Pages.

There is no server in this path. A scheduled GitHub Action runs this, writes a
tree of JSON next to the frontend, and Pages publishes the result — so the site
costs nothing to run and has nothing to keep alive.

Two consequences worth holding in mind:

* **The data is as fresh as the last build**, not as fresh as the page load.
  Every payload carries ``generated_at`` and the UI shows it, because numbers
  that look live but are twenty minutes old are worse than numbers that admit
  their age.
* **Cookies come from CI secrets**, not from a user. Nothing here writes them
  anywhere: they are read from the environment, used for the fetch, and never
  land in the output tree. ``verify_output_is_clean`` enforces that.

The transforms, power rankings, and ESPN client are shared with the server
build; only the delivery mechanism differs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import analytics
from .espn import transform
from .espn.client import ESPNClient, ESPNCredentials, normalize_swid
from .espn.errors import ESPNError
from .service import (
    OVERVIEW_VIEWS,
    ROSTER_VIEWS,
    TRANSACTION_FILTER,
    TRANSACTION_VIEWS,
    current_season,
)

logger = logging.getLogger(__name__)

# Rosters are fetched one scoring period at a time, so a full season is ~17
# extra ESPN calls per build. Capped so a misreported week count cannot turn
# one build into hundreds of requests.
MAX_ROSTER_WEEKS = 18


class BuildError(RuntimeError):
    """The build could not produce a usable site."""


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


async def build(
    *,
    credentials: ESPNCredentials,
    league_id: str,
    season: int,
    out_dir: Path,
    client: ESPNClient | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    client = client or ESPNClient()
    generated_at = now or datetime.now(UTC)

    # One request covers settings, teams, and the whole schedule — the same
    # bundling the server used, for the same reason.
    payload = await client.fetch_league(
        season, league_id, OVERVIEW_VIEWS, credentials=credentials
    )
    meta = transform.league_meta(payload, league_id=league_id, season=season)
    standings = transform.standings(payload)
    weekly = transform.weekly_scores(payload)
    rankings = analytics.power_rankings(standings, weekly)
    all_matchups = transform.matchups(payload)
    teams = list(transform.team_index(payload).values())

    stamp = {
        "generated_at": generated_at.isoformat(),
        "league": meta,
    }

    _write(out_dir / "meta.json", stamp)
    _write(out_dir / "standings.json", {**stamp, "standings": standings})
    _write(out_dir / "matchups.json", {**stamp, "matchups": all_matchups})
    _write(out_dir / "teams.json", {**stamp, "teams": teams})
    _write(
        out_dir / "power-rankings.json",
        {**stamp, "power_rankings": rankings, "weights": analytics.WEIGHTS},
    )
    _write(
        out_dir / "overview.json",
        {
            **stamp,
            "standings": standings,
            "power_rankings": rankings,
            "current_matchups": [
                game for game in all_matchups if game["week"] == meta["current_week"]
            ],
        },
    )

    # Transactions are a separate view with their own filter header.
    transactions: list[dict[str, Any]] = []
    try:
        txn_payload = await client.fetch_league(
            season,
            league_id,
            TRANSACTION_VIEWS,
            credentials=credentials,
            headers={"x-fantasy-filter": json.dumps(TRANSACTION_FILTER)},
        )
        transactions = transform.transactions(
            txn_payload, player_names=transform.player_name_index(txn_payload)
        )
    except ESPNError as exc:
        # A missing activity log should not cost you the standings.
        logger.warning("transactions unavailable, publishing without them: %s", exc)
    _write(out_dir / "transactions.json", {**stamp, "transactions": transactions})

    # One roster file per scoring period, each holding every team.
    last_week = min(max(meta["latest_scoring_period"], 1), MAX_ROSTER_WEEKS)
    roster_weeks: list[int] = []
    for week in range(1, last_week + 1):
        try:
            week_payload = await client.fetch_league(
                season, league_id, ROSTER_VIEWS, credentials=credentials, scoring_period=week
            )
            rosters = [
                transform.roster(week_payload, team_id=team["team_id"], scoring_period=week)
                for team in teams
            ]
        except ESPNError as exc:
            logger.warning("rosters for week %s unavailable: %s", week, exc)
            continue
        _write(
            out_dir / "rosters" / f"week-{week}.json",
            {**stamp, "week": week, "rosters": rosters},
        )
        roster_weeks.append(week)

    # Written last: the frontend reads this to know what actually exists.
    _write(
        out_dir / "index.json",
        {
            **stamp,
            "roster_weeks": roster_weeks,
            "weeks": sorted({game["week"] for game in all_matchups}),
            "has_transactions": bool(transactions),
        },
    )

    return {
        "league": meta["name"],
        "season": season,
        "week": meta["current_week"],
        "teams": len(teams),
        "matchups": len(all_matchups),
        "transactions": len(transactions),
        "roster_weeks": roster_weeks,
        "generated_at": generated_at.isoformat(),
    }


def verify_output_is_clean(out_dir: Path, credentials: ESPNCredentials) -> None:
    """Fail the build if any credential material reached the published tree.

    This is the one check that must never be skipped: the output of this script
    is served to the public internet.
    """
    needles = [credentials.espn_s2, credentials.swid, credentials.swid.strip("{}")]
    for path in sorted(out_dir.rglob("*.json")):
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle and needle in text:
                raise BuildError(f"credential material leaked into {path.name}")


def credentials_from_env() -> ESPNCredentials:
    swid = normalize_swid(os.environ.get("ESPN_SWID", ""))
    # Browsers often show espn_s2 percent-encoded; ESPN wants it decoded.
    espn_s2 = os.environ.get("ESPN_S2", "").strip()
    if "%" in espn_s2 and "%25" not in espn_s2:
        from urllib.parse import unquote

        espn_s2 = unquote(espn_s2)
    if not swid or not espn_s2:
        missing = ", ".join(
            name for name, value in (("ESPN_SWID", swid), ("ESPN_S2", espn_s2)) if not value
        )
        raise BuildError(
            f"{missing} not set. In GitHub: Settings -> Secrets and variables -> "
            "Actions -> Secrets -> New repository secret. Both come from your "
            "logged-in ESPN session (DevTools -> Application -> Cookies)."
        )
    return ESPNCredentials(swid=swid, espn_s2=espn_s2)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    league_id = os.environ.get("LEAGUE_ID", "").strip()
    if not league_id:
        raise BuildError(
            "LEAGUE_ID is not set. In GitHub: Settings -> Secrets and variables "
            "-> Actions -> Variables -> New repository variable, named LEAGUE_ID, "
            "set to the number in your ESPN league URL (...leagueId=123456)."
        )
    season = int(os.environ.get("SEASON") or current_season())
    out_dir = Path(os.environ.get("OUTPUT_DIR", "frontend/data"))

    credentials = credentials_from_env()

    # Start clean so a file that stops being generated cannot linger.
    if out_dir.exists():
        shutil.rmtree(out_dir)

    summary = asyncio.run(
        build(credentials=credentials, league_id=league_id, season=season, out_dir=out_dir)
    )
    verify_output_is_clean(out_dir, credentials)

    logger.info("built %s (%s) week %s", summary["league"], season, summary["week"])
    logger.info(
        "  %s teams, %s matchups, %s transactions, rosters for weeks %s",
        summary["teams"],
        summary["matchups"],
        summary["transactions"],
        summary["roster_weeks"] or "none",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
