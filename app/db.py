"""Read-only SQLite access layer.

Every connection is opened read-only and guarded by an authorizer that
rejects any operation other than SELECT/READ. LLM-generated SQL is
untrusted; this module is the enforcement point, not the prompt.
"""

import os
import time
import sqlite3

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("DB_PATH", os.path.join(_PROJECT_ROOT, "database", "ecommerce.db"))

MAX_ROWS = 200
TIMEOUT_SECONDS = 5.0

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


def _connect(guarded):
    """Open the database read-only. `guarded=True` installs the authorizer."""
    if not os.path.exists(DB_PATH):
        raise FileNotFoundError("Database file not found. Run: python database/build_db.py")

    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    if guarded:
        con.set_authorizer(_authorizer)
    return con


def run_query(sql):
    """Execute a read-only SQL string.

    Returns a dict: columns, rows, row_count, truncated, error.
    Never raises on bad SQL — errors come back in the 'error' field so the
    agent can feed them into its retry step.
    """
    result = {"columns": [], "rows": [], "row_count": 0, "truncated": False, "error": None}
    con = None
    try:
        con = _connect(guarded=True)

        deadline = time.monotonic() + TIMEOUT_SECONDS
        con.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)

        cur = con.execute(sql)
        result["columns"] = [d[0] for d in cur.description] if cur.description else []

        fetched = cur.fetchmany(MAX_ROWS + 1)
        if len(fetched) > MAX_ROWS:
            fetched = fetched[:MAX_ROWS]
            result["truncated"] = True

        result["rows"] = [list(r) for r in fetched]
        result["row_count"] = len(result["rows"])

    except sqlite3.OperationalError as e:
        message = str(e)
        if "interrupted" in message.lower():
            result["error"] = f"Query timed out after {TIMEOUT_SECONDS} seconds."
        else:
            result["error"] = message
    except sqlite3.DatabaseError as e:
        result["error"] = str(e)
    finally:
        if con is not None:
            con.close()

    return result


def get_schema():
    """Return the schema as plain data, for the /schema endpoint."""
    con = _connect(guarded=False)
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
