"""Structured process logging for v2: one JSON object per line on stderr.

Every line carries ``ts`` / ``level`` / ``logger`` / ``msg`` and whatever the caller
passed in ``extra=``. Strings are redacted before they are written, so a provider
key or the database password can never reach a log file even by accident.

The redaction rules are a trimmed copy of the legacy ``app/core/redaction.py``
(v2 must not import ``app``): a registry of live secret values plus a few
secret-shaped patterns.
"""

import json
import logging
import re
import sys
import threading
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import make_url

_MASK = "••••••••"

_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),  # OpenAI / Anthropic / OpenRouter style keys
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),  # Google API keys
    re.compile(r"ghp_[0-9A-Za-z]{20,}"),  # GitHub PATs
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{12,}"),
    re.compile(r"(?i)([A-Z][A-Z0-9_]*(?:API_KEY|APIKEY|TOKEN|SECRET|PASSWORD))=([^\s'\"]{4,})"),
)
_ENV_PATTERN = _PATTERNS[-1]


def mask_secret(value: str) -> str:
    if len(value) <= 8:
        return _MASK
    prefix = value[:3] if value.startswith("sk-") or value[:3].isalnum() else ""
    return f"{prefix}{_MASK}{value[-4:]}"


class _Redactor:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._values: dict[str, str] = {}

    def register(self, value: str | None) -> None:
        if not value or len(value) < 4:
            return
        with self._lock:
            self._values[value] = mask_secret(value)

    def redact(self, text: str) -> str:
        if not text:
            return text
        with self._lock:
            items = list(self._values.items())
        for value, mask in items:
            if value in text:
                text = text.replace(value, mask)
        for pattern in _PATTERNS:
            text = pattern.sub(_replacement, text)
        return text


def _replacement(match: re.Match[str]) -> str:
    if match.re is _ENV_PATTERN:
        return f"{match.group(1)}={_MASK}"
    if match.group(0).startswith("Bearer"):
        return f"Bearer {_MASK}"
    return mask_secret(match.group(0))


_redactor = _Redactor()


def register_secret(value: str | None) -> None:
    """Make ``value`` unprintable in every log line of this process."""
    _redactor.register(value)


def redact(text: str) -> str:
    return _redactor.redact(text)


def redact_data(obj: Any) -> Any:
    """Recursively redact every string in a JSON-like structure."""
    if isinstance(obj, str):
        return redact(obj)
    if isinstance(obj, dict):
        return {key: redact_data(value) for key, value in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_data(item) for item in obj]
    return obj


# Attributes every LogRecord has; anything else on a record came from ``extra=``.
_STANDARD_ATTRS = frozenset(
    set(vars(logging.LogRecord("x", logging.INFO, "x", 0, "x", None, None)))
    | {"message", "asctime", "taskName"}
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                line[key] = _jsonable(value)
        if record.exc_info and record.exc_info[1] is not None:
            line["exc"] = self.formatException(record.exc_info)
        return json.dumps(redact_data(line), ensure_ascii=False, default=str)


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in value]
    return str(value)


_configured = False


def configure(level: str | None = None, *, stream=None, database_url: str | None = None) -> None:
    """Install the JSON handler on the root logger (idempotent) and register the
    database password as a secret."""
    global _configured
    from eidolon.config import settings

    if database_url is None:
        database_url = settings.database_url
    register_secret(make_url(database_url).password)

    root = logging.getLogger()
    root.setLevel((level or settings.log_level).upper())
    if _configured:
        return
    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(JsonFormatter())
    root.addHandler(handler)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
