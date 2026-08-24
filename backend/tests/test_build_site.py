"""The static site build.

Its output is served to the public internet, so the tests care most about two
things: that the tree is complete enough for the frontend to render, and that
no credential material is in it.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from espn_dashboard.build_site import (
    BuildError,
    build,
    credentials_from_env,
    verify_output_is_clean,
)
from espn_dashboard.espn.client import ESPNCredentials
from espn_dashboard.espn.errors import ESPNUnavailableError

from .conftest import VALID_COOKIE
from .fixtures import SWID

NOW = datetime(2026, 11, 3, 14, 30, tzinfo=UTC)
CREDENTIALS = ESPNCredentials(swid=SWID, espn_s2=VALID_COOKIE)


@pytest.fixture
async def built(tmp_path, espn) -> Path:
    await build(
        credentials=CREDENTIALS,
        league_id="123456",
        season=2026,
        out_dir=tmp_path,
        client=espn,
        now=NOW,
    )
    return tmp_path


def read(root: Path, name: str) -> dict:
    return json.loads((root / f"{name}.json").read_text())


# --- completeness -------------------------------------------------------------


def test_every_view_the_frontend_asks_for_exists(built):
    for name in (
        "index",
        "meta",
        "overview",
        "standings",
        "matchups",
        "teams",
        "transactions",
        "power-rankings",
    ):
        assert (built / f"{name}.json").exists(), f"{name}.json missing"


def test_payloads_match_the_shapes_the_views_consume(built):
    assert len(read(built, "standings")["standings"]) == 4
    assert len(read(built, "teams")["teams"]) == 4
    assert len(read(built, "power-rankings")["power_rankings"]) == 4
    assert read(built, "power-rankings")["weights"]
    assert read(built, "transactions")["transactions"]

    overview = read(built, "overview")
    assert overview["standings"] and overview["power_rankings"]
    assert all(game["week"] == 4 for game in overview["current_matchups"])


def test_matchups_ship_every_week_in_one_file(built):
    weeks = {game["week"] for game in read(built, "matchups")["matchups"]}
    assert weeks == {1, 2, 3, 4}


def test_rosters_are_written_per_week_with_every_team(built):
    manifest = read(built, "index")
    assert manifest["roster_weeks"] == [1, 2, 3, 4]
    for week in manifest["roster_weeks"]:
        payload = json.loads((built / "rosters" / f"week-{week}.json").read_text())
        assert payload["week"] == week
        assert len(payload["rosters"]) == 4
        assert payload["rosters"][0]["starters"]


def test_manifest_describes_what_was_actually_built(built):
    manifest = read(built, "index")
    assert manifest["weeks"] == [1, 2, 3, 4]
    assert manifest["has_transactions"] is True


# --- freshness ----------------------------------------------------------------


def test_every_file_is_stamped_so_the_page_can_show_its_age(built):
    for path in built.rglob("*.json"):
        payload = json.loads(path.read_text())
        assert payload["generated_at"] == NOW.isoformat()


# --- credential hygiene -------------------------------------------------------


def test_no_credential_material_reaches_the_published_tree(built):
    blob = "".join(path.read_text() for path in built.rglob("*.json"))
    assert VALID_COOKIE not in blob
    assert SWID not in blob
    assert SWID.strip("{}") not in blob
    verify_output_is_clean(built, CREDENTIALS)


def test_the_leak_check_actually_catches_a_leak(tmp_path):
    (tmp_path / "oops.json").write_text(json.dumps({"cookie": VALID_COOKIE}))
    with pytest.raises(BuildError, match="leaked"):
        verify_output_is_clean(tmp_path, CREDENTIALS)


# --- resilience ---------------------------------------------------------------


async def test_a_missing_transaction_log_does_not_lose_the_standings(tmp_path, espn):
    """ESPN's activity feed is the flakiest view; it must not take the site down."""
    real_fetch = espn.fetch_league

    async def fail_transactions(season, league_id, views, **kwargs):
        if "mTransactions2" in views:
            raise ESPNUnavailableError("ESPN said no")
        return await real_fetch(season, league_id, views, **kwargs)

    espn.fetch_league = fail_transactions
    await build(
        credentials=CREDENTIALS,
        league_id="123456",
        season=2026,
        out_dir=tmp_path,
        client=espn,
        now=NOW,
    )
    assert len(read(tmp_path, "standings")["standings"]) == 4
    assert read(tmp_path, "transactions")["transactions"] == []
    assert read(tmp_path, "index")["has_transactions"] is False


# --- credentials from the environment -----------------------------------------


def test_percent_encoded_cookies_are_decoded(monkeypatch):
    """Browsers show espn_s2 percent-encoded; ESPN wants it decoded."""
    monkeypatch.setenv("ESPN_SWID", SWID)
    monkeypatch.setenv("ESPN_S2", "AEB%3Dvalue%2Fhere")
    assert credentials_from_env().espn_s2 == "AEB=value/here"


def test_already_decoded_cookies_are_left_alone(monkeypatch):
    monkeypatch.setenv("ESPN_SWID", SWID)
    monkeypatch.setenv("ESPN_S2", "AEB=value/here")
    assert credentials_from_env().espn_s2 == "AEB=value/here"


def test_swid_braces_are_added(monkeypatch):
    monkeypatch.setenv("ESPN_SWID", SWID.strip("{}").lower())
    monkeypatch.setenv("ESPN_S2", "cookie")
    assert credentials_from_env().swid == SWID


@pytest.mark.parametrize(("swid", "s2"), [("", "cookie"), (SWID, ""), ("", "")])
def test_missing_credentials_fail_loudly(monkeypatch, swid, s2):
    monkeypatch.setenv("ESPN_SWID", swid)
    monkeypatch.setenv("ESPN_S2", s2)
    with pytest.raises(BuildError, match="must both be set"):
        credentials_from_env()
