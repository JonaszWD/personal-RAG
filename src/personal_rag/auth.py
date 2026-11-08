"""Rate limiting and session storage for remote (browser) access. In-memory by design:
restarting the backend logs every phone out, which is the safe failure mode."""

from __future__ import annotations

import secrets
import time
from collections import deque
from collections.abc import Callable
from threading import Lock


class SlidingWindow:
    """At most `limit` events per `window_s` seconds."""

    def __init__(
        self, limit: int, window_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._limit = limit
        self._window = window_s
        self._clock = clock
        self._events: deque[float] = deque()
        self._lock = Lock()

    def _purge(self, now: float) -> None:
        while self._events and now - self._events[0] >= self._window:
            self._events.popleft()

    def allow(self) -> bool:
        """Record an event if under the limit. False means rate-limited."""
        with self._lock:
            now = self._clock()
            self._purge(now)
            if len(self._events) >= self._limit:
                return False
            self._events.append(now)
            return True

    def hit(self) -> None:
        with self._lock:
            now = self._clock()
            self._purge(now)
            self._events.append(now)

    def retry_after(self) -> float:
        """Seconds until another event would be allowed (0 if allowed now)."""
        with self._lock:
            now = self._clock()
            self._purge(now)
            if len(self._events) < self._limit:
                return 0.0
            return max(self._events[0] + self._window - now, 0.0)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


class SessionStore:
    def __init__(
        self, ttl_s: float, max_sessions: int = 20, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._ttl = ttl_s
        self._max = max_sessions
        self._clock = clock
        self._sessions: dict[str, float] = {}
        self._lock = Lock()

    def create(self) -> str:
        sid = secrets.token_urlsafe(32)
        with self._lock:
            now = self._clock()
            self._sessions = {k: v for k, v in self._sessions.items() if v > now}
            while len(self._sessions) >= self._max:  # evict oldest
                self._sessions.pop(min(self._sessions, key=lambda k: self._sessions[k]))
            self._sessions[sid] = now + self._ttl
        return sid

    def valid(self, sid: str | None) -> bool:
        if not sid:
            return False
        with self._lock:
            expiry = self._sessions.get(sid)
            if expiry is None:
                return False
            if expiry <= self._clock():
                del self._sessions[sid]
                return False
            return True

    def revoke(self, sid: str | None) -> None:
        if sid:
            with self._lock:
                self._sessions.pop(sid, None)
