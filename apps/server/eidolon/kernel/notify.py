"""Wake-up channel for workers.

``FactStore.append`` issues ``NOTIFY <channel>`` inside the writing transaction;
PostgreSQL delivers it only on commit. A worker blocks in ``wait`` instead of
polling, then drains its consumers. Missing a notification is harmless — the
cursor model means the next wake-up (or a periodic timeout) catches up.
"""

import select as _select
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
from sqlalchemy import make_url

from eidolon.config import settings


def _dsn(url: str) -> str:
    u = make_url(url)
    parts = [f"dbname={u.database}"]
    if u.username:
        parts.append(f"user={u.username}")
    if u.password:
        parts.append(f"password={u.password}")
    if u.host:
        parts.append(f"host={u.host}")
    if u.port:
        parts.append(f"port={u.port}")
    for key, value in u.query.items():
        if key in {"host", "port"} and isinstance(value, str):
            parts.append(f"{key}={value}")
    return " ".join(parts)


class Listener:
    """A dedicated autocommit connection subscribed to the fact channel."""

    def __init__(self, url: str | None = None, channel: str | None = None):
        self.channel = channel or settings.fact_notify_channel
        self._conn = psycopg.connect(_dsn(url or settings.database_url), autocommit=True)
        self._conn.execute(f"LISTEN {self.channel}")

    def fileno(self) -> int:
        """The socket to ``select`` on; lets a caller wait on this and its own fds at once."""
        return self._conn.fileno()

    def wait(self, timeout: float) -> int:
        """Block up to ``timeout`` seconds; return how many notifications arrived."""
        ready, _, _ = _select.select([self.fileno()], [], [], timeout)
        if not ready:
            return 0
        return self.pending()

    def pending(self) -> int:
        """Consume every notification already received, without blocking."""
        count = 0
        for _ in self._conn.notifies(timeout=0):
            count += 1
        return count

    def close(self) -> None:
        self._conn.close()


@contextmanager
def listening(url: str | None = None, channel: str | None = None) -> Iterator[Listener]:
    listener = Listener(url, channel)
    try:
        yield listener
    finally:
        listener.close()
