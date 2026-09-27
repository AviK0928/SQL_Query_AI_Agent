"""Agent(prompt_versions=...): eval-only prompt overrides inside the graph (D51).

Production never passes prompt_versions; these tests pin that an override
replaces exactly one system prompt and its logged id, and that the default
path is untouched.
"""

import pytest

from app.agent import Agent
from app.prompts import RETRY_PROMPT_ID, SQL_PROMPT_ID
from app.prompts.variants import system_prompt
from tests.fakes import FakeLLM

# A failing first query, a repair, and the answer: all three model calls.
SCRIPT = ("SELECT revenue FROM customers", "SELECT COUNT(*) FROM customers", "There are 20.")


def test_override_replaces_only_its_own_system_prompt(test_settings):
    default, override = FakeLLM(*SCRIPT), FakeLLM(*SCRIPT)
    Agent(test_settings, llm=default).ask("How many?")
    result = Agent(test_settings, llm=override, prompt_versions={"sql_repair": 1}).ask("How many?")
    assert result["error"] is None and result["rows"] == [[20]]
    v1_text, v1_id = system_prompt("sql_repair", 1)
    assert override.calls[1][0] == {"role": "system", "content": v1_text}
    assert override.kwargs[1]["prompt_id"] == v1_id != RETRY_PROMPT_ID
    # Everything else is what production sends: the other calls, and the repair's turns.
    assert override.calls[1][1:] == default.calls[1][1:]
    assert [override.calls[i] for i in (0, 2)] == [default.calls[i] for i in (0, 2)]
    assert override.kwargs[0]["prompt_id"] == SQL_PROMPT_ID


def test_no_override_sends_the_active_prompts(test_settings):
    llm = FakeLLM(*SCRIPT)
    Agent(test_settings, llm=llm).ask("How many?")
    assert [k["prompt_id"] for k in llm.kwargs][:2] == [SQL_PROMPT_ID, RETRY_PROMPT_ID]


def test_unknown_prompt_name_fails_at_construction(test_settings):
    with pytest.raises(ValueError, match="no variable prompt"):
        Agent(test_settings, llm=FakeLLM(), prompt_versions={"schema": 1})


def test_unreleased_version_fails_at_construction(test_settings):
    with pytest.raises(FileNotFoundError):
        Agent(test_settings, llm=FakeLLM(), prompt_versions={"sql_gen": 99})
