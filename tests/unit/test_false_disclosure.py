"""evals/graders.py false_disclosure: partial-result warnings on complete results.

The real cases are baseline answers (run 2026-09-27-full-gpt-oss-120b) whose
labels were agreed during judge calibration (L13, L21, D46).
"""

import json

import pytest

from evals import graders as g
from evals import runner
from tests.fakes import TEST_MODEL, FakeLLM

TOP_N = "Only the first rows are shown."


@pytest.mark.parametrize(
    ("question", "answer", "rows", "truncated", "limit_reached", "expected"),
    [
        # g07, L21: all 30 rows came back; the answer model saw 20 of them.
        ("List each order with the customer's name and the order date.",
         "The first 20 orders are shown, ranging from order 1 through order 20. "
         "Only the initial rows are displayed.", 30, False, False, True),
        # g35, L21: all 45 rows came back.
        ("List every combination of an order ID and a product name.",
         "Order 1 has Wireless Mouse and Notebook Set, order 2 has Noise Cancelling "
         "Headphones. Only the first 20 of the 45 matching rows are displayed.",
         45, False, False, True),
        # g16, L13: an intended top 3 (with the narrow no-break spaces the model writes).
        ("What are the 3 most expensive products?",
         "The three most expensive products are Standing Desk at Rs 21,999, Office "
         "Chair at Rs 12,499 and Noise Cancelling Headphones at Rs 8,999. " + TOP_N,
         3, False, True, True),
        # g17, L13: an intended top 5.
        ("Which 5 customers have spent the most?",
         "The five customers who have spent the most are Vikram Nair and four others. " + TOP_N,
         5, False, True, True),
        # g25, L13: an intended top 3.
        ("Who are the top 3 customers by total spend?",
         "The top three customers are Vikram Nair, Ananya Iyer and Dev Chauhan. " + TOP_N,
         3, False, True, True),
        # g18: LIMIT 1 hid a tie the question did not ask to drop, so the warning is true.
        ("Which city has the most customers?",
         "Mumbai has the most customers, with 3 customers (only the first rows are shown).",
         1, False, True, False),
        # g22: LIMIT 10 was the model's choice; more customers exist.
        ("Who are our most valuable customers?",
         "Our most valuable customers are Vikram Nair and nine others. (Only the first rows "
         "are shown; additional matching customers may exist.)", 10, False, True, False),
        # g34: LIMIT 100 on "every pair"; 190 exist.
        ("List every possible pair of two different customers.",
         "Only the first 20 of the 100 matching rows are shown.", 100, False, True, False),
        # a04: complete, and the answer claims nothing about missing rows.
        ("Show all customer names.",
         "The query returned the following customer names: Aarav Sharma, Diya Patel.",
         20, False, False, False),
        # A capped result: a warning is required there, never false.
        ("List all orders.", "These are only the first 200 rows.", 200, True, False, False),
    ],
    ids=["g07", "g35", "g16", "g17", "g25", "g18", "g22", "g34", "a04", "truncated"],
)  # fmt: skip
def test_baseline_answers(question, answer, rows, truncated, limit_reached, expected):
    assert g.false_disclosure(answer, question, rows, truncated, limit_reached) is expected


@pytest.mark.parametrize(
    "answer",
    [
        "Only Mumbai has three customers.",
        "The first 20 customers signed up in 2024.",
        "Only 2 results match your filter.",
    ],
)
def test_ordinary_uses_of_only_and_first_are_not_claims(answer):
    assert g.claims_partial(answer) is False


def test_number_words_in_the_question_count_as_an_intended_top_n():
    question = "Show the top five products by price."
    assert g.false_disclosure(TOP_N, question, 5, False, True) is True
    assert g.false_disclosure(TOP_N, question, 4, False, True) is False


def test_synthesizer_items_are_checked():
    item = {
        "question": "What are the 3 most expensive products?",
        "rows": [["A", 1], ["B", 2], ["C", 3]],
        "truncated": False,
        "limit_reached": True,
        "expected_numbers": [],
        "expected_terms": [],
        "money": False,
    }
    assert runner.grade_synthesizer(item, "A, B and C. " + TOP_N)["false_disclosure"] is True
    assert runner.grade_synthesizer(item, "A, B and C.")["false_disclosure"] is False


def test_agent_runs_record_and_report_it(test_settings, tmp_path):
    run_dir, code = runner.run(
        role="sql_generator",
        model=TEST_MODEL,
        suites=["golden"],
        ids=["g04"],
        repeats=1,
        tag="t",
        base_settings=test_settings,
        make_llm=lambda s: FakeLLM("SELECT COUNT(*) FROM customers", "There are 20 customers."),
        reports=tmp_path,
        sleep=lambda s: None,
        yes=True,
        today="2026-01-01",
    )
    (rec,) = [json.loads(line) for line in (run_dir / "results.jsonl").read_text().splitlines()]
    assert code == 0 and rec["false_disclosure"] is False
    report = (run_dir / "report.md").read_text()
    assert "False partial-result warnings (reported, not scored): 0 of 1 answers" in report
