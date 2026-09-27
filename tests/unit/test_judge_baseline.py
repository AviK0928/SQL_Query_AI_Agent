"""evals/judges/run.py: judging a whole eval run and summarising it (D48).

Offline: the selection is pinned on the committed D41 results, and judge runs use
FakeLLM.
"""

import json

from evals.judges import calibration as cal
from evals.judges import run as jrun
from evals.judges.response import CRITERIA
from tests.fakes import TEST_MODEL, FakeLLM

D41 = jrun.REPORTS / cal.BASELINE_RUN / "results.jsonl"


def _verdict(score):
    return {c: {"score": score, "reason": "r"} for c in CRITERIA}


def _case(item_id, repeat=0, answer="There are 20.", rows=1, limit_reached=False):
    return {
        "id": item_id, "repeat": repeat, "tier": "T1", "kind": "sql", "earlier_questions": [],
        "question": "What are the 3 most expensive products?", "sql": "SELECT 1",
        "columns": ["n"], "rows": [[i] for i in range(rows)], "truncated": False,
        "limit_reached": limit_reached, "answer": answer, "reference_sql": None,
        "reference_rows": None, "matches_reference": None,
    }  # fmt: skip


def test_every_answered_item_of_the_baseline_is_selected():
    records = cal.load_jsonl(D41)
    assert len(jrun.select_run(records, [0])) == 35
    assert len(jrun.select_run(records, [0, 1, 2])) == 107


def test_calibration_cases_are_part_of_the_baseline_selection():
    """So the calibration run's cached verdicts answer those cases again."""
    ids = {r["id"] for r in jrun.select_run(cal.load_jsonl(D41), [0])}
    assert {c["id"] for c in cal.load_jsonl(jrun.CASES)} <= ids


def test_judge_filters_repeats_and_resumes_by_id_and_repeat(test_settings, tmp_path):
    cases = tmp_path / "cases.jsonl"
    cases.write_text("".join(json.dumps(_case("g01", r)) + "\n" for r in (0, 1, 2)))
    kwargs = {
        "model": TEST_MODEL, "tag": "t", "cases_path": cases, "repeats": [0], "yes": True,
        "base_settings": test_settings, "reports": tmp_path, "today": "2026-01-01",
        "sleep": lambda s: None,
    }  # fmt: skip
    run_dir, code = jrun.judge(**kwargs, make_llm=lambda s: FakeLLM(json.dumps(_verdict(4))))
    assert code == 0
    rows = cal.load_jsonl(run_dir / "judgments.jsonl")
    assert [(r["id"], r["repeat"]) for r in rows] == [("g01", 0)]
    _, code = jrun.judge(**kwargs, make_llm=lambda s: FakeLLM())  # nothing left: no call
    assert code == 0


def test_summary_covers_trusted_criteria_and_false_disclosures(tmp_path):
    judgments = [
        {"id": "g01", "repeat": 0, "status": "ok", "verdict": _verdict(5)},
        {"id": "g02", "repeat": 0, "status": "ok", "verdict": _verdict(3)},
        {"id": "g03", "repeat": 0, "status": "ok", "verdict": None, "parse_error": "x"},
    ]
    (tmp_path / "judgments.jsonl").write_text("".join(json.dumps(j) + "\n" for j in judgments))
    cases = tmp_path / "cases.jsonl"
    top3 = "A, B and C. Only the first rows are shown."
    cases.write_text(
        json.dumps(_case("g01"))
        + "\n"
        + json.dumps(_case("g16", answer=top3, rows=3, limit_reached=True))
        + "\n"
        + json.dumps(_case("g16", repeat=1, answer="A, B and C.", rows=3, limit_reached=True))
        + "\n"
    )
    summary = jrun.summarize(tmp_path, cases)
    assert set(summary["criteria"]) == set(jrun.TRUSTED)
    assert summary["criteria"]["faithfulness"] == {"mean": 4.0, "pass_rate": 0.5}
    assert (summary["judged"], summary["verdicts"]) == (3, 2)
    assert summary["false_disclosure"] == {"flagged": 1, "of": 3, "items": ["g16/r0"]}
    assert (
        "False partial-result warnings: 1 of 3 answers (g16/r0)"
        in (tmp_path / "summary.md").read_text()
    )


class _CachedLLM:
    """Every reply comes from the cache, as when the calibration verdicts are reused."""

    def complete(self, role, messages, **kwargs):
        from types import SimpleNamespace

        return SimpleNamespace(
            content=json.dumps(_verdict(4)), input_tokens=0, output_tokens=0, cache_hit=True
        )


def test_cache_hits_are_not_paced(test_settings, tmp_path):
    cases = tmp_path / "cases.jsonl"
    cases.write_text("".join(json.dumps(_case(f"g0{i}")) + "\n" for i in range(1, 4)))
    waits = []
    run_dir, code = jrun.judge(
        model=TEST_MODEL, tag="t", cases_path=cases, yes=True, base_settings=test_settings,
        reports=tmp_path, today="2026-01-01", sleep=waits.append, make_llm=lambda s: _CachedLLM(),
    )  # fmt: skip
    assert code == 0 and waits == []
    assert all(r["cached"] for r in cal.load_jsonl(run_dir / "judgments.jsonl"))


def test_real_calls_are_paced(test_settings, tmp_path):
    cases = tmp_path / "cases.jsonl"
    cases.write_text("".join(json.dumps(_case(f"g0{i}")) + "\n" for i in range(1, 4)))
    waits = []
    good = json.dumps(_verdict(4))
    jrun.judge(
        model=TEST_MODEL, tag="t", cases_path=cases, yes=True, base_settings=test_settings,
        reports=tmp_path, today="2026-01-01", sleep=waits.append, min_interval=25.0,
        make_llm=lambda s: FakeLLM(good, good, good),
    )  # fmt: skip
    assert waits == [25.0, 25.0]
