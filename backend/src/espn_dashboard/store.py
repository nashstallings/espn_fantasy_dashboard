"""Storage for connected ESPN accounts.

Privacy shape of a record:

* ``swid`` is stored in the clear. It is ESPN's member id and the only thing we
  can key an account on; on its own it does not authenticate anything.
* ``espn_s2`` is the actual session secret and is stored only as AES-256-GCM
  ciphertext, with the SWID as additional authenticated data so a blob cannot be
  replayed under a different account.
* Nothing here is ever returned to the browser. ``/api/me`` exposes the SWID and
  the league list, never cookie material.

``MemoryCredentialStore`` backs local development and tests; Firestore backs
deployed environments. Firestore rather than BigQuery because this is
low-volume, per-user, mutable, point-read data — BigQuery holds the league
history instead.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

CURRENT_RECORD_VERSION = 1


@dataclass
class LeagueRef:
    """One league a SWID has a team in."""

    league_id: str
    season: int
    name: str = ""
    team_id: int | None = None
    team_name: str = ""
    is_private: bool = True

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LeagueRef:
        return cls(
            league_id=str(raw.get("league_id", "")),
            season=int(raw.get("season", 0)),
            name=str(raw.get("name", "")),
            team_id=raw.get("team_id"),
            team_name=str(raw.get("team_name", "")),
            is_private=bool(raw.get("is_private", True)),
        )


@dataclass
class CredentialRecord:
    swid: str
    espn_s2_encrypted: str
    leagues: list[LeagueRef] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    last_validated_at: str = ""
    version: int = CURRENT_RECORD_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CredentialRecord:
        return cls(
            swid=str(raw.get("swid", "")),
            espn_s2_encrypted=str(raw.get("espn_s2_encrypted", "")),
            leagues=[LeagueRef.from_dict(x) for x in raw.get("leagues") or []],
            created_at=str(raw.get("created_at", "")),
            updated_at=str(raw.get("updated_at", "")),
            last_validated_at=str(raw.get("last_validated_at", "")),
            version=int(raw.get("version", CURRENT_RECORD_VERSION)),
        )


def document_id(swid: str) -> str:
    """Hash the SWID for the document name so member ids are not directory keys."""
    return hashlib.sha256(swid.encode()).hexdigest()


def utcnow() -> str:
    return datetime.now(UTC).isoformat()


class CredentialStore(ABC):
    @abstractmethod
    def get(self, swid: str) -> CredentialRecord | None: ...

    @abstractmethod
    def put(self, record: CredentialRecord) -> None: ...

    @abstractmethod
    def delete(self, swid: str) -> bool: ...

    @abstractmethod
    def list_all(self) -> list[CredentialRecord]:
        """Every connected account. Used only by the scheduled BigQuery sync."""


class MemoryCredentialStore(CredentialStore):
    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}

    def get(self, swid: str) -> CredentialRecord | None:
        raw = self._rows.get(document_id(swid))
        return CredentialRecord.from_dict(raw) if raw else None

    def put(self, record: CredentialRecord) -> None:
        key = document_id(record.swid)
        existing = self._rows.get(key)
        record.created_at = (existing or {}).get("created_at") or record.created_at or utcnow()
        record.updated_at = utcnow()
        self._rows[key] = record.to_dict()

    def delete(self, swid: str) -> bool:
        return self._rows.pop(document_id(swid), None) is not None

    def list_all(self) -> list[CredentialRecord]:
        return [CredentialRecord.from_dict(r) for r in self._rows.values()]


class FirestoreCredentialStore(CredentialStore):
    def __init__(self, project: str, collection: str) -> None:
        from google.cloud import firestore  # imported lazily: local dev has no GCP creds

        self._client = firestore.Client(project=project or None)
        self._collection = collection

    def _doc(self, swid: str):
        return self._client.collection(self._collection).document(document_id(swid))

    def get(self, swid: str) -> CredentialRecord | None:
        snapshot = self._doc(swid).get()
        if not snapshot.exists:
            return None
        return CredentialRecord.from_dict(snapshot.to_dict() or {})

    def put(self, record: CredentialRecord) -> None:
        doc = self._doc(record.swid)
        snapshot = doc.get()
        created = (snapshot.to_dict() or {}).get("created_at") if snapshot.exists else None
        record.created_at = created or record.created_at or utcnow()
        record.updated_at = utcnow()
        doc.set(record.to_dict())

    def delete(self, swid: str) -> bool:
        doc = self._doc(swid)
        if not doc.get().exists:
            return False
        doc.delete()
        return True

    def list_all(self) -> list[CredentialRecord]:
        return [
            CredentialRecord.from_dict(s.to_dict() or {})
            for s in self._client.collection(self._collection).stream()
        ]


def build_store(kind: str, *, project: str = "", collection: str = "") -> CredentialStore:
    if kind == "firestore":
        return FirestoreCredentialStore(project, collection or "espn_credentials")
    if kind == "memory":
        return MemoryCredentialStore()
    raise ValueError(f"unknown CREDENTIAL_STORE {kind!r}; expected 'firestore' or 'memory'")
