"""Tests for prompt assembly.

The important one is test_prompt_schema_matches_database: prompts.py restates
the schema in prose for the LLM, so it can silently drift out of sync with
schema.sql. When that happens the model writes SQL against a schema that no
longer exists, and the failure looks like a bad model rather than stale config.
"""

import pytest

from app.db import get_schema
from app.prompts import (
    SCHEMA_DESCRIPTION,
    SQL_SYSTEM_PROMPT,
    OUT_OF_SCOPE_TOKEN,
    build_sql_messages,
    build_retry_messages,
    build_answer_messages,
)


def test_prompt_schema_matches_database():
    """Every real table and column must be described in the prompt."""
    actual = get_schema()
    missing = []

    for table in actual["tables"]:
        if f"Table: {table['name']}" not in SCHEMA_DESCRIPTION:
            missing.append(f"table {table['name']}")
            continue
        for column in table["columns"]:
            if column["name"] not in SCHEMA_DESCRIPTION:
                missing.append(f"{table['name']}.{column['name']}")

    assert not missing, f"prompts.py is out of sync with the database: {missing}"


def test_prompt_describes_no_phantom_tables():
    """The prompt must not mention tables that do not exist."""
    real = {t["name"] for t in get_schema()["tables"]}
    described = {
        line.split("Table:")[1].strip()
        for line in SCHEMA_DESCRIPTION.splitlines()
        if line.startswith("Table:")
    }
    assert described == real, f"described={described} actual={real}"


# --- message assembly ---------------------------------------------------

def test_sql_messages_without_history():
    m = build_sql_messages("Show all customers")
    assert [x["role"] for x in m] == ["system", "user"]
    assert m[-1]["content"] == "Show all customers"


def test_sql_messages_replay_history_in_order():
    history = [
        {"question": "customers in Mumbai", "sql": "SELECT * FROM customers WHERE city='Mumbai'"},
        {"question": "how many?", "sql": "SELECT COUNT(*) FROM customers WHERE city='Mumbai'"},
    ]
    m = build_sql_messages("and Pune?", history)
    assert [x["role"] for x in m] == ["system", "user", "assistant", "user", "assistant", "user"]
    assert m[1]["content"] == "customers in Mumbai"
    assert m[-1]["content"] == "and Pune?"


def test_retry_messages_include_the_error():
    m = build_retry_messages("Show revenue", "SELECT revenue FROM customers", "no such column: revenue")
    assert "no such column: revenue" in m[-1]["content"]
    assert m[2]["content"] == "SELECT revenue FROM customers"


def test_answer_messages_handle_empty_results():
    m = build_answer_messages("Any customers in Goa?", ["name"], [])
    assert "no rows returned" in m[-1]["content"]


def test_answer_messages_flag_truncation():
    m = build_answer_messages("List all", ["id"], [[i] for i in range(5)], truncated=True)
    assert "truncated" in m[-1]["content"]


def test_answer_messages_cap_rows_sent_to_llm():
    m = build_answer_messages("List all", ["id"], [[i] for i in range(200)])
    assert "199" not in m[-1]["content"], "should send at most 20 rows"


# --- prompt content guarantees ------------------------------------------

@pytest.mark.parametrize("rule", [
    "unit_price",        # D1: use price actually paid
    "cancelled",         # exclude cancelled orders from totals
    "LIMIT",             # cap result size
    OUT_OF_SCOPE_TOKEN,  # scope + injection handling
])
def test_sql_prompt_states_key_rules(rule):
    assert rule in SQL_SYSTEM_PROMPT
