"""Unit tests for app/sql/validator.py and app/sql/errors.py (S1, Phase 3).

Every case from tests/test_validator.py is ported here with its original
expectation, now also asserting the specific error code. The old file is
removed when the agent switches to this validator.
"""

import pytest
from sqlglot import exp

from app.db import Database
from app.sql import validator as validator_module
from app.sql.errors import REPAIRABLE_CODES, USER_MESSAGES, SqlErrorCode, SqlSafetyError
from app.sql.validator import WRITE_TYPES, ValidatedQuery, validate_sql

TABLES = frozenset({"customers", "orders", "order_items", "products"})
CAP = 200
C = SqlErrorCode


def check(sql, max_rows=CAP, **kwargs) -> ValidatedQuery:
    return validate_sql(sql, allowed_tables=TABLES, max_rows=max_rows, **kwargs)


def rejected(sql, **kwargs) -> SqlSafetyError:
    with pytest.raises(SqlSafetyError) as info:
        check(sql, **kwargs)
    return info.value


def test_allowlist_matches_the_real_database(test_settings):
    schema = Database.from_settings(test_settings).get_schema()
    assert {t["name"] for t in schema["tables"]} == TABLES


# --- accepted (ported) ----------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
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
        "WITH totals AS (SELECT customer_id, COUNT(*) n FROM orders GROUP BY customer_id) "
        "SELECT * FROM totals",
        "SELECT * FROM products WHERE name LIKE '%CREATED%'",
        "SELECT * FROM orders WHERE status = 'updated'",
    ],
)
def test_valid_queries_are_accepted(sql):
    result = check(sql)
    assert not result.sql.endswith(";")
    assert "LIMIT" in result.sql.upper()
    assert result.tables <= TABLES


def test_trailing_semicolon_is_stripped_not_rejected():
    assert check("SELECT * FROM customers;").sql == "SELECT * FROM customers LIMIT 201"


def test_validated_sql_is_stable_when_validated_again():
    first = check("SELECT name FROM customers WHERE city = 'Delhi'")
    assert check(first.sql).sql == first.sql


# --- new accepted shapes --------------------------------------------------


def test_semicolon_inside_string_literal_is_accepted():
    # Closes limitation L2: the old textual check rejected this.
    result = check("SELECT * FROM customers WHERE city = 'Mum;bai'")
    assert "'Mum;bai'" in result.sql


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM main.customers",
        'SELECT * FROM "Customers"',
        "SELECT 1",
        "SELECT city FROM customers UNION SELECT name FROM products",
        "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n WHERE x < 5) "
        "SELECT x FROM n",
        "SELECT * FROM (SELECT id FROM orders) AS o",
    ],
)
def test_other_read_only_shapes_are_accepted(sql):
    check(sql)


def test_tables_lists_only_real_tables_not_ctes():
    result = check("WITH t AS (SELECT * FROM orders) SELECT * FROM t JOIN customers c ON 1 = 1")
    assert result.tables == {"orders", "customers"}


# --- writes and other statements (ported, now with codes) -----------------


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE customers",
        "DELETE FROM customers",
        "UPDATE products SET price = 0",
        "INSERT INTO customers (id) VALUES (99)",
        "ALTER TABLE customers ADD COLUMN hacked TEXT",
        "TRUNCATE TABLE orders",
        "CREATE TABLE evil (id INTEGER)",
        "REPLACE INTO customers (id) VALUES (1)",
        "WITH x AS (SELECT 1) DELETE FROM customers",
    ],
)
def test_writes_are_forbidden_writes(sql):
    error = rejected(sql)
    assert error.code == C.FORBIDDEN_WRITE
    assert not error.repairable


@pytest.mark.parametrize(
    "sql",
    [
        "ATTACH DATABASE '/etc/passwd' AS leak",
        "DETACH DATABASE leak",
        "PRAGMA database_list",
        "VACUUM",
        "EXPLAIN SELECT * FROM customers",
        "ANALYZE",
    ],
)
def test_non_query_statements_are_forbidden(sql):
    error = rejected(sql)
    assert error.code == C.FORBIDDEN_STATEMENT
    assert not error.repairable


def test_write_hidden_behind_a_leading_comment_is_still_a_write():
    assert rejected("/* harmless */ DROP TABLE customers").code == C.FORBIDDEN_WRITE


def test_write_detection_has_the_core_classes():
    # Guards against a sqlglot upgrade renaming classes that _classes() skips silently.
    for cls in (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop):
        assert cls in WRITE_TYPES


# --- stacking (ported) ----------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM customers; DROP TABLE customers",
        "SELECT 1; SELECT 2",
        "SELECT * FROM customers;;",
    ],
)
def test_multiple_statements_are_blocked(sql):
    error = rejected(sql)
    assert error.code == C.MULTIPLE_STATEMENTS
    assert "Multiple SQL statements" in error.detail


# --- comments (ported) ----------------------------------------------------


def test_keyword_after_line_comment_is_caught():
    rejected("SELECT 1 --\nDROP TABLE customers")


def test_keyword_inside_block_comment_is_ignored_safely():
    result = check("SELECT /* DROP TABLE x */ name FROM customers")
    assert "DROP" not in result.sql.upper()


def test_comment_only_input_is_rejected():
    assert rejected("-- just a comment").code == C.EMPTY_QUERY


# --- empty, oversized and malformed ---------------------------------------


@pytest.mark.parametrize("sql", ["", "   ", None, ";"])
def test_empty_input_is_rejected(sql):
    assert rejected(sql).code == C.EMPTY_QUERY


def test_oversized_sql_is_rejected_before_parsing():
    error = rejected("SELECT 1" + " " * 100, max_chars=50)
    assert error.code == C.TOO_LONG
    assert not error.repairable


def test_malformed_sql_is_a_repairable_parse_error_without_ansi_codes():
    error = rejected("SELECT * FROM customers WHERE (city = 'Delhi'")
    assert error.code == C.PARSE_ERROR
    assert error.repairable
    assert "\x1b" not in error.detail


def test_parse_result_that_is_not_an_expression_is_rejected(monkeypatch):
    monkeypatch.setattr(validator_module.sqlglot, "parse", lambda *args, **kwargs: [object()])
    assert rejected("SELECT 1").code == C.NOT_A_SELECT


def test_deeply_nested_sql_is_a_parse_error_not_a_crash():
    error = rejected("SELECT " + "(" * 1500 + "1" + ")" * 1500)
    assert error.code == C.PARSE_ERROR
    assert "nested too deeply" in error.detail


def test_bare_select_is_an_incomplete_query():
    # Found by the totality property test: sqlglot parses a bare SELECT and the
    # validator used to regenerate it as "SELECT LIMIT 201", which is not SQL.
    error = rejected("SELECT")
    assert error.code == C.PARSE_ERROR
    assert error.repairable


def test_tokenizer_error_is_a_parse_error():
    error = rejected("SELECT 'abc")
    assert error.code == C.PARSE_ERROR
    assert "\x1b" not in error.detail


@pytest.mark.parametrize("sql", ["DESCRIBE customers", "USE main", "SET x = 1", "SHOW TABLES"])
def test_other_statement_roots_are_forbidden(sql):
    error = rejected(sql)
    assert error.code == C.FORBIDDEN_STATEMENT
    assert not error.repairable


@pytest.mark.parametrize("sql", ["VALUES (1, 2)", "hello"])
def test_non_query_expressions_are_not_a_select(sql):
    error = rejected(sql)
    assert error.code == C.NOT_A_SELECT
    assert not error.repairable


# --- tables ---------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM buyers",
        "SELECT * FROM sqlite_master",
        "SELECT * FROM temp.customers",
        "SELECT * FROM other.customers",
        "SELECT * FROM (SELECT * FROM sqlite_schema)",
        "SELECT * FROM customers WHERE id IN (SELECT customer_id FROM secrets)",
    ],
)
def test_tables_outside_the_allowlist_are_rejected(sql):
    error = rejected(sql)
    assert error.code == C.UNKNOWN_TABLE
    assert error.repairable


def test_unknown_table_detail_lists_available_tables():
    detail = rejected("SELECT * FROM buyers").detail
    assert "buyers" in detail
    assert "customers, order_items, orders, products" in detail


# --- functions ------------------------------------------------------------


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT load_extension('evil.so')",
        "SELECT readfile('/etc/passwd')",
        "SELECT * FROM pragma_table_info('customers')",
        "SELECT name FROM customers WHERE id = (SELECT count(*) FROM pragma_table_list())",
    ],
)
def test_dangerous_functions_are_rejected(sql):
    assert rejected(sql).code == C.FORBIDDEN_FUNCTION


def test_table_valued_function_in_from_is_rejected():
    assert rejected("SELECT * FROM json_each('[1,2]')").code == C.FORBIDDEN_FUNCTION


def test_ordinary_functions_are_allowed():
    check("SELECT strftime('%Y-%m', order_date), COUNT(*), ROUND(AVG(1.5), 2) FROM orders")


# --- row cap --------------------------------------------------------------


def test_missing_limit_gets_cap_plus_one():
    result = check("SELECT * FROM orders")
    assert result.sql.endswith("LIMIT 201")
    assert result.query_limit is None


def test_limit_below_cap_is_kept_and_reported():
    result = check("SELECT * FROM orders ORDER BY id DESC LIMIT 5")
    assert result.sql.endswith("LIMIT 5")
    assert result.query_limit == 5


@pytest.mark.parametrize("requested", [200, 201, 1000])
def test_limit_at_or_above_cap_becomes_cap_plus_one(requested):
    result = check(f"SELECT * FROM orders LIMIT {requested}")
    assert result.sql.endswith("LIMIT 201")
    assert result.query_limit is None


def test_offset_is_preserved():
    result = check("SELECT * FROM orders LIMIT 10 OFFSET 20")
    assert "OFFSET 20" in result.sql
    assert result.query_limit == 10


def test_limit_applies_to_a_whole_union_once():
    result = check("SELECT city FROM customers UNION SELECT name FROM products LIMIT 5")
    assert result.sql.upper().count("LIMIT") == 1


def test_union_without_limit_gets_one_limit():
    result = check("SELECT city FROM customers UNION SELECT name FROM products")
    assert result.sql.upper().count("LIMIT") == 1
    assert result.sql.endswith("LIMIT 201")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * FROM orders LIMIT -1",
        "SELECT * FROM orders LIMIT (SELECT 5)",
        "SELECT * FROM orders LIMIT 2.5",
    ],
)
def test_non_literal_or_negative_limit_is_rejected(sql):
    error = rejected(sql)
    assert error.code == C.INVALID_LIMIT
    assert error.repairable


def test_max_rows_must_be_positive():
    with pytest.raises(ValueError):
        check("SELECT 1", max_rows=0)


# --- taxonomy -------------------------------------------------------------


def test_every_code_has_a_user_message():
    assert set(USER_MESSAGES) == set(SqlErrorCode)


def test_repairable_codes_are_exactly_the_approved_four():
    assert {
        C.PARSE_ERROR,
        C.UNKNOWN_TABLE,
        C.EXECUTION_ERROR,
        C.INVALID_LIMIT,
    } == REPAIRABLE_CODES


def test_error_string_carries_code_and_detail():
    error = SqlSafetyError(C.TIMEOUT, "stopped after 5.0 seconds")
    assert str(error) == "TIMEOUT: stopped after 5.0 seconds"
    assert error.user_message == USER_MESSAGES[C.TIMEOUT]
