"""Schema introspection for the /schema endpoint, the prompt-sync tests and the
schema hash recorded with every LLM call.

Query execution moved to app/sql/executor.py in Phase 3, together with the
read-only authorizer. This module only reads the schema, over a read-only
connection. Moved here from app/db.py in Phase 5 (app/db.py re-exports it).
"""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.config import Settings


class Database:
    """Read-only schema access to one SQLite file."""

    def __init__(self, path: Path):
        self.path = Path(path)

    @classmethod
    def from_settings(cls, settings: Settings) -> Database:
        return cls(settings.db_path)

    def _connect(self) -> sqlite3.Connection:
        """Open the database read-only. No authorizer: PRAGMA table_info is needed here."""
        if not self.path.is_file():
            raise FileNotFoundError("Database file not found. Run: python database/build_db.py")
        return sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)

    def schema_hash(self) -> str:
        """Short, stable fingerprint of the schema definition (not the data).

        Recorded with every LLM call and eval result (principle 8), so a result
        can be tied to the schema it was produced against. Changes when a table,
        column, index or view definition changes; not when rows change.
        """
        con = self._connect()
        try:
            rows = con.execute(
                "SELECT type, name, sql FROM sqlite_master "
                "WHERE name NOT LIKE 'sqlite_%' ORDER BY type, name"
            ).fetchall()
        finally:
            con.close()
        canonical = "\n".join(f"{kind}|{name}|{sql or ''}" for kind, name, sql in rows)
        return hashlib.sha256(canonical.encode()).hexdigest()[:12]

    def get_schema(self) -> dict[str, Any]:
        """Return the schema as plain data, for the /schema endpoint."""
        con = self._connect()
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
                        # Safe only because `table` comes from sqlite_master, never from a
                        # caller: PRAGMA cannot take bound parameters. Never add a table
                        # name argument to this method.
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
