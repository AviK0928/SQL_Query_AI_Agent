"""Unit tests for app/agent/nodes/check.py (check_answer, Phase 5)."""

import pytest

from app.agent.nodes.check import (
    EMPTY_NOTE,
    EMPTY_UNDISCLOSED,
    LIMIT_UNDISCLOSED,
    TRUNCATION_UNDISCLOSED,
    UNSUPPORTED_NUMBERS,
    check_answer,
    normalize,
    numbers_in,
)

# --- disclosure --------------------------------------------------------------


def test_an_undisclosed_empty_result_gets_a_note():
    result = check_answer("Here they are.", [])
    assert result.answer == f"Here they are. {EMPTY_NOTE}"
    assert result.findings == (EMPTY_UNDISCLOSED,)


@pytest.mark.parametrize(
    "answer", ["No customers live in Goa.", "I couldn't find any orders.", "There are none."]
)
def test_a_disclosed_empty_result_is_left_alone(answer):
    result = check_answer(answer, [])
    assert (result.answer, result.findings) == (answer, ())


def test_undisclosed_truncation_gets_a_note_with_the_row_count():
    result = check_answer("Two customers shown.", [[1], [2]], truncated=True)
    assert result.answer.endswith(
        "Only the first 2 rows were returned, so this may not include every match."
    )
    assert result.findings == (TRUNCATION_UNDISCLOSED,)


def test_undisclosed_query_limit_gets_a_note():
    result = check_answer("Three customers.", [[1], [2], [3]], limit_reached=True)
    assert result.answer.endswith(
        "The query was limited to 3 rows, so more matching records may exist."
    )
    assert result.findings == (LIMIT_UNDISCLOSED,)


@pytest.mark.parametrize(
    "answer",
    [
        "Showing the first 2 customers.",
        "These are only some of the matches.",
        "Results were truncated.",
    ],
)
def test_disclosed_partial_results_are_left_alone(answer):
    result = check_answer(answer, [[1], [2]], truncated=True, limit_reached=True)
    assert (result.answer, result.findings) == (answer, ())


def test_non_breaking_spaces_do_not_hide_a_disclosure():
    result = check_answer("Showing\u00a0the\u202ffirst 2 rows.", [[1], [2]], truncated=True)
    assert result.findings == ()


# --- number grounding ----------------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "rows", "question"),
    [
        ("Total revenue is Rs 1,83,530.", [[183530.0]], ""),
        ("Total revenue is Rs 183,530.", [[183530.0]], ""),
        ("The average is 1,234.57.", [[1234.5678]], ""),
        ("There are 20 customers.", [[20]], ""),
        ("Orders in 2024: 12", [[12]], "How many orders in 2024?"),
        ("Signed up in 2024-03.", [["2024-03-09"]], ""),
        ("3 cities.", [["Delhi"], ["Pune"], ["Goa"]], ""),
    ],
)
def test_numbers_traceable_to_rows_count_or_question_pass(answer, rows, question):
    assert UNSUPPORTED_NUMBERS not in check_answer(answer, rows, question=question).findings


def test_an_invented_number_is_flagged_but_the_text_is_unchanged():
    result = check_answer("The top spender spent Rs 99,999.", [[183530.0]])
    assert result.findings == (UNSUPPORTED_NUMBERS,)
    assert result.answer == "The top spender spent Rs 99,999."


def test_booleans_are_not_numbers():
    assert check_answer("1 active.", [[True]]).findings == ()  # 1 is the row count


# --- helpers -------------------------------------------------------------------


def test_numbers_are_parsed_with_indian_grouping_and_nbsp():
    assert numbers_in("Rs\u00a01,83,530 and 12.5% and 3") == [183530.0, 12.5, 3.0]


def test_numbers_inside_words_are_ignored():
    assert numbers_in("gpt-oss-120b v2 costs 5") == [5.0]


def test_normalize_collapses_unicode_space_and_case():
    assert normalize("Showing\u00a0THE\u202f first") == "showing the first"
