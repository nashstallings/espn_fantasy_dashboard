"""Failure modes of an undocumented upstream API.

ESPN's fantasy endpoints have no SLA and no published contract, so every call
site distinguishes "your cookies stopped working" from "ESPN is having a bad
day" from "ESPN changed its response shape". The frontend renders each of these
differently: only the first should send the user back to the connect form.
"""

from __future__ import annotations


class ESPNError(Exception):
    """Base class for anything that went wrong talking to ESPN."""

    status_code = 502


class ESPNAuthError(ESPNError):
    """Cookies are missing, expired, or not entitled to this league."""

    status_code = 401


class ESPNNotFoundError(ESPNError):
    """No such league/team/season."""

    status_code = 404


class ESPNUnavailableError(ESPNError):
    """Timeout, connection failure, or a 5xx from ESPN."""

    status_code = 503


class ESPNSchemaError(ESPNError):
    """A response arrived but did not look like what we parse."""

    status_code = 502
