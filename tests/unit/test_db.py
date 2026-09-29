"""Unit tests for app/db.py (schema reading for /schema and the prompt-sync tests)."""

import sqlite3

import pytest

from app.db import Database


def test_missing_database_file_is_reported(tmp_path):
    with pytest.raises(FileNotFoundError, match="build_db.py"):
        Database(tmp_path / "missing.db").get_schema()


def test_schema_lists_every_table_with_typed_columns(test_settings):
    schema = Database.from_settings(test_settings).get_schema()
    names = [t["name"] for t in schema["tables"]]
    assert names == sorted(names), "tables are returned in name order"
    assert all(c["type"] for t in schema["tables"] for c in t["columns"])


def test_schema_hash_is_short_and_stable(test_settings):
    db = Database.from_settings(test_settings)
    first = db.schema_hash()
    assert len(first) == 12
    assert all(c in "0123456789abcdef" for c in first)
    assert db.schema_hash() == first


def test_schema_hash_ignores_data_but_tracks_schema(tmp_path):
    path = tmp_path / "copy.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (x INTEGER)")
    con.commit()
    before = Database(path).schema_hash()

    con.execute("INSERT INTO t VALUES (1)")
    con.commit()
    assert Database(path).schema_hash() == before, "data changes must not change the hash"

    con.execute("ALTER TABLE t ADD COLUMN y TEXT")
    con.commit()
    con.close()
    assert Database(path).schema_hash() != before, "schema changes must change the hash"


def test_the_old_import_path_still_works():
    from app.db import Database as Old
    from app.sql.schema import Database as New

    assert Old is New


# --- pinned by mutation testing (T24) --------------------------------------------------

EXPECTED_COLUMNS = {
    "customers": [
        ("id", "INTEGER"),
        ("name", "TEXT"),
        ("email", "TEXT"),
        ("city", "TEXT"),
        ("signup_date", "TEXT"),
    ],
    "order_items": [
        ("id", "INTEGER"),
        ("order_id", "INTEGER"),
        ("product_id", "INTEGER"),
        ("quantity", "INTEGER"),
        ("unit_price", "REAL"),
    ],
    "orders": [
        ("id", "INTEGER"),
        ("customer_id", "INTEGER"),
        ("order_date", "TEXT"),
        ("status", "TEXT"),
    ],
    "products": [("id", "INTEGER"), ("name", "TEXT"), ("category", "TEXT"), ("price", "REAL")],
}


def test_schema_describes_each_column_by_its_name_and_type(test_settings):
    """The /schema payload the frontend reads: every column as {"name", "type"}."""
    schema = Database.from_settings(test_settings).get_schema()
    got = {t["name"]: [(c["name"], c["type"]) for c in t["columns"]] for t in schema["tables"]}
    assert got == EXPECTED_COLUMNS


def test_schema_hash_of_the_committed_database_is_pinned(test_settings):
    """Every call and eval since Phase 4 recorded 207e7a26b02f (T9). A new value means the
    schema or the hashing changed: a model re-selection trigger, never a silent update."""
    assert Database.from_settings(test_settings).schema_hash() == "207e7a26b02f"


def test_the_schema_connection_cannot_write(test_settings, tmp_path):
    """No authorizer here (PRAGMA table_info needs none), so read-only mode is this
    connection's only write protection. Runs on a copy: a failure never touches the real file."""
    copy = tmp_path / "copy.db"
    copy.write_bytes(test_settings.db_path.read_bytes())
    con = Database(copy)._connect()
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            con.execute("CREATE TABLE intruder (x INTEGER)")
    finally:
        con.close()
