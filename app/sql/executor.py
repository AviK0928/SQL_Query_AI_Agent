"""Read-only execution of validated SQL (S2).

The second, independent safety layer. Even if the validator (S1) had a bug,
this layer cannot write: the connection is opened read-only (`mode=ro`) and an
SQLite authorizer denies every operation except reads and function calls,
which also blocks ATTACH and PRAGMA (read-only mode alone allows those).

`execute()` accepts only a ValidatedQuery, so running unvalidated SQL is a
type error rather than a convention. Spring comparison: a service method that
takes a validated DTO instead of a raw String.

Failures are raised as SqlSafetyError with a taxonomy code; nothing here
returns errors inside result dicts.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.sql.errors import SqlErrorCode, SqlSafetyError
from app.sql.validator import ValidatedQuery

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger(__name__)

# How many SQLite VM steps between deadline checks. Small enough to stop a
# runaway query within milliseconds of its deadline, large enough to be cheap.
PROGRESS_STEPS = 1000

# SQLite action codes we permit. Everything else is denied by default.
# Looked up defensively: constant names vary slightly across Python versions.
_ALLOWED_ACTIONS: frozenset[int] = frozenset(
    code
    for code in (
        getattr(sqlite3, name, None)
        for name in ("SQLITE_SELECT", "SQLITE_READ", "SQLITE_FUNCTION", "SQLITE_RECURSIVE")
    )
    if code is not None
)


def _authorizer(
    action: int,
    arg1: str | None,
    arg2: str | None,
    db_name: str | None,
    trigger: str | None,
) -> int:
    """Called by SQLite for every operation while a statement is prepared."""
    if action in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


@dataclass(frozen=True)
class QueryResult:
    """Rows from one query.

    truncated:     more rows existed than the row cap; only the first
                   `max_rows` are returned.
    limit_reached: the query's own LIMIT (below the cap) was hit exactly, so
                   more matching rows may exist.
    """

    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    limit_reached: bool

    @property
    def row_count(self) -> int:
        return len(self.rows)


class ReadOnlyExecutor:
    """Runs validated queries against one SQLite file, read-only."""

    def __init__(self, path: Path, max_rows: int, timeout_s: float) -> None:
        if max_rows < 1:
            raise ValueError("max_rows must be at least 1")
        if timeout_s <= 0:
            raise ValueError("timeout_s must be positive")
        self.path = Path(path)
        self.max_rows = max_rows
        self.timeout_s = timeout_s

    @classmethod
    def from_settings(cls, settings: Settings) -> ReadOnlyExecutor:
        return cls(settings.db_path, settings.max_rows, settings.query_timeout_s)

    def _connect(self) -> sqlite3.Connection:
        if not self.path.is_file():
            raise FileNotFoundError("Database file not found. Run: python database/build_db.py")
        return sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)

    def allowed_tables(self) -> frozenset[str]:
        """The validator's table allowlist, read from the real schema."""
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        finally:
            con.close()
        return frozenset(str(row[0]).lower() for row in rows)

    def execute(self, query: ValidatedQuery) -> QueryResult:
        """Run one validated query. Raises SqlSafetyError on any failure."""
        con = self._connect()
        try:
            con.set_authorizer(_authorizer)
            deadline = time.monotonic() + self.timeout_s
            con.set_progress_handler(
                lambda: 1 if time.monotonic() > deadline else 0, PROGRESS_STEPS
            )
            cursor = con.execute(query.sql)
            columns = [d[0] for d in cursor.description] if cursor.description else []
            # One row beyond the cap is how truncation is detected honestly.
            fetched = cursor.fetchmany(self.max_rows + 1)
        except sqlite3.DatabaseError as exc:
            raise self._classify(exc) from None
        finally:
            con.close()

        rows = [list(row) for row in fetched[: self.max_rows]]
        return QueryResult(
            columns=columns,
            rows=rows,
            truncated=len(fetched) > self.max_rows,
            limit_reached=query.query_limit is not None and len(rows) == query.query_limit,
        )

    def _classify(self, exc: sqlite3.DatabaseError) -> SqlSafetyError:
        message = str(exc)
        lowered = message.lower()
        if "interrupted" in lowered:
            return SqlSafetyError(
                SqlErrorCode.TIMEOUT, f"The query was stopped after {self.timeout_s:g} seconds."
            )
        if "not authorized" in lowered:
            # Unreachable when the validator works, so it is a security event.
            logger.warning("sqlite authorizer denied a validated query: %s", message)
            return SqlSafetyError(
                SqlErrorCode.DB_DENIED, "The database refused the query: only reads are permitted."
            )
        return SqlSafetyError(
            SqlErrorCode.EXECUTION_ERROR, f"The database rejected the query: {message}"
        )
