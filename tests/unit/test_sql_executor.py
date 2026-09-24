"""Unit tests for app/sql/executor.py (S2, Phase 3), run offline against the
real database/ecommerce.db.

The round-trip test is the important one for sqlglot: every query must return
identical rows whether run exactly as written or through validate-then-execute.
That proves the SQL sqlglot regenerates is faithful for this schema.
"""

import logging
import sqlite3
import time

import pytest

from app.sql.errors import SqlErrorCode, SqlSafetyError
from app.sql.executor import ReadOnlyExecutor
from app.sql.validator import ValidatedQuery, validate_sql

C = SqlErrorCode
TABLES = frozenset({"customers", "orders", "order_items", "products"})


@pytest.fixture
def db_path(test_settings):
    return test_settings.db_path


def run(db_path, sql, max_rows=200, timeout_s=5.0):
    executor = ReadOnlyExecutor(db_path, max_rows=max_rows, timeout_s=timeout_s)
    query = validate_sql(sql, allowed_tables=TABLES, max_rows=max_rows)
    return executor.execute(query)


def direct(db_path, sql):
    """Run SQL exactly as written, read-only, without the validator."""
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return [list(row) for row in con.execute(sql).fetchall()]
    finally:
        con.close()


# --- construction and schema ---------------------------------------------


def test_from_settings_uses_configured_values(test_settings):
    executor = ReadOnlyExecutor.from_settings(test_settings)
    assert executor.path == test_settings.db_path
    assert executor.max_rows == test_settings.max_rows
    assert executor.timeout_s == test_settings.query_timeout_s


@pytest.mark.parametrize(("max_rows", "timeout_s"), [(0, 5.0), (10, 0)])
def test_invalid_limits_are_rejected(db_path, max_rows, timeout_s):
    with pytest.raises(ValueError):
        ReadOnlyExecutor(db_path, max_rows=max_rows, timeout_s=timeout_s)


def test_allowed_tables_come_from_the_real_schema(db_path):
    assert ReadOnlyExecutor(db_path, 200, 5.0).allowed_tables() == TABLES


def test_missing_database_file_is_reported(tmp_path):
    executor = ReadOnlyExecutor(tmp_path / "missing.db", 200, 5.0)
    with pytest.raises(FileNotFoundError):
        executor.allowed_tables()


# --- results --------------------------------------------------------------


def test_returns_columns_and_rows(db_path):
    result = run(db_path, "SELECT name, city FROM customers WHERE city = 'Delhi' ORDER BY id")
    assert result.columns == ["name", "city"]
    assert [row[0] for row in result.rows] == ["Kabir Singh", "Nikhil Verma", "Karan Malhotra"]
    assert result.row_count == 3
    assert not result.truncated
    assert not result.limit_reached


def test_more_rows_than_the_cap_are_truncated_honestly(db_path):
    result = run(db_path, "SELECT id FROM customers ORDER BY id", max_rows=2)
    assert result.row_count == 2
    assert result.truncated


def test_exactly_the_cap_is_not_truncated(db_path):
    result = run(db_path, "SELECT id FROM customers WHERE city = 'Delhi'", max_rows=3)
    assert result.row_count == 3
    assert not result.truncated


@pytest.mark.parametrize(
    ("sql", "reached"),
    [
        ("SELECT id FROM customers ORDER BY id LIMIT 3", True),
        ("SELECT id FROM customers WHERE city = 'Delhi' LIMIT 5", False),
    ],
)
def test_limit_reached_flags_a_query_limit_that_was_hit(db_path, sql, reached):
    result = run(db_path, sql)
    assert result.limit_reached is reached
    assert not result.truncated


# --- failures -------------------------------------------------------------


def test_runaway_query_times_out_promptly(db_path):
    sql = "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c"
    started = time.monotonic()
    with pytest.raises(SqlSafetyError) as info:
        run(db_path, sql, timeout_s=0.2)
    assert info.value.code == C.TIMEOUT
    assert not info.value.repairable
    assert time.monotonic() - started < 5


def test_unknown_column_is_a_repairable_execution_error(db_path):
    with pytest.raises(SqlSafetyError) as info:
        run(db_path, "SELECT no_such_column FROM customers")
    assert info.value.code == C.EXECUTION_ERROR
    assert info.value.repairable
    assert "no_such_column" in info.value.detail


@pytest.mark.parametrize(
    "sql",
    [
        "DELETE FROM customers",
        "ATTACH DATABASE ':memory:' AS leak",
        "PRAGMA writable_schema = ON",
    ],
)
def test_database_layer_blocks_unvalidated_sql_on_its_own(db_path, sql, caplog):
    """Bypasses the validator on purpose: S2 must hold even if S1 had a bug."""
    before = direct(db_path, "SELECT COUNT(*) FROM customers")
    forged = ValidatedQuery(sql=sql, tables=frozenset({"customers"}), query_limit=None)

    with (
        caplog.at_level(logging.WARNING, logger="app.sql.executor"),
        pytest.raises(SqlSafetyError) as info,
    ):
        ReadOnlyExecutor(db_path, 200, 5.0).execute(forged)

    assert info.value.code == C.DB_DENIED
    assert not info.value.repairable
    assert "authorizer denied" in caplog.text
    assert direct(db_path, "SELECT COUNT(*) FROM customers") == before


# --- round trip: regenerated SQL is faithful -------------------------------

ROUND_TRIP = [
    "SELECT id, name, city FROM customers WHERE city = 'Delhi' ORDER BY id",
    "SELECT city, COUNT(*) AS n FROM customers GROUP BY city HAVING COUNT(*) >= 1 "
    "ORDER BY n DESC, city",
    "SELECT strftime('%Y-%m', signup_date) AS month, COUNT(*) AS n FROM customers "
    "GROUP BY month ORDER BY month",
    "SELECT c.name, SUM(oi.quantity * oi.unit_price) AS spend FROM customers c "
    "JOIN orders o ON o.customer_id = c.id JOIN order_items oi ON oi.order_id = o.id "
    "WHERE o.status != 'cancelled' GROUP BY c.id ORDER BY spend DESC, c.id",
    "SELECT id, CASE WHEN status = 'cancelled' THEN 'X' ELSE 'OK' END AS flag "
    "FROM orders ORDER BY id",
    "SELECT id, customer_id, ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY id) AS rn "
    "FROM orders ORDER BY customer_id, id",
    "SELECT name || ' (' || city || ')' AS label FROM customers ORDER BY id",
    "SELECT IFNULL(NULL, name) AS n FROM customers ORDER BY id",
    "SELECT c.name, (SELECT COUNT(*) FROM orders o WHERE o.customer_id = c.id) AS n "
    "FROM customers c ORDER BY c.id",
    "SELECT c.id FROM customers c LEFT JOIN orders o ON o.customer_id = c.id "
    "WHERE o.id IS NULL ORDER BY c.id",
    "WITH t AS (SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY customer_id) "
    "SELECT customer_id, n FROM t WHERE n > 1 ORDER BY customer_id",
    "SELECT name, price FROM products WHERE price > 1000 ORDER BY price DESC, name",
    "SELECT ROUND(AVG(unit_price), 2) FROM order_items",
    "SELECT DISTINCT city FROM customers WHERE city IN ('Delhi', 'Mumbai', 'Pune') ORDER BY city",
    "SELECT city AS v FROM customers UNION SELECT status FROM orders ORDER BY v",
    "SELECT id FROM customers WHERE date(signup_date) >= date('2024-03-01') ORDER BY id",
    "SELECT order_id, quantity, CAST(unit_price AS INTEGER) AS whole FROM order_items "
    "WHERE quantity BETWEEN 1 AND 3 ORDER BY order_id, quantity, unit_price",
    "SELECT id FROM customers ORDER BY id LIMIT 5 OFFSET 2",
    "SELECT name FROM customers c WHERE EXISTS (SELECT 1 FROM orders o "
    "WHERE o.customer_id = c.id AND o.status = 'cancelled') ORDER BY c.id",
]


@pytest.mark.parametrize("sql", ROUND_TRIP)
def test_validated_sql_returns_the_same_rows_as_the_original(db_path, sql):
    expected = direct(db_path, sql)
    result = run(db_path, sql, max_rows=10_000)
    assert result.rows == expected
    assert not result.truncated
