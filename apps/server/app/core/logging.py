"""Unified structured logging. See docs/architecture.md §10.

Format: ``%(asctime)s %(levelname)s [%(name)s] employee=.. project=.. task=.. %(message)s``
Missing context fields default to ``-``. Production code must not use ``print()``.
"""

import logging

from app.core.redaction import redact

LOG_FORMAT = (
    "%(asctime)s %(levelname)s [%(name)s] "
    "employee=%(employee_id)s project=%(project_id)s task=%(task_id)s %(message)s"
)

CONTEXT_FIELDS = ("employee_id", "project_id", "task_id")


class ContextFilter(logging.Filter):
    """Inject '-' defaults for missing context fields."""

    def filter(self, record: logging.LogRecord) -> bool:
        for field in CONTEXT_FIELDS:
            if not hasattr(record, field):
                setattr(record, field, "-")
        return True


class RedactionFilter(logging.Filter):
    """Scrub registered secret values / secret-shaped patterns from every record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = ()
        return True


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(LOG_FORMAT))
    handler.addFilter(ContextFilter())
    handler.addFilter(RedactionFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())


def get_logger(name: str, **context: int | str | None) -> logging.LoggerAdapter:
    """Return a LoggerAdapter that injects employee/project/task ids into every record."""
    extra = {field: context.get(field) or "-" for field in CONTEXT_FIELDS}
    return logging.LoggerAdapter(logging.getLogger(name), extra)
