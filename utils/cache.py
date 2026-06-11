"""Async-safe TTL cache with LRU eviction."""

from __future__ import annotations
import asyncio
from time import monotonic
from typing import Any


class TTLCache:
    def __init__(self, max_entries: int = 500):
        self._store: dict[str, tuple[float, float, Any]] = {}
        self._max = max_entries
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None
            expiry, _, val = entry
            if monotonic() > expiry:
                del self._store[key]
                return None
            return val

    async def set(self, key: str, value: Any, ttl: int = 300) -> None:
        async with self._lock:
            self._store[key] = (monotonic() + ttl, monotonic(), value)
            if len(self._store) > self._max:
                self._evict()

    async def clear(self) -> None:
        async with self._lock:
            self._store.clear()

    def _evict(self) -> None:
        to_remove = max(1, len(self._store) // 5)
        sorted_keys = sorted(self._store, key=lambda k: self._store[k][1])
        for k in sorted_keys[:to_remove]:
            del self._store[k]
