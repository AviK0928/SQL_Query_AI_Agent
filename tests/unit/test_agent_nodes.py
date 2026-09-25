"""Unit tests for the pure node logic in app/agent/nodes/ (Phase 5)."""

import pytest

from app.agent.nodes.classify import Intent, classify_reply
from app.agent.nodes.guard import MAX_QUESTION_CHARS, guard_input
from app.agent.replies import CLARIFY_FALLBACK

# --- guard_input ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "code"),
    [
        (None, "INPUT_EMPTY"),
        ("", "INPUT_EMPTY"),
        ("  \n\t ", "INPUT_EMPTY"),
        ("x" * (MAX_QUESTION_CHARS + 1), "INPUT_TOO_LONG"),
        ("???", "INPUT_NO_TEXT"),
        ("12345 !!", "INPUT_NO_TEXT"),
    ],
)
def test_guard_rejects_with_a_code_and_a_reply(question, code):
    rejection = guard_input(question)
    assert rejection is not None
    assert rejection.code == code
    assert rejection.reply.strip()


@pytest.mark.parametrize(
    "question",
    ["How many customers are there?", "x" * MAX_QUESTION_CHARS, "¿Cuántos clientes hay?", "top 5?"],
)
def test_guard_lets_real_questions_through(question):
    assert guard_input(question) is None


# --- classify_intent -------------------------------------------------------------


@pytest.mark.parametrize(
    ("reply", "intent", "text"),
    [
        ("  SELECT name FROM customers  ", Intent.SQL, "SELECT name FROM customers"),
        (
            "SELECT 'CLARIFY: not a token here' AS x",
            Intent.SQL,
            "SELECT 'CLARIFY: not a token here' AS x",
        ),
        ("READ_ONLY", Intent.READ_ONLY, ""),
        ("OUT_OF_SCOPE", Intent.OUT_OF_SCOPE, ""),
        ("CLARIFY: By spend or by order count?", Intent.CLARIFY, "By spend or by order count?"),
        ("clarify: which year?", Intent.CLARIFY, "which year?"),
        ("CLARIFY", Intent.CLARIFY, CLARIFY_FALLBACK),
        ("CLARIFY:   ", Intent.CLARIFY, CLARIFY_FALLBACK),
    ],
)
def test_replies_are_classified(reply, intent, text):
    result = classify_reply(reply)
    assert (result.intent, result.text) == (intent, text)


def test_read_only_wins_over_every_other_token():
    """Specific before general: a write request is never treated as anything else."""
    assert classify_reply("CLARIFY: READ_ONLY?").intent is Intent.READ_ONLY
    assert classify_reply("READ_ONLY OUT_OF_SCOPE").intent is Intent.READ_ONLY


def test_clarify_is_checked_before_out_of_scope():
    assert classify_reply("CLARIFY: is this OUT_OF_SCOPE for you?").intent is Intent.CLARIFY
