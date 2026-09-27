"""Snapshot tests of the rendered messages sent to the model (T14).

The stored snapshots (tests/unit/__snapshots__/test_prompt_snapshots.ambr) hold
the complete message lists, system prompts included, for each kind of call.
Any change to what the model receives fails here with a readable diff, until it
is accepted with `pytest tests/unit/test_prompt_snapshots.py --snapshot-update`
and the updated .ambr file is reviewed and committed with the change.

This complements test_prompt_ids.py: the ids pin the system prompts by hash,
and these snapshots also cover how questions, history, errors, rows and result
notes are assembled around them.
"""

from app.prompts import build_answer_messages, build_retry_messages, build_sql_messages

HISTORY = [
    {"question": "How many customers are in Mumbai?", "sql": "SELECT COUNT(*) FROM customers"},
]


def test_sql_first_turn(snapshot):
    assert build_sql_messages("How many orders were cancelled?") == snapshot


def test_sql_with_history(snapshot):
    assert build_sql_messages("And in Pune?", HISTORY) == snapshot


def test_repair(snapshot):
    messages = build_retry_messages(
        "Total revenue?", "SELECT SUM(revenue) FROM orders", "no such column: revenue"
    )
    assert messages == snapshot


def test_answer_with_rows(snapshot):
    rows = [["Delhi", 5], ["Mumbai", 4]]
    assert build_answer_messages("Customers per city?", ["city", "n"], rows) == snapshot


def test_answer_empty(snapshot):
    assert build_answer_messages("Customers in Goa?", ["name"], []) == snapshot


def test_answer_with_every_note(snapshot):
    rows = [[i] for i in range(25)]
    messages = build_answer_messages(
        "List order ids", ["id"], rows, truncated=True, limit_reached=True
    )
    assert messages == snapshot
