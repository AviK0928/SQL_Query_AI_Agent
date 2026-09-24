"""Read-only SQLite access layer.

Every connection is opened read-only and guarded by an authorizer that
rejects any operation other than SELECT/READ. LLM-generated SQL is
untrusted; this module is the enforcement point, not the prompt.

Configuration (path, row cap, timeout) comes from Settings through the
Database constructor. There are no module-level settings.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.config import Settings

# SQLite action codes we permit. Everything else is denied by default.
# Looked up defensively: constant names vary slightly across Python versions.
_ALLOWED_ACTIONS = {
    code
    for code in (
        getattr(sqlite3, name, None)
        for name in ("SQLITE_SELECT", "SQLITE_READ", "SQLITE_FUNCTION", "SQLITE_RECURSIVE")
    )
    if code is not None
}


def _authorizer(action, arg1, arg2, db_name, trigger):
    """Called by SQLite for every operation in a prepared statement."""
    if action in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


class Database:
    """Read-only access to one SQLite file, with a row cap and a query timeout."""

    def __init__(self, path: Path, max_rows: int, timeout_s: float):
        self.path = Path(path)
        self.max_rows = max_rows
        self.timeout_s = timeout_s

    @classmethod
    def from_settings(cls, settings: Settings) -> Database:
        return cls(settings.db_path, settings.max_rows, settings.query_timeout_s)

    def _connect(self, guarded: bool) -> sqlite3.Connection:
        """Open the database read-only. `guarded=True` installs the authorizer."""
        if not self.path.is_file():
            raise FileNotFoundError("Database file not found. Run: python database/build_db.py")

        con = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        if guarded:
            con.set_authorizer(_authorizer)
        return con

    def run_query(self, sql: str) -> dict[str, Any]:
        """Execute a read-only SQL string.

        Returns a dict: columns, rows, row_count, truncated, error.
        Never raises on bad SQL — errors come back in the 'error' field so the
        agent can feed them into its retry step.
        """
        result: dict[str, Any] = {
            "columns": [],
            "rows": [],
            "row_count": 0,
            "truncated": False,
            "error": None,
        }
        con = None
        try:
            con = self._connect(guarded=True)

            deadline = time.monotonic() + self.timeout_s
            con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)

            cur = con.execute(sql)
            result["columns"] = [d[0] for d in cur.description] if cur.description else []

            fetched = cur.fetchmany(self.max_rows + 1)
            if len(fetched) > self.max_rows:
                fetched = fetched[: self.max_rows]
                result["truncated"] = True

            result["rows"] = [list(r) for r in fetched]
            result["row_count"] = len(result["rows"])

        except sqlite3.OperationalError as e:
            message = str(e)
            if "interrupted" in message.lower():
                result["error"] = f"Query timed out after {self.timeout_s} seconds."
            else:
                result["error"] = message
        except sqlite3.DatabaseError as e:
            result["error"] = str(e)
        finally:
            if con is not None:
                con.close()

        return result

    def get_schema(self) -> dict[str, Any]:
        """Return the schema as plain data, for the /schema endpoint."""
        con = self._connect(guarded=False)
        try:
            tables = [
                row[0]
                for row in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            return {
                "tables": [
                    {
                        "name": table,
                        "columns": [
                            {"name": col[1], "type": col[2]}
                            for col in con.execute(f"PRAGMA table_info({table})")
                        ],
                    }
                    for table in tables
                ]
            }
        finally:
            con.close()
