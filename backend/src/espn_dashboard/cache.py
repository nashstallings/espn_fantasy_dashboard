"""Small in-process TTL cache for ESPN responses.

The dashboard fetches on demand, so a burst of view switches would otherwise
hammer ESPN with the same league payload. A short TTL collapses that burst while
keeping the data effectively live. Cache keys always include the SWID, so one
user's private-league payload can never be served to another.

In-process means per-container: with several Cloud Run instances each keeps its
own copy, which is fine for a 60s TTL. Move to Memorystore only if the instance
count makes ESPN traffic a problem.
"""

from __future__ import annotations

import threading
import time
from typing import Any


class TTLCache:
    def __init__(self, ttl_seconds: int, *, max_entries: int = 2048) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                return None
            expires_at, value = entry
            if expires_at <= now:
                self._entries.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        if self._ttl <= 0:
            return
        now = time.monotonic()
        with self._lock:
            if len(self._entries) >= self._max_entries:
                self._evict_expired(now)
            if len(self._entries) >= self._max_entries:
                oldest = min(self._entries, key=lambda k: self._entries[k][0])
                self._entries.pop(oldest, None)
            self._entries[key] = (now + self._ttl, value)

    def invalidate_prefix(self, prefix: str) -> None:
        """Drop every entry for one user — used on disconnect."""
        with self._lock:
            for key in [k for k in self._entries if k.startswith(prefix)]:
                self._entries.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def _evict_expired(self, now: float) -> None:
        for key in [k for k, (exp, _) in self._entries.items() if exp <= now]:
            self._entries.pop(key, None)
