"""Integration: the agent over the real LLM stack, with only the transport faked.

tests/test_agent.py injects a FakeLLM at the gateway interface, and
tests/unit/test_llm_client.py drives the client without the graph. These tests
join the two: Agent -> LlmGateway (response cache, call log) -> LlmClient
(retries, retry-after, fallback) -> a scripted transport standing in for Groq.
Waits are recorded, never slept. The client-side rate limiter is left out: it
would pace these calls with real time, and it has its own tests (T9).
"""

import json

import pytest

from app.agent import LLM_ERROR_REPLIES, Agent
from app.config import load_settings
from app.llm.cache import ResponseCache
from app.llm.calllog import CallLogger
from app.llm.client import (
    CallTimeoutError,
    LlmClient,
    LlmErrorCode,
    ModelGoneError,
    RateLimitedError,
    RawCompletion,
)
from app.llm.gateway import LlmGateway
from app.llm.registry import ModelRegistry
from app.sql.executor import ReadOnlyExecutor
from tests.fakes import TEST_LLM_LIMITS, TEST_MODEL

FALLBACK = "fake/fallback-model"
# A count, not a LIMIT query: a reached LIMIT would add a disclosure note (check_answer),
# which these tests are not about.
SQL = RawCompletion("SELECT COUNT(*) FROM customers", input_tokens=300, output_tokens=12)
ANSWER = RawCompletion("There are 20 customers.", input_tokens=200, output_tokens=8)


class ScriptedTransport:
    """Pops scripted outcomes per model: a RawCompletion is returned, an exception raised."""

    def __init__(self, script):
        self.script = {model: list(outcomes) for model, outcomes in script.items()}
        self.calls = []

    def __call__(self, model, messages, temperature, max_tokens):
        self.calls.append(model)
        outcome = self.script[model].pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def stack(tmp_path, monkeypatch):
    """Builds an Agent on the production gateway and client around a scripted transport."""
    queries = []
    real_execute = ReadOnlyExecutor.execute

    def spy(self, query):
        queries.append(query.sql)
        return real_execute(self, query)

    monkeypatch.setattr(ReadOnlyExecutor, "execute", spy)

    def build(script):
        settings = load_settings(
            env_file=None,
            groq_api_key="test-key-not-real",
            groq_model=TEST_MODEL,
            groq_fallback_model=FALLBACK,
            llm_limits={**TEST_LLM_LIMITS, FALLBACK: TEST_LLM_LIMITS[TEST_MODEL]},
            llm_cache_path=tmp_path / "cache.sqlite",
            llm_log_path=tmp_path / "calls.jsonl",
        )
        transport, sleeps = ScriptedTransport(script), []
        client = LlmClient(
            ModelRegistry.from_settings(settings),
            transport,
            max_attempts=settings.llm_max_attempts,
            max_wait_s=settings.llm_max_wait_s,
            sleep=sleeps.append,
        )
        gateway = LlmGateway(
            client,
            cache=ResponseCache(settings.llm_cache_path),
            call_log=CallLogger.from_settings(settings),
        )
        return Agent(settings, llm=gateway), transport, sleeps

    def log():
        path = tmp_path / "calls.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    return build, queries, log


def test_a_429_with_retry_after_is_waited_out_and_answered(stack):
    build, queries, log = stack
    agent, transport, sleeps = build({TEST_MODEL: [RateLimitedError(2.0), SQL, ANSWER]})
    result = agent.ask("How many customers are there?")
    assert (result["error"], result["answer"]) == (None, "There are 20 customers.")
    assert sleeps == [2.0], "retry-after is honoured exactly"
    assert transport.calls == [TEST_MODEL] * 3
    assert result["usage"]["calls"] == 2 and len(queries) == 1
    first = log()[0]
    assert (first["llm.attempts"], first["llm.fallback_used"]) == (2, False)


def test_a_retired_model_falls_back_and_the_call_log_says_so(stack):
    build, _, log = stack
    gone = ModelGoneError("model decommissioned")
    agent, transport, sleeps = build({TEST_MODEL: [gone, gone], FALLBACK: [SQL, ANSWER]})
    result = agent.ask("How many customers are there?")
    assert (result["error"], result["answer"]) == (None, "There are 20 customers.")
    assert transport.calls == [TEST_MODEL, FALLBACK, TEST_MODEL, FALLBACK]
    assert sleeps == [], "a retired model is not retried"
    records = log()
    assert [r["gen_ai.response.model"] for r in records] == [FALLBACK, FALLBACK]
    assert all(r["llm.fallback_used"] for r in records)


def test_rate_limits_on_every_model_become_a_coded_answer_and_no_query_runs(stack):
    build, queries, log = stack
    busy = [RateLimitedError(None)] * 3
    agent, transport, _ = build({TEST_MODEL: list(busy), FALLBACK: list(busy)})
    result = agent.ask("How many customers are there?")
    assert result["error"] == LlmErrorCode.RATE_LIMITED.value
    assert result["answer"] == LLM_ERROR_REPLIES[LlmErrorCode.RATE_LIMITED]
    assert (result["sql"], result["rows"], queries) == (None, [], [])
    assert transport.calls == [TEST_MODEL] * 3 + [FALLBACK] * 3
    assert [r["error.type"] for r in log()] == ["LLM_RATE_LIMITED"]


def test_a_persistent_timeout_is_coded_and_does_not_fall_back(stack):
    build, queries, _ = stack
    agent, transport, _ = build({TEST_MODEL: [CallTimeoutError("slow")] * 3, FALLBACK: []})
    result = agent.ask("How many customers are there?")
    assert result["error"] == LlmErrorCode.TIMEOUT.value
    assert result["answer"] == LLM_ERROR_REPLIES[LlmErrorCode.TIMEOUT]
    assert transport.calls == [TEST_MODEL] * 3 and queries == []


def test_asking_the_same_question_again_spends_no_quota(stack):
    build, queries, log = stack
    agent, transport, _ = build({TEST_MODEL: [SQL, ANSWER]})
    first = agent.ask("How many customers are there?")
    second = agent.ask("How many customers are there?")
    assert second["answer"] == first["answer"] and second["rows"] == first["rows"]
    assert transport.calls == [TEST_MODEL] * 2, "the second ask never reached the transport"
    assert second["usage"] == {"calls": 0, "cache_hits": 2, "input_tokens": 0, "output_tokens": 0}
    assert len(queries) == 2, "the database still answers: only model calls are cached"
    assert [r["llm.cache_hit"] for r in log()] == [False, False, True, True]
