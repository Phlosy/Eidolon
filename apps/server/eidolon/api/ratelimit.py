"""Login rate limiting (R6-d): a sliding window per key, in this process's memory.

Keys are the account (email) and the client address. Five failures in fifteen
minutes lock the key until the window slides past; a success clears it. In-memory
on purpose: it protects the password check, it is not an audit record, and losing
it on restart costs nothing (ADR-22's test for API-layer state that is not a fact).
"""

import threading
import time
from collections import deque
from collections.abc import Callable


class LoginLimiter:
    def __init__(
        self,
        *,
        limit: int = 5,
        window_s: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.limit, self.window, self.clock = limit, window_s, clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] >= self.window:
            q.popleft()
        return q

    def blocked(self, *keys: str) -> float | None:
        """Seconds until the earliest of ``keys`` unblocks, or None when none is blocked."""
        now = self.clock()
        with self._lock:
            waits = []
            for key in keys:
                q = self._prune(key, now)
                if len(q) >= self.limit:
                    waits.append(self.window - (now - q[0]))
            return max(waits) if waits else None

    def failed(self, *keys: str) -> None:
        now = self.clock()
        with self._lock:
            for key in keys:
                self._prune(key, now).append(now)

    def succeeded(self, *keys: str) -> None:
        with self._lock:
            for key in keys:
                self._hits.pop(key, None)
