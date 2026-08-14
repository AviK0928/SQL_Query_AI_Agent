"""Tests for the SQL validator (S1)."""

import pytest

from app.validator import validate_sql


# --- queries that must be accepted -------------------------------------

@pytest.mark.parametrize("sql", [
    "SELECT * FROM customers",
    "select name, city from customers where city = 'Pune'",
    "SELECT * FROM customers;",
    "   SELECT * FROM customers   ",
    "SELECT * FROM customers; ",
    """SELECT c.name, SUM(oi.quantity * oi.unit_price) AS revenue
       FROM customers c
       JOIN orders o ON o.customer_id = c.id
       JOIN order_items oi ON oi.order_id = o.id
       GROUP BY c.id
       ORDER BY revenue DESC
       LIMIT 5""",
    "WITH totals AS (SELECT customer_id, COUNT(*) n FROM orders GROUP BY customer_id) SELECT * FROM totals",
    "SELECT * FROM products WHERE name LIKE '%CREATED%'",
    "SELECT * FROM orders WHERE status = 'updated'",
])
def test_valid_queries_are_accepted(sql):
    is_valid, cleaned, error = validate_sql(sql)
    assert is_valid is True, f"rejected with: {error}"
    assert error is None
    assert not cleaned.endswith(";")


# --- write operations that must be blocked -----------------------------

@pytest.mark.parametrize("sql", [
    "DROP TABLE customers",
    "DELETE FROM customers",
    "UPDATE products SET price = 0",
    "INSERT INTO customers (id) VALUES (99)",
    "ALTER TABLE customers ADD COLUMN hacked TEXT",
    "TRUNCATE TABLE orders",
    "CREATE TABLE evil (id INTEGER)",
    "ATTACH DATABASE '/etc/passwd' AS leak",
    "PRAGMA database_list",
    "VACUUM",
    "EXPLAIN SELECT * FROM customers",
    "ANALYZE",
])
def test_write_operations_are_blocked(sql):
    is_valid, cleaned, error = validate_sql(sql)
    assert is_valid is False
    assert cleaned is None
    assert error


# --- statement stacking -------------------------------------------------

@pytest.mark.parametrize("sql", [
    "SELECT * FROM customers; DROP TABLE customers",
    "SELECT 1; SELECT 2",
    "SELECT * FROM customers;;",
])
def test_multiple_statements_are_blocked(sql):
    is_valid, _, error = validate_sql(sql)
    assert is_valid is False
    assert "Multiple SQL statements" in error


def test_trailing_semicolon_is_stripped_not_rejected():
    is_valid, cleaned, _ = validate_sql("SELECT * FROM customers;")
    assert is_valid is True
    assert cleaned == "SELECT * FROM customers"


# --- keywords hidden behind comments ------------------------------------

def test_keyword_after_line_comment_is_caught():
    is_valid, _, error = validate_sql("SELECT 1 --\nDROP TABLE customers")
    assert is_valid is False


def test_keyword_inside_block_comment_is_ignored_safely():
    # The comment is stripped, so what remains is a plain SELECT.
    is_valid, cleaned, _ = validate_sql("SELECT /* DROP TABLE x */ name FROM customers")
    assert is_valid is True
    assert "DROP" not in cleaned.upper()


def test_comment_only_input_is_rejected():
    is_valid, _, error = validate_sql("-- just a comment")
    assert is_valid is False


# --- empty and malformed input ------------------------------------------

@pytest.mark.parametrize("sql", ["", "   ", None, ";"])
def test_empty_input_is_rejected(sql):
    is_valid, cleaned, error = validate_sql(sql)
    assert is_valid is False
    assert error
