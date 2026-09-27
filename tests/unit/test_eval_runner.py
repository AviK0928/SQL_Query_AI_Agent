"""Offline tests for evals/runner.py: the plumbing, not the model.

A scripted fake stands in for Groq, so checkpointing, resuming, stopping on a
rate limit and report writing are proven without spending quota. The live runs
(step 6.3 onwards) measure the models.
"""

import json

import pytest

from app.llm.client import LlmError, LlmErrorCode
from app.llm.registry import LlmRole
from evals import runner
from tests.fakes import TEST_MODEL, FakeLLM, FakeResponse

NO_WAIT = {"sleep": lambda s: None, "yes": True, "today": "2026-01-01"}


def run(test_settings, tmp_path, *, suites, ids, script, repeats=1, tag="t"):
    return runner.run(
        role={"golden": "sql_generator", "repair": "sql_repair", "synthesizer": "synthesizer"}[
            suites[0]
        ],
        model=TEST_MODEL,
        suites=suites,
        ids=ids,
        repeats=repeats,
        tag=tag,
        base_settings=test_settings,
        make_llm=lambda s: FakeLLM(*script),
        reports=tmp_path,
        **NO_WAIT,
    )


def records(run_dir):
    return [json.loads(line) for line in (run_dir / "results.jsonl").read_text().splitlines()]


def test_golden_item_is_answered_graded_and_reported(test_settings, tmp_path):
    run_dir, code = run(
        test_settings,
        tmp_path,
        suites=["golden"],
        ids=["g04"],
        script=["SELECT COUNT(*) FROM customers", "There are 20 customers."],
    )
    assert code == 0
    (rec,) = records(run_dir)
    assert (rec["status"], rec["correct"], rec["behaviour_ok"], rec["false_refusal"]) == (
        "ok",
        True,
        True,
        False,
    )
    assert rec["format_valid"] == [True]
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["role"] == "sql_generator" and manifest["model"] == TEST_MODEL
    assert "golden" in manifest["datasets"]
    report = (run_dir / "report.md").read_text()
    assert "# Eval report: sql_generator" in report and "**Total:" in report


def test_repeats_are_recorded_separately_and_a_rerun_resumes(test_settings, tmp_path):
    script = ["SELECT COUNT(*) FROM customers", "There are 20 customers."]
    run_dir, _ = run(
        test_settings, tmp_path, suites=["golden"], ids=["g04"], script=script, repeats=2
    )
    assert sorted(r["repeat"] for r in records(run_dir)) == [0, 1]
    again, code = run(test_settings, tmp_path, suites=["golden"], ids=["g04"], script=[], repeats=2)
    assert again == run_dir and code == 0
    assert len(records(run_dir)) == 2, "nothing re-run: every item and repeat was already done"


def test_a_rate_limit_stops_the_run_and_is_not_graded(test_settings, tmp_path):
    busy = LlmError(LlmErrorCode.RATE_LIMITED, "rate limited on fake/test-model")
    run_dir, code = run(
        test_settings, tmp_path, suites=["golden"], ids=["g01", "g04"], script=[busy]
    )
    assert code == 3
    (rec,) = records(run_dir)
    assert (rec["status"], rec["error"]) == ("error", "LLM_RATE_LIMITED")


def test_repair_item_is_executed_and_compared(test_settings, tmp_path):
    fixed = (
        "SELECT SUM(i.unit_price * i.quantity) FROM order_items i "
        "JOIN orders o ON o.id = i.order_id WHERE o.status != 'cancelled'"
    )
    run_dir, _ = run(test_settings, tmp_path, suites=["repair"], ids=["r01"], script=[fixed])
    (rec,) = records(run_dir)
    assert (rec["status"], rec["correct"], rec["format_valid"]) == ("ok", True, [True])


def test_a_repair_that_still_fails_is_graded_wrong(test_settings, tmp_path):
    run_dir, _ = run(
        test_settings,
        tmp_path,
        suites=["repair"],
        ids=["r01"],
        script=["SELECT SUM(revenue) FROM orders"],
    )
    assert records(run_dir)[0]["correct"] is False


def test_synthesizer_item_is_graded_on_the_text(test_settings, tmp_path):
    run_dir, _ = run(
        test_settings,
        tmp_path,
        suites=["synthesizer"],
        ids=["s01"],
        script=["Total revenue is Rs 1,83,530."],
    )
    (rec,) = records(run_dir)
    assert (rec["faithful"], rec["currency"], rec["concise"]) == (True, True, True)


def test_report_only_rebuilds_without_calls(test_settings, tmp_path):
    run_dir, _ = run(
        test_settings,
        tmp_path,
        suites=["synthesizer"],
        ids=["s02"],
        script=["There are 20 customers."],
    )
    (run_dir / "report.md").unlink()
    assert (
        runner.main(
            [
                "--role",
                "synthesizer",
                "--model",
                TEST_MODEL,
                "--tag",
                "x",
                "--report-only",
                str(run_dir),
            ]
        )
        == 0
    )
    assert (run_dir / "report.md").exists()


class ByRole:
    """Answers any number of calls: prose for the synthesizer, SQL for the SQL roles."""

    def complete(self, role, messages, **kwargs):
        if role == LlmRole.SYNTHESIZER:
            return FakeResponse("There are 20 customers.")
        return FakeResponse("SELECT COUNT(*) FROM customers")


@pytest.mark.parametrize(
    ("role", "suite", "ids"),
    [("sql_generator", "golden", ["g01", "g04"]), ("synthesizer", "synthesizer", ["s01", "s02"])],
    ids=["agent-suites", "role-suites"],
)
def test_one_llm_stack_per_repeat_serves_all_its_items(test_settings, tmp_path, role, suite, ids):
    """One cache per repeat (D36): the stack is built once per repeat, not per item."""
    built = []

    def make_llm(settings):
        built.append(settings.llm_cache_path.name)
        return ByRole()

    run_dir, code = runner.run(
        role=role,
        model=TEST_MODEL,
        suites=[suite],
        ids=ids,
        repeats=1,
        tag="t",
        base_settings=test_settings,
        make_llm=make_llm,
        reports=tmp_path,
        **NO_WAIT,
    )
    assert code == 0 and built == ["repeat0.sqlite"]
    assert [(r["id"], r["status"]) for r in records(run_dir)] == [(i, "ok") for i in ids]


def test_other_provider_errors_are_recorded_and_the_run_goes_on(test_settings, tmp_path):
    """Only a rate limit stops a run. An errored item is run again on resume."""
    timeout = LlmError(LlmErrorCode.TIMEOUT, "slow")
    ids = ["s01", "s02"]
    script = [timeout, "There are 20 customers."]
    run_dir, code = run(test_settings, tmp_path, suites=["synthesizer"], ids=ids, script=script)
    assert code == 0
    assert [(r["id"], r["status"], r.get("error")) for r in records(run_dir)] == [
        ("s01", "error", "LLM_TIMEOUT"),
        ("s02", "ok", None),
    ]
    script = ["Total revenue is Rs 1,83,530."]
    again, code = run(test_settings, tmp_path, suites=["synthesizer"], ids=ids, script=script)
    assert again == run_dir and code == 0
    assert [(r["id"], r["status"]) for r in records(run_dir)][-1] == ("s01", "ok")


def test_nothing_runs_without_confirmation(test_settings, tmp_path):
    asked = []
    run_dir, code = runner.run(
        role="synthesizer",
        model=TEST_MODEL,
        suites=["synthesizer"],
        ids=["s02"],
        repeats=1,
        tag="t",
        base_settings=test_settings,
        make_llm=lambda s: FakeLLM(),
        reports=tmp_path,
        today="2026-01-01",
        sleep=lambda s: None,
        confirm=lambda text: asked.append(text) or "n",
    )
    assert code == 1 and asked == ["Proceed? [y/N] "]
    assert not (run_dir / "manifest.json").exists()
    assert not (run_dir / "results.jsonl").exists()


def test_rescore_writes_a_d52_copy_without_calls(test_settings, tmp_path, capsys):
    script = ["There are 20 customers."]
    run_dir, _ = run(test_settings, tmp_path, suites=["synthesizer"], ids=["s02"], script=script)
    capsys.readouterr()
    assert runner.main(["--rescore", str(run_dir)]) == 0
    copy = tmp_path / f"{run_dir.name}-d52"
    assert records(copy) == records(run_dir), "no clarify item, so nothing changes"
    assert json.loads((copy / "manifest.json").read_text())["rescored_from"] == run_dir.name
    assert "# Eval report: synthesizer" in capsys.readouterr().out
