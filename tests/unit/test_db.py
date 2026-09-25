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
