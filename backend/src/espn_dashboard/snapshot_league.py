"""Write one league's daily snapshot to BigQuery, without a server.

The Cloud Run path walked every connected account and needed the credential
store to do it. Here there is exactly one league and its cookies come from CI
secrets, so this reads the environment, fetches once, and appends — reusing the
same row shaping and the same idempotent delete-then-append as before, so rows
written by either path are interchangeable.
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from .bigquery_sync import ALL_TABLES, BigQueryWriter, build_snapshot_rows, league_key
from .build_site import BuildError, credentials_from_env
from .espn.client import ESPNClient
from .service import OVERVIEW_VIEWS, current_season

logger = logging.getLogger(__name__)


async def snapshot(
    *, league_id: str, season: int, project: str, dataset: str, now: datetime | None = None
) -> dict:
    credentials = credentials_from_env()
    snapshot_ts = now or datetime.now(UTC)

    payload = await ESPNClient().fetch_league(
        season, league_id, OVERVIEW_VIEWS, credentials=credentials
    )
    rows = build_snapshot_rows(
        payload, league_id=league_id, season=season, snapshot_ts=snapshot_ts
    )

    writer = BigQueryWriter(project, dataset)
    key = league_key(league_id, season)
    snapshot_date = snapshot_ts.date().isoformat()
    for table in ALL_TABLES:
        # Delete-then-append keeps a re-run idempotent instead of doubling rows.
        writer.delete_snapshot(table, snapshot_date, [key])
        writer.write(table, rows[table])

    return {
        "league_key": key,
        "snapshot_date": snapshot_date,
        "rows_written": {table: len(rows[table]) for table in ALL_TABLES},
    }


def main() -> int:
    import asyncio

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    league_id = os.environ.get("LEAGUE_ID", "").strip()
    if not league_id:
        raise BuildError("LEAGUE_ID must be set")
    project = os.environ.get("GCP_PROJECT", "").strip()
    if not project:
        raise BuildError("GCP_PROJECT must be set")

    result = asyncio.run(
        snapshot(
            league_id=league_id,
            season=int(os.environ.get("SEASON") or current_season()),
            project=project,
            dataset=os.environ.get("BIGQUERY_DATASET", "espn_fantasy"),
        )
    )
    logger.info("snapshot %s for %s", result["league_key"], result["snapshot_date"])
    for table, count in result["rows_written"].items():
        logger.info("  %s: %s rows", table, count)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
