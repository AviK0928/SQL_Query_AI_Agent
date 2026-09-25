"""Unit tests for app/db.py (schema reading for /schema and the prompt-sync tests)."""

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
