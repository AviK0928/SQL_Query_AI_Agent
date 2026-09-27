"""evals/judges/calibration.py and run.py: selection, label sheet, agreement, runs.

All offline: judge runs use FakeLLM, and the baseline run's committed
results.jsonl pins what the selection rule picks.
"""

import csv
import json
from types import SimpleNamespace

import pytest

from app.llm.client import LlmError, LlmErrorCode
from app.llm.registry import LlmRole
from evals.judges import calibration as cal
from evals.judges import run as jrun
from evals.judges.response import CRITERIA, Verdict
from tests.fakes import TEST_MODEL, FakeLLM

# The selection rule applied to the D41 run (repeat 0): two per tier plus every
# deterministic failure (g14, g18, g20, g22, g35).
EXPECTED_IDS = [
    "a04", "g01", "g02", "g04", "g05", "g07", "g08", "g10", "g11", "g13", "g14", "g16", "g17",
    "g18", "g19", "g20", "g22", "g25", "g32", "g33", "g34", "g35", "g36", "g37", "g39", "g40",
]  # fmt: skip

GOOD = json.dumps({c: {"score": 4, "reason": "ok"} for c in CRITERIA})


def _rec(item_id, tier="T1", **kw):
    base = {
        "suite": "golden",
        "id": item_id,
        "tier": tier,
        "kind": "sql",
        "repeat": 0,
        "status": "ok",
        "sql": "SELECT 1",
        "error": None,
        "rows": 1,
        "answer": "one",
    }
    return {**base, **kw}


def _case(item_id="g01"):
    return {
        "id": item_id,
        "repeat": 0,
        "tier": "T1",
        "kind": "sql",
        "earlier_questions": [],
        "question": "How many?",
        "sql": "SELECT COUNT(*) FROM customers",
        "columns": ["n"],
        "rows": [[20]],
        "truncated": False,
        "limit_reached": False,
        "answer": "There are 20.",
        "reference_sql": "SELECT COUNT(*) FROM customers",
        "reference_rows": 1,
        "matches_reference": True,
    }


def _verdict(score):
    return Verdict.model_validate({c: {"score": score, "reason": "r"} for c in CRITERIA})


# --- selection ---------------------------------------------------------------------------


def test_selection_rule_on_the_baseline_run():
    records = cal.load_jsonl(jrun.REPORTS / cal.BASELINE_RUN / "results.jsonl")
    assert [r["id"] for r in cal.select_items(records)] == EXPECTED_IDS


def test_committed_cases_are_the_selected_items():
    cases = cal.load_jsonl(jrun.CASES)
    assert [c["id"] for c in cases] == EXPECTED_IDS
    assert all(c["repeat"] == 0 and c["sql"] and c["answer"] for c in cases)


def test_selection_keeps_two_per_tier_plus_every_failure():
    records = [
        _rec("g01"),
        _rec("g02"),
        _rec("g03"),
        _rec("g04", correct=False),
        _rec("g05", disclosed=False),
        _rec("g06", repeat=1),
        _rec("g07", sql=None),
        _rec("g08", error="EXECUTION_ERROR"),
        _rec("g09", status="error"),
    ]
    assert [r["id"] for r in cal.select_items(records)] == ["g01", "g02", "g04", "g05"]


def test_selection_orders_ids_numerically():
    records = [_rec("g10", tier="A"), _rec("g9", tier="B")]
    assert [r["id"] for r in cal.select_items(records)] == ["g9", "g10"]


# --- building a case ---------------------------------------------------------------------


def _result(rows, truncated=False, limit_reached=False):
    return SimpleNamespace(
        columns=["n"], rows=rows, truncated=truncated, limit_reached=limit_reached
    )


def test_case_carries_rows_flags_turns_and_reference():
    item = {"turns": ["first?", "and then?"], "reference_sql": "SELECT 2"}
    case = cal.build_case(
        _rec("g36", rows=2, correct=False),
        item,
        lambda sql: _result([[1], [2]], limit_reached=True),
        lambda sql: 3,
    )
    assert case["earlier_questions"] == ["first?"] and case["question"] == "and then?"
    assert case["rows"] == [[1], [2]] and case["limit_reached"] is True
    assert (case["reference_rows"], case["matches_reference"]) == (3, False)


def test_case_refuses_a_row_count_that_changed_since_the_run():
    with pytest.raises(ValueError, match="1 rows now, 2 in the run"):
        cal.build_case(_rec("g01", rows=2), {"turns": ["q"]}, lambda s: _result([[1]]), None)


# --- the label sheet ---------------------------------------------------------------------


def _fill(path, scores):
    with path.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for row in rows:
        row.update(scores.get(row["id"], {}))
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=cal.SHEET_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def test_sheet_round_trip(tmp_path):
    sheet = tmp_path / "labels.csv"
    cal.write_label_sheet([_case("g01"), _case("g02")], sheet)
    _fill(sheet, {"g01": dict.fromkeys(CRITERIA, "5"), "g02": dict.fromkeys(CRITERIA, " 2 ")})
    labels = cal.read_labels(sheet, ["g01", "g02"])
    assert labels["g01"] == dict.fromkeys(CRITERIA, 5)
    assert labels["g02"] == dict.fromkeys(CRITERIA, 2)


def test_sheet_shows_the_result_table_and_flags(tmp_path):
    sheet = tmp_path / "labels.csv"
    case = {**_case(), "rows": [[i] for i in range(60)], "truncated": True}
    cal.write_label_sheet([case], sheet)
    (row,) = csv.DictReader(sheet.open(encoding="utf-8"))
    assert row["result"].endswith("(60 rows; 50 shown)")
    assert row["flags"] == "truncated" and row["matches_reference"] == "yes"


@pytest.mark.parametrize(
    ("scores", "message"),
    [
        ({"g01": dict.fromkeys(CRITERIA, "5")}, "g02: not labelled"),
        (
            {
                "g01": dict.fromkeys(CRITERIA, "5"),
                "g02": {**dict.fromkeys(CRITERIA, "5"), "honesty": "6"},
            },
            "g02.honesty: '6' is not 1-5",
        ),
        (
            {
                "g01": dict.fromkeys(CRITERIA, "5"),
                "g02": {**dict.fromkeys(CRITERIA, "5"), "clarity": "4.5"},
            },
            "g02.clarity",
        ),
    ],
    ids=["unlabelled", "out-of-range", "not-an-integer"],
)
def test_sheet_problems_are_all_reported(tmp_path, scores, message):
    sheet = tmp_path / "labels.csv"
    cal.write_label_sheet([_case("g01"), _case("g02")], sheet)
    _fill(sheet, scores)
    with pytest.raises(cal.LabelError, match=message):
        cal.read_labels(sheet, ["g01", "g02"])


def test_sheet_rejects_unknown_and_duplicate_ids(tmp_path):
    sheet = tmp_path / "labels.csv"
    cal.write_label_sheet([_case("g01"), _case("g01"), _case("g99")], sheet)
    _fill(sheet, {i: dict.fromkeys(CRITERIA, "3") for i in ("g01", "g99")})
    with pytest.raises(cal.LabelError) as err:
        cal.read_labels(sheet, ["g01"])
    assert "g01: listed twice" in str(err.value)
    assert "g99: not in the calibration set" in str(err.value)


# --- agreement -----------------------------------------------------------------------------


def test_agreement_counts_within_one_and_pass_fail():
    labels = {f"i{n}": dict.fromkeys(CRITERIA, 4) for n in range(10)}
    verdicts = {f"i{n}": _verdict(4) for n in range(8)}
    verdicts["i8"] = _verdict(3)  # within one, but fail instead of pass
    verdicts["i9"] = None  # a parse failure counts against both measures
    result = cal.agreement(labels, verdicts)["faithfulness"]
    assert (result["within_one"], result["pass_match"], result["trusted"]) == (0.9, 0.8, False)


def test_agreement_threshold_is_inclusive():
    labels = {f"i{n}": dict.fromkeys(CRITERIA, 5) for n in range(20)}
    verdicts = {f"i{n}": _verdict(5) for n in range(17)}  # 17/20 = 0.85
    verdicts.update({f"i{n}": _verdict(2) for n in range(17, 20)})
    result = cal.agreement(labels, verdicts)["clarity"]
    assert result["pass_match"] == 0.85 and result["trusted"] is True


# --- judge runs ------------------------------------------------------------------------------


def _write_cases(tmp_path, ids):
    path = tmp_path / "cases.jsonl"
    path.write_text("".join(json.dumps(_case(i)) + "\n" for i in ids))
    return path


def _judge(test_settings, tmp_path, cases, *script):
    return jrun.judge(
        model=TEST_MODEL,
        tag="calib",
        cases_path=cases,
        yes=True,
        base_settings=test_settings,
        make_llm=lambda s: FakeLLM(*script),
        reports=tmp_path,
        today="2026-01-01",
        sleep=lambda s: None,
    )


def test_judge_run_records_every_case_without_printing_scores(test_settings, tmp_path, capsys):
    cases = _write_cases(tmp_path, ["g01", "g02"])
    run_dir, code = _judge(test_settings, tmp_path, cases, GOOD, "not json")
    rows = cal.load_jsonl(run_dir / "judgments.jsonl")
    assert code == 0 and [r["id"] for r in rows] == ["g01", "g02"]
    assert rows[0]["verdict"]["honesty"]["score"] == 4
    assert rows[1]["verdict"] is None and rows[1]["parse_error"]
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["judge_model"] == TEST_MODEL and manifest["rubric"] == "response.v1"
    assert "score" not in capsys.readouterr().out


def test_judge_run_stops_on_rate_limit_and_resumes(test_settings, tmp_path):
    cases = _write_cases(tmp_path, ["g01", "g02"])
    limited = LlmError(LlmErrorCode.RATE_LIMITED, "429")
    run_dir, code = _judge(test_settings, tmp_path, cases, GOOD, limited)
    assert code == 3
    run_dir, code = _judge(test_settings, tmp_path, cases, GOOD)
    ok = [r["id"] for r in cal.load_jsonl(run_dir / "judgments.jsonl") if r["status"] == "ok"]
    assert code == 0 and ok == ["g01", "g02"]


def test_judge_run_needs_limits_for_the_judge_model(test_settings, tmp_path):
    cases = _write_cases(tmp_path, ["g01"])
    with pytest.raises(SystemExit, match="LLM_LIMITS has no entry"):
        jrun.judge(
            model="other/model",
            tag="t",
            cases_path=cases,
            yes=True,
            base_settings=test_settings,
            reports=tmp_path,
        )


def test_agree_reports_per_criterion(test_settings, tmp_path):
    cases = _write_cases(tmp_path, ["g01", "g02"])
    run_dir, _ = _judge(test_settings, tmp_path, cases, GOOD, GOOD)
    sheet = tmp_path / "labels.csv"
    cal.write_label_sheet(cal.load_jsonl(cases), sheet)
    _fill(sheet, {"g01": dict.fromkeys(CRITERIA, "5"), "g02": dict.fromkeys(CRITERIA, "1")})
    result = jrun.agree(run_dir, sheet, cases_path=cases)
    assert result["criteria"]["relevance"] == {
        "n": 2,
        "within_one": 0.5,
        "pass_match": 0.5,
        "trusted": False,
    }
    assert "| relevance | 50% | 50% | no |" in (run_dir / "agreement.md").read_text()


def test_judge_run_waits_for_confirmation(test_settings, tmp_path):
    cases = _write_cases(tmp_path, ["g01"])
    asked = []
    run_dir, code = jrun.judge(
        model=TEST_MODEL,
        tag="calib",
        cases_path=cases,
        base_settings=test_settings,
        make_llm=lambda s: FakeLLM(),
        reports=tmp_path,
        today="2026-01-01",
        confirm=lambda text: asked.append(text) or "n",
    )
    assert code == 1 and asked == ["Proceed? [y/N] "]
    assert not (run_dir / "manifest.json").exists()
    assert not (run_dir / "judgments.jsonl").exists()


def test_other_provider_errors_are_recorded_and_the_run_goes_on(test_settings, tmp_path):
    """Only a rate limit stops a run; any other failure is one errored case."""
    cases = _write_cases(tmp_path, ["g01", "g02"])
    timeout = LlmError(LlmErrorCode.TIMEOUT, "slow")
    run_dir, code = _judge(test_settings, tmp_path, cases, timeout, GOOD)
    rows = cal.load_jsonl(run_dir / "judgments.jsonl")
    assert code == 0
    assert [(r["id"], r["status"], r.get("error")) for r in rows] == [
        ("g01", "error", "LLM_TIMEOUT"),
        ("g02", "ok", None),
    ]


def test_judge_builds_the_production_stack_with_only_the_judge_changed(
    test_settings, tmp_path, monkeypatch
):
    """One variable: the judge role gets the model under test, with no fallback,
    and the cache and call log live in the run folder."""
    built = []

    def fake_build_llm(settings):
        built.append(settings)
        return FakeLLM(GOOD)

    monkeypatch.setattr("app.agent.build_llm", fake_build_llm)
    cases = _write_cases(tmp_path, ["g01"])
    run_dir, code = jrun.judge(
        model=TEST_MODEL,
        tag="calib",
        cases_path=cases,
        yes=True,
        base_settings=test_settings,
        reports=tmp_path,
        today="2026-01-01",
        sleep=lambda s: None,
    )
    (settings,) = built
    assert code == 0
    assert settings.llm_role_models[LlmRole.JUDGE] == TEST_MODEL
    assert settings.groq_fallback_model is None
    assert settings.llm_cache_path == run_dir / "cache" / "judge.sqlite"
    assert settings.llm_log_path == run_dir / "calls.jsonl"


def test_agree_skips_errored_judgments_and_lists_them_as_missing(tmp_path):
    cases = _write_cases(tmp_path, ["g01", "g02"])
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    judgments = [
        {"id": "g01", "repeat": 0, "status": "ok", "verdict": json.loads(GOOD)},
        {"id": "g02", "repeat": 0, "status": "error", "error": "LLM_TIMEOUT"},
    ]
    (run_dir / "judgments.jsonl").write_text("".join(json.dumps(j) + "\n" for j in judgments))
    sheet = tmp_path / "labels.csv"
    cal.write_label_sheet(cal.load_jsonl(cases), sheet)
    _fill(sheet, {i: dict.fromkeys(CRITERIA, "4") for i in ("g01", "g02")})
    result = jrun.agree(run_dir, sheet, cases_path=cases)
    assert result["missing"] == ["g02"] and result["parse_failures"] == 0
    assert result["criteria"]["clarity"]["within_one"] == 0.5


def test_sheet_missing_a_criterion_column_is_refused(tmp_path):
    sheet = tmp_path / "labels.csv"
    sheet.write_text("id,faithfulness\ng01,5\n", encoding="utf-8")
    with pytest.raises(cal.LabelError, match=r"missing columns: .*relevance"):
        cal.read_labels(sheet, ["g01"])


def test_sheet_rows_without_an_id_are_ignored(tmp_path):
    """Blank rows a spreadsheet leaves at the end are not labels."""
    sheet = tmp_path / "labels.csv"
    cal.write_label_sheet([_case("g01")], sheet)
    _fill(sheet, {"g01": dict.fromkeys(CRITERIA, "4")})
    with sheet.open("a", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerow([" "] + [""] * (len(cal.SHEET_COLUMNS) - 1))
    assert cal.read_labels(sheet, ["g01"]) == {"g01": dict.fromkeys(CRITERIA, 4)}
