"""Request/response models for the public API.

The response models exist mostly as documentation of the contract the frontend
codes against; league payloads are returned as plain dicts because their shape
tracks ESPN's and pinning it in Pydantic would turn every upstream tweak into a
500 rather than a rendering oddity.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ConnectRequest(BaseModel):
    """The one-time cookie paste.

    ``league_id`` is optional: supply it when ESPN's league discovery cannot see
    your leagues (it happens), and connect validates against that league instead.
    """

    swid: str = Field(min_length=1, description="SWID cookie, with or without braces")
    espn_s2: str = Field(min_length=1, description="espn_s2 cookie value")
    season: int | None = Field(default=None, ge=2000, le=2100)
    league_id: str = ""


class AddLeagueRequest(BaseModel):
    league_id: str = Field(min_length=1)
    season: int | None = Field(default=None, ge=2000, le=2100)


class LeagueSummary(BaseModel):
    league_id: str
    season: int
    name: str = ""
    team_id: int | None = None
    team_name: str = ""
    is_private: bool = True


class ConnectResponse(BaseModel):
    token: str
    expires_at: str
    swid: str
    leagues: list[LeagueSummary]
    discovery_failed: bool = False
    message: str = ""


class AccountResponse(BaseModel):
    swid: str
    leagues: list[LeagueSummary]
    connected_at: str = ""
    last_validated_at: str = ""


class ErrorResponse(BaseModel):
    error: str
    detail: str = ""
