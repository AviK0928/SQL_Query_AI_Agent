"""Property-based tests for app/sql/validator.py (S1, Phase 3).

Example tests show that chosen inputs behave. These show that whole classes of
input do: generated text, stacked statements, obfuscated writes and arbitrary
LIMIT values. Profiles are in tests/unit/conftest.py (derandomized in CI).
"""

import re

import pytest
import sqlglot
from hypothesis import given
from hypothesis import strategies as st
from sqlglot import exp

from app.sql.errors import SqlErrorCode, SqlSafetyError
from app.sql.validator import validate_sql

TABLES = frozenset({"customers", "orders", "order_items", "products"})
CAP = 200
C = SqlErrorCode

QUERY_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)
WRITE_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Create, exp.Drop)


def validate(sql, max_rows=CAP):
    return validate_sql(sql, allowed_tables=TABLES, max_rows=max_rows)


def assert_safe(sql: str, max_rows: int = CAP) -> None:
    """What every accepted query must be, checked independently of the validator."""
    parsed = sqlglot.parse(sql, read="sqlite")
    assert len(parsed) == 1
    root = parsed[0]
    assert isinstance(root, QUERY_ROOTS), f"accepted a non-query root: {sql!r}"
    assert next(root.find_all(*WRITE_NODES), None) is None, f"accepted a write: {sql!r}"

    ctes = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
    used = {t.name.lower() for t in root.find_all(exp.Table)} - ctes
    assert used <= TABLES, f"accepted tables outside the allowlist: {used - TABLES}"

    limit = root.args.get("limit")
    assert limit is not None, f"accepted query has no LIMIT: {sql!r}"
    value = limit.args.get("expression")
    assert isinstance(value, exp.Literal) and int(value.name) <= max_rows + 1


# --- 1. totality ------------------------------------------------------------

SQL_TOKENS = [
    "SELECT",
    "*",
    "FROM",
    "customers",
    "orders",
    "sqlite_master",
    "t",
    "WHERE",
    "id",
    "=",
    "1",
    "'x'",
    ";",
    "(",
    ")",
    ",",
    "AS",
    "WITH",
    "UNION",
    "LIMIT",
    "5",
    "-1",
    "DROP",
    "TABLE",
    "DELETE",
    "INSERT",
    "INTO",
    "VALUES",
    "PRAGMA",
    "ATTACH",
    "--",
    "/*",
    "*/",
    "\n",
    "load_extension",
    "json_each",
    "main.",
    "COUNT(*)",
]

sql_like = st.lists(st.sampled_from(SQL_TOKENS), min_size=1, max_size=25).map(" ".join)


@given(st.one_of(st.text(max_size=300), sql_like))
def test_any_input_is_accepted_safely_or_rejected_with_a_safety_error(text):
    """No input may crash the validator, and nothing unsafe may be accepted."""
    try:
        result = validate(text)
    except SqlSafetyError:
        return
    assert_safe(result.sql)


# --- 2. stacking ------------------------------------------------------------

BASES = [
    "SELECT * FROM customers",
    "SELECT id FROM orders WHERE status = 'shipped'",
    "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
]
SECONDS = [
    "DROP TABLE customers",
    "DELETE FROM orders",
    "UPDATE products SET price = 0",
    "PRAGMA database_list",
    "ATTACH DATABASE 'x.db' AS leak",
    "SELECT 1",
]
SEPARATORS = [";", "; ", ";\n", " ;", ";/* c */ ", ";\t"]


@given(
    st.sampled_from(BASES),
    st.sampled_from(SEPARATORS),
    st.sampled_from(SECONDS),
    st.booleans(),
)
def test_a_second_statement_is_never_accepted(base, separator, second, trailing):
    sql = base + separator + second + (";" if trailing else "")
    with pytest.raises(SqlSafetyError) as info:
        validate(sql)
    assert info.value.code == C.MULTIPLE_STATEMENTS
    assert not info.value.repairable


# --- 3. obfuscated writes ----------------------------------------------------

WRITES = [
    "DROP TABLE customers",
    "DELETE FROM customers",
    "UPDATE products SET price = 0",
    "INSERT INTO customers (id) VALUES (1)",
]
GAPS = [" ", "  ", "\n", "\t", " /* x */ ", " /**/ ", "\n-- note\n"]
LEADS = ["", " ", "/* lead */ ", "-- lead\n", "\n\n"]


@st.composite
def obfuscated_writes(draw):
    words = draw(st.sampled_from(WRITES)).split(" ")
    words = ["".join(c.upper() if draw(st.booleans()) else c.lower() for c in w) for w in words]
    gaps = [draw(st.sampled_from(GAPS)) for _ in words[1:]]
    return (
        draw(st.sampled_from(LEADS))
        + words[0]
        + "".join(g + w for g, w in zip(gaps, words[1:], strict=True))
    )


@given(obfuscated_writes())
def test_obfuscated_writes_are_always_forbidden(sql):
    with pytest.raises(SqlSafetyError) as info:
        validate(sql)
    assert info.value.code == C.FORBIDDEN_WRITE


# --- 4. row cap -------------------------------------------------------------


def enforced_limit(sql: str) -> int:
    match = re.search(r"LIMIT (\d+)$", sql)
    assert match, f"no trailing LIMIT in {sql!r}"
    return int(match.group(1))


@given(n=st.integers(min_value=0, max_value=10**6), cap=st.integers(min_value=1, max_value=5000))
def test_enforced_limit_never_exceeds_cap_plus_one(n, cap):
    result = validate(f"SELECT id FROM orders LIMIT {n}", max_rows=cap)
    enforced = enforced_limit(result.sql)

    assert enforced <= cap + 1
    if n < cap:
        assert (result.query_limit, enforced) == (n, n)
    else:
        assert (result.query_limit, enforced) == (None, cap + 1)


@given(cap=st.integers(min_value=1, max_value=5000))
def test_missing_limit_always_gets_cap_plus_one(cap):
    result = validate("SELECT id FROM orders", max_rows=cap)
    assert enforced_limit(result.sql) == cap + 1
    assert result.query_limit is None
