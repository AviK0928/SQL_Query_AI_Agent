"""app/prompts/variants.py and the runner's --prompt override (Phase 7 experiments).

An experiment measures a released but inactive prompt version; these tests pin
that the override renders exactly as production would, reaches only the suites
that build their own messages, and is recorded in the run's manifest.
"""

import json

import pytest

from app import prompts
from app.prompts.variants import system_prompt
from evals import runner
from tests.fakes import TEST_MODEL, FakeLLM

REPAIR_V2_ID = "sql_repair@cbe7c9f7"  # experiment 1 (L14)


@pytest.mark.parametrize(
    ("name", "text", "pid"),
    [
        ("sql_gen", prompts.SQL_SYSTEM_PROMPT, prompts.SQL_PROMPT_ID),
        ("sql_repair", prompts.RETRY_SYSTEM_PROMPT, prompts.RETRY_PROMPT_ID),
        ("answer", prompts.ANSWER_SYSTEM_PROMPT, prompts.ANSWER_PROMPT_ID),
    ],
)
def test_active_versions_render_exactly_as_production(name, text, pid):
    assert system_prompt(name, prompts.ACTIVE_VERSIONS[name]) == (text, pid)


def test_repair_v2_adds_the_domain_rules_and_nothing_else():
    v1, _ = system_prompt("sql_repair", 1)
    v2, pid = system_prompt("sql_repair", 2)
    assert pid == REPAIR_V2_ID
    assert v2.startswith(v1 + "\n\nRULES\n")
    assert "unit_price" in v2 and "'cancelled'" in v2 and "counting or listing orders" in v2


def test_unknown_prompt_is_rejected():
    with pytest.raises(ValueError, match="no variable prompt"):
        system_prompt("schema", 1)


def test_override_parsing():
    assert runner.parse_prompt_overrides(["sql_repair=2", "answer=1"]) == {
        "sql_repair": 2,
        "answer": 1,
    }
    assert runner.parse_prompt_overrides(None) == {}


@pytest.mark.parametrize("value", ["sql_repair", "sql_repair=v2", "sql_repair=0"])
def test_override_parsing_rejects_bad_values(value):
    with pytest.raises(ValueError, match="NAME=VERSION"):
        runner.parse_prompt_overrides([value])


def _run(test_settings, tmp_path, suites, ids, llm, versions):
    return runner.run(
        role={"repair": "sql_repair", "golden": "sql_generator"}[suites[0]],
        model=TEST_MODEL,
        suites=suites,
        ids=ids,
        repeats=1,
        tag="t",
        base_settings=test_settings,
        make_llm=lambda s: llm,
        reports=tmp_path,
        sleep=lambda s: None,
        yes=True,
        today="2026-01-01",
        prompt_versions=versions,
    )


def test_repair_run_sends_and_records_the_override(test_settings, tmp_path):
    llm = FakeLLM("SELECT name FROM customers WHERE city = 'Pune'")
    run_dir, code = _run(test_settings, tmp_path, ["repair"], ["r02"], llm, {"sql_repair": 2})
    assert code == 0
    assert llm.calls[0][0] == {"role": "system", "content": system_prompt("sql_repair", 2)[0]}
    assert llm.kwargs[0]["prompt_id"] == REPAIR_V2_ID
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["prompt_ids"]["sql_repair"] == REPAIR_V2_ID
    assert manifest["prompt_overrides"] == {"sql_repair": 2}
    assert manifest["prompt_ids"]["sql_gen"] == prompts.SQL_PROMPT_ID


def test_agent_suites_accept_overrides_and_record_them(test_settings, tmp_path):
    """Since D51 the agent suites pass overrides to the Agent (previously refused)."""
    llm = FakeLLM("SELECT COUNT(*) FROM customers", "There are 20 customers.")
    run_dir, code = _run(test_settings, tmp_path, ["golden"], ["g04"], llm, {"sql_gen": 1})
    assert code == 0
    assert llm.calls[0][0]["content"] == system_prompt("sql_gen", 1)[0]
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["prompt_overrides"] == {"sql_gen": 1}


def test_role_suites_refuse_prompts_they_never_send(test_settings, tmp_path):
    with pytest.raises(SystemExit, match="do not apply"):
        _run(test_settings, tmp_path, ["repair"], ["r02"], FakeLLM(), {"sql_gen": 1})
