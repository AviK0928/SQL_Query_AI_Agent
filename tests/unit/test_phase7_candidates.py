"""Phase 7 pre-registered candidates: prompt versions, synthesizer_v2, the D52 rule.

The candidates are released but inactive. These tests pin that each one changes
exactly what its experiment says it changes (PROMPTS.md pre-registration), that
synthesizer_v2 extends v1 without altering it, and the clarify rule and re-score.
"""

import json
import shutil

import pytest

from app.prompts.variants import system_prompt
from evals import graders as g
from evals import runner

D41 = runner.REPORTS / "2026-09-27-full-gpt-oss-120b"


def _added_lines(name, version):
    old = system_prompt(name, 1)[0].splitlines()
    new = system_prompt(name, version)[0].splitlines()
    return [line for line in new if line not in old], [line for line in old if line not in new]


def test_answer_v2_only_appends_two_rules():
    v1, v2 = system_prompt("answer", 1)[0], system_prompt("answer", 2)[0]
    assert v2.startswith(v1 + "\n- The user sees the result rows in a table")
    assert v2.count("\n- ") == v1.count("\n- ") + 2


def test_sql_gen_v2_rewrites_only_rule_5():
    added, removed = _added_lines("sql_gen", 2)
    assert removed[0].startswith("5. Unless the user says otherwise") and len(removed) == 2
    assert added[0].startswith("5. Cancelled orders were placed") and "cancelled_orders" in "".join(
        added
    )


def test_sql_gen_v3_only_drops_rule_3():
    v1, v3 = system_prompt("sql_gen", 1)[0], system_prompt("sql_gen", 3)[0]
    assert "LIMIT of at most 100" in v1 and "LIMIT" not in v3.split("RULES")[1].split("SCOPE")[0]
    renumbered = (
        v1.replace(
            "3. Always add a LIMIT of at most 100 unless the question asks for a single\n"
            "   aggregate value.\n",
            "",
        )
        .replace("\n4. Revenue", "\n3. Revenue")
        .replace("\n5. Unless", "\n4. Unless")
    )
    assert v3 == renumbered.replace("\n6. Dates", "\n5. Dates")


def test_sql_gen_v4_only_adds_rule_7():
    added, removed = _added_lines("sql_gen", 4)
    assert removed == [] and added[0].startswith("7. When the question asks for the top")
    assert len(added) == 2


def test_sql_gen_v5_combines_the_kept_changes():
    """v5 (D63) = v1 + v2's rule 5 (exp 3, D60) + v4's rule 7 (exp 6, D62), nothing else."""
    added2, removed2 = _added_lines("sql_gen", 2)
    added4, removed4 = _added_lines("sql_gen", 4)
    added5, removed5 = _added_lines("sql_gen", 5)
    assert added5 == added2 + added4 and removed5 == removed2 + removed4
    assert "LIMIT of at most 100" in system_prompt("sql_gen", 5)[0]  # exp 4 reverted (D61)


# --- synthesizer_v2 ---------------------------------------------------------------------


def test_synthesizer_v2_extends_v1_unchanged():
    v1 = runner.load_suite("synthesizer")
    v2 = runner.load_suite("synthesizer_v2")
    assert v2[: len(v1)] == v1
    assert [i["id"] for i in v2[len(v1) :]] == ["s09", "s10", "s11", "s12"]


def test_new_items_have_the_failing_shapes():
    items = {i["id"]: i for i in runner.load_suite("synthesizer_v2")}
    for complete_list in ("s09", "s10"):  # L21: more rows than the answer model sees
        item = items[complete_list]
        assert len(item["rows"]) > 20 and not item["truncated"] and not item["limit_reached"]
    for top_n, n in (("s11", 3), ("s12", 5)):  # L13: the LIMIT is the question
        item = items[top_n]
        assert len(item["rows"]) == n and item["limit_reached"] and str(n) in item["question"]
        assert item["must_disclose"] is None


# --- D52: clarify, or state the assumption ------------------------------------------------


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("Our most valuable customers (based on total spend) are Vikram Nair ...", True),
        ("Ranked by revenue, Electronics leads.", True),
        ("Your most valuable customers are Vikram Nair and Ananya Iyer.", False),
        ("Electronics is top with Rs 83,674 in revenue and 26 units sold.", False),
    ],
    ids=["g22-r0", "ranked-by", "g22-r1", "g24-r1"],
)
def test_states_assumption(answer, expected):
    assert g.states_assumption(answer) is expected


def test_clarify_accepts_a_stated_assumption_but_not_a_silent_guess():
    sql = "SELECT name FROM customers"
    stated = {"answer": "Based on total spend: Vikram Nair.", "sql": sql}
    silent = {"answer": "Vikram Nair.", "sql": sql}
    failed = {"answer": "Based on spend, something failed.", "sql": sql, "error": "TIMEOUT"}
    assert g.behaviour_ok("clarify", stated) is True
    assert g.behaviour_ok("clarify", silent) is False
    assert g.behaviour_ok("clarify", failed) is False


def test_rescore_changes_only_clarify_items_and_leaves_the_source(tmp_path):
    src = tmp_path / D41.name
    shutil.copytree(D41, src, ignore=shutil.ignore_patterns("cache"))
    before = (src / "results.jsonl").read_text()
    dst = runner.rescore_clarify(src, tmp_path)
    assert (src / "results.jsonl").read_text() == before
    old = [json.loads(line) for line in before.splitlines()]
    new = [json.loads(line) for line in (dst / "results.jsonl").read_text().splitlines()]
    flipped = [(a["id"], a["repeat"]) for a, b in zip(old, new, strict=True) if a != b]
    assert flipped == [("g22", 0), ("g22", 2)]
    assert json.loads((dst / "manifest.json").read_text())["rescored_from"] == D41.name
    assert (dst / "report.md").exists()
