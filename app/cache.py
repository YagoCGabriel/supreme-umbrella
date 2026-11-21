"""Small process-local TTL cache used to avoid repeated upstream requests."""

from __future__ import annotations

import threading
import time
from typing import Any, Optional


class TTLCache:
    def __init__(self, ttl_seconds: int = 300, max_items: int = 256) -> None:
        self.ttl_seconds = max(0, int(ttl_seconds))
        self.max_items = max(1, int(max_items))
        self._data: dict[str, tuple[float, Any]] = {}
        self._lock = threading.RLock()

    def get(self, key: str) -> Optional[Any]:
        with self._lock:
            item = self._data.get(key)
            if item is None:
                return None
            expires_at, value = item
            if expires_at <= time.monotonic():
                self._data.pop(key, None)
                return None
            return value

    def set(self, key: str, value: Any) -> None:
        if self.ttl_seconds == 0:
            return
        with self._lock:
            now = time.monotonic()
            expired = [item_key for item_key, (expiry, _) in self._data.items() if expiry <= now]
            for item_key in expired:
                self._data.pop(item_key, None)
            if len(self._data) >= self.max_items and key not in self._data:
                oldest_key = min(self._data, key=lambda item_key: self._data[item_key][0])
                self._data.pop(oldest_key, None)
            self._data[key] = (now + self.ttl_seconds, value)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
