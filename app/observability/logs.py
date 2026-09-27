"""Structured JSON logs: one object per line on stdout (Phase 9, D55).

stdout is what Render's log viewer shows. Every line has the same timestamp
format and field style as the LLM call log (app/llm/calllog.py), so all lines
for one question can be found by their `request_id`.

Fields: `ts`, `level`, `logger`, `event` (the log message), `request_id`, then
any `extra=` fields; an extra field cannot overwrite one of these. For an
exception only its type and stack frames are written, never its message:
provider and database messages can carry user data or SQL (H5). Configured
secret values are replaced with *** anywhere in a line.

Spring comparison: `request_id_var` plays the role of SLF4J's MDC.
"""

from __future__ import annotations

import json
import logging
import sys
import traceback
from collections.abc import Iterable
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

# Set by the HTTP middleware for the duration of one request.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

REDACTED = "***"
# Third-party loggers held above INFO (D55, H5):
# - sqlglot logs a warning containing the SQL text when it falls back to
#   parsing a statement as a raw command, so model output could reach the logs (H2).
# - HTTP clients log every request's full URL, query string included, at INFO:
#   httpx (the Groq SDK), httpcore (its transport), httpx2 (the test client).
QUIET_LOGGERS = {
    "sqlglot": logging.ERROR,
    "httpx": logging.WARNING,
    "httpcore": logging.WARNING,
    "httpx2": logging.WARNING,
}
# Attributes every LogRecord has; anything else on a record came from `extra=`.
_STANDARD_ATTRS = frozenset(vars(logging.makeLogRecord({}))) | {"message", "asctime", "taskName"}


class JsonFormatter(logging.Formatter):
    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self.secrets = tuple(s for s in secrets if s)

    def format(self, record: logging.LogRecord) -> str:
        extra = {k: v for k, v in vars(record).items() if k not in _STANDARD_ATTRS}
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            "request_id": extra.pop("request_id", None) or request_id_var.get(),
        }
        for key, value in extra.items():
            entry.setdefault(key, value)
        exc = record.exc_info[1] if record.exc_info else None
        if isinstance(exc, BaseException):
            entry["error.type"] = type(exc).__name__
            entry["error.stack"] = traceback.format_tb(exc.__traceback__)
        line = json.dumps(entry, ensure_ascii=False, default=str)
        for secret in self.secrets:
            line = line.replace(secret, REDACTED)
        return line


class StdoutHandler(logging.Handler):
    """Writes to whatever sys.stdout is when a record is emitted.

    logging.StreamHandler binds the stream once; a stream replaced later (test
    capture, a notebook) would then receive nothing or raise on a closed file.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            line = self.format(record)
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        except Exception:
            self.handleError(record)  # a broken log line must never fail a request


def configure_logging(*, secrets: Iterable[str] = (), level: int = logging.INFO) -> StdoutHandler:
    """Route every log record through one JSON handler on the root logger.

    Idempotent: a handler installed by an earlier call is replaced, so building
    the app twice (tests) never duplicates lines.
    """
    root = logging.getLogger()
    for old in [h for h in root.handlers if isinstance(h, StdoutHandler)]:
        root.removeHandler(old)
    handler = StdoutHandler()
    handler.setFormatter(JsonFormatter(secrets))
    root.addHandler(handler)
    root.setLevel(level)
    for name, quiet_level in QUIET_LOGGERS.items():
        logging.getLogger(name).setLevel(quiet_level)
    return handler
