"""Every eval call records the schema it ran against (principle 8, D54, resolves L22).

Before D54 the role suites (repair, synthesizer) and the judge called the gateway
without a schema hash: 307 of 963 logged calls in the committed runs had it empty.
The agent suites already sent it through the Agent; they are checked here too, so
one test covers every path an eval call can take.
"""

import json

import pytest

from app.sql.schema import Database
from evals import runner
from evals.judges import run as jrun
from evals.judges.response import CRITERIA
from tests.fakes import TEST_MODEL, FakeLLM

SUITES = {  # suite -> (role under test, one item, a script that answers it)
    "golden": ("sql_generator", "g04", ["SELECT COUNT(*) FROM customers", "There are 20."]),
    "repair": ("sql_repair", "r01", ["SELECT COUNT(*) FROM customers"]),
    "synthesizer": ("synthesizer", "s02", ["There are 20 customers."]),
}
VERDICT = json.dumps({c: {"score": 4, "reason": "ok"} for c in CRITERIA})


@pytest.mark.parametrize("suite", list(SUITES))
def test_every_eval_call_and_the_manifest_carry_the_schema_hash(test_settings, tmp_path, suite):
    expected = Database.from_settings(test_settings).schema_hash()
    role, item, script = SUITES[suite]
    fakes = []

    def make_llm(settings):
        fakes.append(FakeLLM(*script))
        return fakes[-1]

    run_dir, code = runner.run(
        role=role,
        model=TEST_MODEL,
        suites=[suite],
        ids=[item],
        repeats=1,
        tag="t",
        base_settings=test_settings,
        make_llm=make_llm,
        reports=tmp_path,
        today="2026-01-01",
        sleep=lambda s: None,
        yes=True,
    )
    (fake,) = fakes
    assert code == 0 and fake.call_count == len(script)
    assert [k["schema_hash"] for k in fake.kwargs] == [expected] * len(script)
    assert json.loads((run_dir / "manifest.json").read_text())["schema_hash"] == expected


def test_every_judge_call_and_the_manifest_carry_the_schema_hash(test_settings, tmp_path):
    expected = Database.from_settings(test_settings).schema_hash()
    case = {
        "id": "g01",
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
        "reference_sql": None,
        "reference_rows": None,
        "matches_reference": None,
    }
    cases = tmp_path / "cases.jsonl"
    cases.write_text("".join(json.dumps({**case, "id": i}) + "\n" for i in ("g01", "g02")))
    fake = FakeLLM(VERDICT, VERDICT)
    run_dir, code = jrun.judge(
        model=TEST_MODEL,
        tag="t",
        cases_path=cases,
        yes=True,
        base_settings=test_settings,
        make_llm=lambda s: fake,
        reports=tmp_path,
        today="2026-01-01",
        sleep=lambda s: None,
    )
    assert code == 0
    assert [k["schema_hash"] for k in fake.kwargs] == [expected, expected]
    assert json.loads((run_dir / "manifest.json").read_text())["schema_hash"] == expected
