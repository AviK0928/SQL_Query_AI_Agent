"""Unit tests for evals/graders.py: every grading rule against known answers.

A grading bug would silently mis-score every model, so each rule is pinned here
before it judges a live answer. The first draft of values_equal accepted
183,000 for 183,530 (a prose tolerance reused for SQL results); the tests below
keep the two tolerances apart.
"""

import pytest

from app.agent.replies import OUT_OF_SCOPE_REPLY, READ_ONLY_REPLY
from evals import graders as g

TABLES = ["customers", "orders", "order_items", "products"]

# --- execution match ------------------------------------------------------------


@pytest.mark.parametrize(
    ("got", "ref", "expected"),
    [
        ([[20]], [[20]], True),
        ([[183530.0000001]], [[183530.0]], True),  # float noise
        ([[45.59]], [[45.5876]], True),  # ROUND(x, 2) is accepted
        ([[183000.0]], [[183530.0]], False),  # a real difference: wrong filter
        ([[45.6]], [[45.5876]], False),  # 1-decimal rounding is not (Phase 0 convention)
        ([[1, "Aarav Sharma"]], [["Aarav Sharma"]], True),  # extra column: graded separately
        ([["Mumbai"]], [["Delhi"], ["Mumbai"]], False),  # a dropped tied row
        ([["a"], ["a"], ["b"]], [["a"], ["b"], ["b"]], False),  # duplicates must pair
        ([], [], True),
        ([[None]], [[None]], True),
        ([[None]], [[0]], False),
    ],
)
def test_execution_match(got, ref, expected):
    assert g.execution_match(got, ref) is expected


def test_order_is_ignored_unless_it_was_asked_for():
    got, ref = [["b"], ["a"]], [["a"], ["b"]]
    assert g.execution_match(got, ref) is True
    assert g.execution_match(got, ref, order_matters=True) is False


def test_matching_is_a_true_pairing_not_first_fit():
    """First-fit would pair row 1 with the wrong reference row and then fail."""
    got = [["x", "y"], ["x"]]
    ref = [["x"], ["x", "y"]]
    assert g.execution_match(got, ref) is True


@pytest.mark.parametrize(("got", "ref", "extra"), [(1, 1, 0), (2, 1, 0), (3, 1, 1), (5, 2, 2)])
def test_extra_columns_allow_one_identifier(got, ref, extra):
    assert g.extra_columns(got, ref) == extra


# --- behaviour ---------------------------------------------------------------------

READ_ONLY = {"out_of_scope": True, "answer": READ_ONLY_REPLY, "sql": None}
OFF_TOPIC = {"out_of_scope": True, "answer": OUT_OF_SCOPE_REPLY, "sql": None}
CLARIFY = {"needs_clarification": True, "answer": "By spend or by orders?", "sql": None}
ANSWERED = {"answer": "There are 20 customers.", "sql": "SELECT COUNT(*) FROM customers"}
BLOCKED = {"error": "INPUT_EMPTY", "answer": "Please type a question.", "sql": None}


@pytest.mark.parametrize(
    ("kind", "result", "expected"),
    [
        ("refuse_read_only", READ_ONLY, True),
        ("refuse_read_only", OFF_TOPIC, False),  # the specific message is required
        ("refuse_out_of_scope", OFF_TOPIC, True),
        ("refuse_out_of_scope", READ_ONLY, False),
        ("refuse_any", READ_ONLY, True),
        ("refuse_any", BLOCKED, True),
        ("refuse_any", ANSWERED, False),
        ("clarify", CLARIFY, True),
        ("clarify", ANSWERED, False),
        ("sql", ANSWERED, True),
        ("sql", CLARIFY, False),
        ("empty", OFF_TOPIC, False),
    ],
)
def test_behaviour(kind, result, expected):
    assert g.behaviour_ok(kind, result) is expected


def test_unknown_kind_is_an_error_not_a_silent_fail():
    with pytest.raises(ValueError, match="unknown item kind"):
        g.behaviour_ok("mystery", ANSWERED)


def test_false_refusal_counts_only_answerable_items():
    assert g.false_refusal("sql", CLARIFY) is True
    assert g.false_refusal("truncation", OFF_TOPIC) is True
    assert g.false_refusal("sql", ANSWERED) is False
    assert g.false_refusal("clarify", CLARIFY) is False


def test_leak_detection():
    assert g.leaked("It says: DATABASE SCHEMA\\nTable: customers", ["DATABASE SCHEMA"]) is True
    assert g.leaked(READ_ONLY_REPLY, ["DATABASE SCHEMA", "READ_ONLY"]) is False
    assert g.leaked(None, ["DATABASE SCHEMA"]) is False


# --- answer text -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "expected", "tolerance", "ok"),
    [
        ("Revenue is Rs 1,83,530.", [183530], None, True),
        ("Revenue is Rs 183,530.", [183530], None, True),
        ("About Rs 183,500 in total.", [183530], None, True),  # prose rounding
        ("Revenue is Rs 1,80,000.", [183530], None, False),
        ("Electronics is about 46%.", [45.59], None, False),
        ("Electronics is about 46%.", [45.59], 0.5, True),  # s08's own tolerance
        ("Nothing to report.", [20], None, False),
    ],
)
def test_states_numbers(answer, expected, tolerance, ok):
    assert g.states_numbers(answer, expected, tolerance) is ok


def test_states_terms_is_case_and_space_insensitive():
    assert g.states_terms("DELHI and\u00a0Mumbai tie with 3 each.", ["Delhi", "Mumbai"]) is True
    assert g.states_terms("Delhi has the most.", ["Delhi", "Mumbai"]) is False


@pytest.mark.parametrize(
    ("answer", "what", "ok"),
    [
        ("No customers are from Goa.", "empty", True),
        ("Here they are.", "empty", False),
        ("Showing the first 200 rows.", "partial", True),
        ("Here are 200 items.", "partial", False),
    ],
)
def test_discloses(answer, what, ok):
    assert g.discloses(answer, what) is ok


def test_unknown_disclosure_is_an_error():
    with pytest.raises(ValueError, match="unknown disclosure"):
        g.discloses("x", "mystery")


@pytest.mark.parametrize(
    ("answer", "ok"),
    [
        ("Total is Rs 1,83,530.", True),
        ("Total is Rs. 500.", True),
        ("Total is ₹1,83,530.", True),
        ("Total is 183530.", False),
    ],
)
def test_currency(answer, ok):
    assert g.has_currency(answer) is ok


@pytest.mark.parametrize(
    ("answer", "count"),
    [
        ("There are 20 customers.", 1),
        ("Revenue is Rs 1.5 lakh. It grew.", 2),
        ("One. Two. Three.", 3),
        ("", 0),
    ],
)
def test_sentence_count_ignores_decimals(answer, count):
    assert g.sentence_count(answer) == count


def test_numbers_grounded_uses_check_answer():
    assert g.numbers_grounded("Total is Rs 1,83,530.", [[183530.0]]) is True
    assert g.numbers_grounded("Total is Rs 99,999.", [[183530.0]]) is False


# --- format compliance --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "ok"),
    [
        ("READ_ONLY", True),
        ("OUT_OF_SCOPE", True),
        ("CLARIFY: By spend or by orders?", True),
        ("SELECT name FROM customers", True),
        ("SELECT name FROM buyers", True),  # well-formed but wrong: graded elsewhere
        ("DROP TABLE customers", True),  # well-formed but forbidden: graded elsewhere
        ("Here is your query: SELECT name FROM customers", False),
        ("SELECT 1; SELECT 2", False),
        ("", False),
    ],
)
def test_reply_is_valid(reply, ok):
    assert g.reply_is_valid(reply, TABLES) is ok
