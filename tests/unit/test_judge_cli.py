"""evals/judges/run.py offline: the CLI routing, the replay, and exact case rebuilds.

The rebuild tests re-run the stored SQL of committed runs against the real
database through the production safety layer, and prove that the committed
calibration set (D44) and the D48 case file are reproducible from their
sources (principle 8). No model is called.
"""

from pathlib import Path

import pytest

from app.sql.errors import SqlSafetyError
from evals.judges import calibration as cal
from evals.judges import run as jrun

D41 = jrun.REPORTS / cal.BASELINE_RUN
D48_CASES = jrun.REPORTS / "2026-09-27-judge-d41-qwen3.8-27b" / "cases.jsonl"


@pytest.fixture
def replay_settings(test_settings, monkeypatch):
    """_replay loads settings itself; give it the offline test settings."""
    monkeypatch.setattr("app.config.load_settings", lambda *a, **k: test_settings)
    return test_settings


# --- replay and rebuilds --------------------------------------------------------------------


def test_replay_runs_stored_sql_through_the_safety_layer(replay_settings):
    run_query, ref_rows, items = jrun._replay()
    assert len(run_query("SELECT name FROM customers").rows) == ref_rows(
        "SELECT name FROM customers"
    )
    with pytest.raises(SqlSafetyError):
        run_query("DELETE FROM orders")
    assert {"g04", "a04"} <= set(items)


def test_build_reproduces_the_committed_calibration_set(replay_settings, tmp_path):
    cases_path, sheet = tmp_path / "cases.jsonl", tmp_path / "labels.csv"
    cases = jrun.build(sheet, cases_path=cases_path)
    assert len(cases) == 26
    assert cal.load_jsonl(cases_path) == cal.load_jsonl(jrun.CASES)
    assert sheet.read_text(encoding="utf-8").startswith(",".join(cal.SHEET_COLUMNS))


def test_run_cases_reproduce_the_d48_case_file(replay_settings, tmp_path):
    out = tmp_path / "nested" / "cases.jsonl"
    cases = jrun.build_run_cases(D41, out, [0, 1, 2])
    assert len(cases) == 107
    assert cal.load_jsonl(out) == cal.load_jsonl(D48_CASES)


def test_run_cases_refuse_a_dataset_changed_since_the_run(tmp_path):
    (tmp_path / "manifest.json").write_text('{"datasets": {"golden": "000000000000"}}')
    with pytest.raises(SystemExit, match="golden dataset changed since"):
        jrun.build_run_cases(tmp_path, tmp_path / "out.jsonl", [0])


# --- the command line ------------------------------------------------------------------------


@pytest.fixture
def calls(monkeypatch):
    """Replaces every command's function with a recorder."""
    seen = []

    def recorder(name, result):
        def fake(*args, **kwargs):
            seen.append((name, args, kwargs))
            return result

        return fake

    monkeypatch.setattr(jrun, "build", recorder("build", []))
    monkeypatch.setattr(jrun, "build_run_cases", recorder("cases", []))
    monkeypatch.setattr(jrun, "summarize", recorder("summary", {}))
    monkeypatch.setattr(jrun, "judge", recorder("judge", (None, 3)))
    monkeypatch.setattr(jrun, "agree", recorder("agree", {}))
    return seen


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["build", "--sheet", "s.csv"], ("build", (Path("s.csv"),), {})),
        (
            ["cases", "--run", "r", "--out", "o.jsonl"],
            ("cases", (Path("r"), Path("o.jsonl"), [0, 1, 2]), {}),
        ),
        (
            ["cases", "--run", "r", "--out", "o.jsonl", "--repeats", "0"],
            ("cases", (Path("r"), Path("o.jsonl"), [0]), {}),
        ),
        (["summary", "--run", "r", "--cases", "c"], ("summary", (Path("r"), Path("c")), {})),
        (["agree", "--run", "r", "--labels", "l.csv"], ("agree", (Path("r"), Path("l.csv")), {})),
    ],
    ids=["build", "cases", "cases-repeat-0", "summary", "agree"],
)
def test_each_command_reaches_its_function(calls, argv, expected):
    assert jrun.main(argv) == 0
    assert calls == [expected]


def test_judge_command_passes_its_options_and_returns_the_run_code(calls):
    argv = ["judge", "--model", "m", "--tag", "t", "--repeats", "0", "--min-interval", "5"]
    assert jrun.main([*argv, "--date", "2026-09-27", "--yes"]) == 3
    ((name, args, kwargs),) = calls
    assert (name, args) == ("judge", ())
    assert kwargs == {
        "model": "m",
        "tag": "t",
        "cases_path": jrun.CASES,
        "repeats": [0],
        "min_interval": 5.0,
        "yes": True,
        "today": "2026-09-27",
    }


def test_a_command_is_required(calls):
    with pytest.raises(SystemExit):
        jrun.main([])
    assert calls == []
