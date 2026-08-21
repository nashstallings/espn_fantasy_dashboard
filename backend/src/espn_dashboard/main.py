"""HTTP surface for the dashboard.

Deployed as a single Cloud Run service. The frontend on GitHub Pages is the only
intended caller, so CORS is pinned to the configured origins.

The security rule this file exists to enforce: ``espn_s2`` enters through
``POST /api/connect`` and never leaves. No route echoes it, no response embeds
it, and nothing logs it.
"""

from __future__ import annotations

import logging

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .auth import AuthError, bearer_from_header, issue_token, read_token
from .config import Settings, get_settings
from .crypto import CryptoError
from .espn.errors import ESPNError
from .schemas import (
    AccountResponse,
    AddLeagueRequest,
    ConnectRequest,
    ConnectResponse,
    LeagueSummary,
)
from .service import (
    DashboardService,
    LeagueAccessError,
    NotConnectedError,
    current_season,
)
from .store import build_store

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="ESPN Fantasy Dashboard API",
    version="0.1.0",
    description="Authenticated proxy over ESPN's unofficial fantasy football API.",
)

_settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=_settings.origins,
    allow_credentials=False,  # we use bearer tokens, not cookies, on this API
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type"],
)

_service: DashboardService | None = None


def get_service() -> DashboardService:
    """Built once, lazily, so tests can install their own before first use."""
    global _service
    if _service is None:
        settings = get_settings()
        _service = DashboardService(
            settings,
            build_store(
                settings.credential_store,
                project=settings.gcp_project,
                collection=settings.firestore_collection,
            ),
        )
    return _service


def set_service(service: DashboardService | None) -> None:
    """Test seam."""
    global _service
    _service = service


def current_swid(
    authorization: str | None = Header(default=None),
    settings: Settings = Depends(get_settings),
) -> str:
    return read_token(bearer_from_header(authorization), settings.jwt_secret)


# --- error handling ----------------------------------------------------------


def _error(status: int, error: str, detail: str = "") -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": error, "detail": detail})


@app.exception_handler(AuthError)
async def _auth_error(_: Request, exc: AuthError) -> JSONResponse:
    return _error(401, "unauthorized", str(exc))


@app.exception_handler(NotConnectedError)
async def _not_connected(_: Request, exc: NotConnectedError) -> JSONResponse:
    return _error(401, "not_connected", str(exc))


@app.exception_handler(LeagueAccessError)
async def _league_access(_: Request, exc: LeagueAccessError) -> JSONResponse:
    return _error(403, "league_not_linked", str(exc))


@app.exception_handler(ESPNError)
async def _espn_error(_: Request, exc: ESPNError) -> JSONResponse:
    # Auth failures upstream mean the stored cookies died; the frontend uses
    # this code to send the user back to the connect form.
    return _error(exc.status_code, type(exc).__name__, str(exc))


@app.exception_handler(CryptoError)
async def _crypto_error(_: Request, exc: CryptoError) -> JSONResponse:
    logger.error("credential decryption failed: %s", exc)
    return _error(500, "credential_unreadable", "stored credentials could not be read")


@app.exception_handler(ValueError)
async def _value_error(_: Request, exc: ValueError) -> JSONResponse:
    return _error(400, "bad_request", str(exc))


# --- meta --------------------------------------------------------------------


@app.get("/healthz")
@app.get("/api/healthz")
async def healthz() -> dict[str, str]:
    """Liveness check, served on two paths.

    ``/healthz`` is the conventional one, but on Cloud Run it was answered by
    Google's frontend with a 404 that never reached this app, while sibling
    routes under ``/api/`` served normally. Rather than depend on a path
    something upstream may intercept, the deploy workflow polls ``/api/healthz``;
    the bare path stays for anything already pointed at it.
    """
    return {"status": "ok"}


@app.get("/api/season")
async def season() -> dict[str, int]:
    """What the backend considers the current fantasy season."""
    return {"season": current_season()}


# --- connect / account -------------------------------------------------------


@app.post("/api/connect", response_model=ConnectResponse)
async def connect(
    body: ConnectRequest,
    settings: Settings = Depends(get_settings),
    service: DashboardService = Depends(get_service),
) -> ConnectResponse:
    result = await service.connect(
        swid=body.swid,
        espn_s2=body.espn_s2,
        season=body.season,
        league_id=body.league_id,
    )
    token, expires_at = issue_token(
        result.swid, settings.jwt_secret, ttl_hours=settings.jwt_ttl_hours
    )
    message = ""
    if result.discovery_failed and not result.leagues:
        message = (
            "Your cookies work, but ESPN did not return a league list. "
            "Add a league by its id from the league URL."
        )
    elif result.discovery_failed:
        message = "Some leagues may be missing from automatic discovery; add any others by id."
    return ConnectResponse(
        token=token,
        expires_at=expires_at.isoformat(),
        swid=result.swid,
        leagues=[LeagueSummary(**ref.__dict__) for ref in result.leagues],
        discovery_failed=result.discovery_failed,
        message=message,
    )


@app.get("/api/me", response_model=AccountResponse)
async def me(
    swid: str = Depends(current_swid),
    service: DashboardService = Depends(get_service),
) -> AccountResponse:
    record = service.account(swid)
    return AccountResponse(
        swid=record.swid,
        leagues=[LeagueSummary(**ref.__dict__) for ref in record.leagues],
        connected_at=record.created_at,
        last_validated_at=record.last_validated_at,
    )


@app.post("/api/me/leagues", response_model=LeagueSummary)
async def add_league(
    body: AddLeagueRequest,
    swid: str = Depends(current_swid),
    service: DashboardService = Depends(get_service),
) -> LeagueSummary:
    league = await service.add_league(swid, body.league_id, body.season)
    return LeagueSummary(**league.__dict__)


@app.delete("/api/me/leagues/{season_id}/{league_id}")
async def remove_league(
    season_id: int,
    league_id: str,
    swid: str = Depends(current_swid),
    service: DashboardService = Depends(get_service),
) -> dict[str, bool]:
    removed = service.remove_league(swid, league_id, season_id)
    if not removed:
        raise HTTPException(status_code=404, detail="league not linked to this account")
    return {"removed": True}


@app.delete("/api/me")
async def disconnect(
    swid: str = Depends(current_swid),
    service: DashboardService = Depends(get_service),
) -> dict[str, bool]:
    """Delete the stored cookies and league list for this account."""
    return {"deleted": service.disconnect(swid)}


# --- league views ------------------------------------------------------------


def _season(value: int | None) -> int:
    return value or current_season()


@app.get("/api/leagues/{league_id}/overview")
async def overview(
    league_id: str,
    season_id: int | None = Query(default=None, alias="season"),
    swid: str = Depends(current_swid),
    service: DashboardService = Depends(get_service),
) -> dict:
    return await service.overview(swid, league_id, _season(season_id))


@app.get("/api/leagues/{league_id}/standings")
async def standings(
    league_id: str,
    season_id: int | None = Query(default=None, alias="season"),
    swid: str = Depends(current_swid),
    service: DashboardService = Depends(get_service),
) -> dict:
    return await service.standings(swid, league_id, _season(season_id))


@app.get("/api/leagues/{league_id}/matchups")
async def matchups(
    league_id: str,
    season_id: int | None = Query(default=None, alias="season"),
    week: int | None = Query(default=None, ge=1, le=25),
    swid: str = Depends(current_swid),
    service: DashboardService = Depends(get_service),
) -> dict:
    return await service.matchups(swid, league_id, _season(season_id), week)


@app.get("/api/leagues/{league_id}/teams")
async def teams(
    league_id: str,
    season_id: int | None = Query(default=None, alias="season"),
    swid: str = Depends(current_swid),
    service: DashboardService = Depends(get_service),
) -> dict:
    return await service.teams(swid, league_id, _season(season_id))


@app.get("/api/leagues/{league_id}/teams/{team_id}/roster")
async def roster(
    league_id: str,
    team_id: int,
    season_id: int | None = Query(default=None, alias="season"),
    week: int | None = Query(default=None, ge=1, le=25),
    swid: str = Depends(current_swid),
    service: DashboardService = Depends(get_service),
) -> dict:
    return await service.roster(swid, league_id, _season(season_id), team_id, week)


@app.get("/api/leagues/{league_id}/transactions")
async def transactions(
    league_id: str,
    season_id: int | None = Query(default=None, alias="season"),
    limit: int = Query(default=100, ge=1, le=200),
    swid: str = Depends(current_swid),
    service: DashboardService = Depends(get_service),
) -> dict:
    return await service.transactions(swid, league_id, _season(season_id), limit)


@app.get("/api/leagues/{league_id}/power-rankings")
async def power_rankings(
    league_id: str,
    season_id: int | None = Query(default=None, alias="season"),
    swid: str = Depends(current_swid),
    service: DashboardService = Depends(get_service),
) -> dict:
    return await service.power_rankings(swid, league_id, _season(season_id))


# --- scheduled sync ----------------------------------------------------------


@app.post("/internal/sync")
async def run_sync(
    x_sync_token: str | None = Header(default=None),
    season_id: int | None = Query(default=None, alias="season"),
    settings: Settings = Depends(get_settings),
    service: DashboardService = Depends(get_service),
) -> dict:
    """Snapshot every connected league into BigQuery. Called by Cloud Scheduler.

    Guarded by a shared secret rather than a user token: there is no user in this
    request, and it walks every stored account.
    """
    from .bigquery_sync import sync_all_leagues

    if not settings.sync_token or x_sync_token != settings.sync_token:
        raise HTTPException(status_code=403, detail="invalid sync token")
    return await sync_all_leagues(service, settings, season=_season(season_id))
