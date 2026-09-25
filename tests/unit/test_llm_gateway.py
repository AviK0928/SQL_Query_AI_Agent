"""Unit tests for app/llm/cache.py, app/llm/budget.py and app/llm/gateway.py."""

import threading

import pytest

from app.llm.budget import UsageTracker
from app.llm.cache import CachedCompletion, ResponseCache, cache_key
from app.llm.client import LlmError, LlmErrorCode, LlmResult
from app.llm.gateway import LlmGateway
from app.llm.registry import LlmRole, ModelLimits, ModelRegistry

LIMITS = ModelLimits(rpm=30, rpd=1000, tpm=8000, tpd=200000)
MESSAGES = [{"role": "system", "content": "sys"}, {"role": "user", "content": "q"}]
ROLE = LlmRole.SQL_GENERATOR
BASE_KEY = ("big", "sql_gen.v1", MESSAGES, 0.0, None, "abc123")


def result(model="big", content="SELECT 1", cache_hit=False, tokens=(100, 10)):
    return LlmResult(
        content=content,
        role=ROLE,
        model=model,
        input_tokens=tokens[0],
        output_tokens=tokens[1],
        attempts=1,
        fallback_used=model != "big",
        latency_ms=5,
        cache_hit=cache_hit,
    )


class StubClient:
    """Stands in for LlmClient: a registry and a scripted complete()."""

    def __init__(self, *outcomes):
        self.registry = ModelRegistry(
            default_model="big", fallback_model="small", limits={"big": LIMITS, "small": LIMITS}
        )
        self.outcomes = list(outcomes)
        self.calls = []

    def complete(self, role, messages, *, temperature, max_tokens):
        self.calls.append((role, temperature, max_tokens))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


# --- cache key -------------------------------------------------------------------


@pytest.mark.parametrize(
    "index, changed",
    [
        (0, "small"),
        (1, "sql_gen.v2"),
        (2, [{"role": "system", "content": "sys"}, {"role": "user", "content": "Q"}]),
        (3, 0.2),
        (4, 256),
        (5, "def456"),
    ],
)
def test_every_input_changes_the_key(index, changed):
    variant = list(BASE_KEY)
    variant[index] = changed
    assert cache_key(*variant) != cache_key(*BASE_KEY)


def test_key_is_stable_and_ignores_dict_key_order():
    reordered = [{"content": "sys", "role": "system"}, {"content": "q", "role": "user"}]
    assert cache_key(*BASE_KEY) == cache_key("big", "sql_gen.v1", reordered, 0.0, None, "abc123")
    assert len(cache_key(*BASE_KEY)) == 64


def test_message_order_matters():
    assert cache_key("big", "p", list(reversed(MESSAGES)), 0.0, None, "") != cache_key(
        "big", "p", MESSAGES, 0.0, None, ""
    )


# --- cache storage ------------------------------------------------------------------


def test_cache_round_trip_persists_across_instances(tmp_path):
    path = tmp_path / "nested" / "llm_cache.sqlite"
    ResponseCache(path).put("k", CachedCompletion("SELECT 1", 100, 10))
    reopened = ResponseCache(path)
    assert reopened.get("k") == CachedCompletion("SELECT 1", 100, 10)
    assert reopened.get("missing") is None
    assert len(reopened) == 1


def test_cache_put_replaces_an_existing_entry(tmp_path):
    cache = ResponseCache(tmp_path / "c.sqlite")
    cache.put("k", CachedCompletion("old", 1, 1))
    cache.put("k", CachedCompletion("new", 2, 2))
    assert cache.get("k") == CachedCompletion("new", 2, 2)
    assert len(cache) == 1


def test_cache_file_never_contains_the_prompt(tmp_path):
    path = tmp_path / "c.sqlite"
    secret_question = "customers of ACME-ZX-9"
    messages = [{"role": "user", "content": secret_question}]
    ResponseCache(path).put(
        cache_key("big", "p", messages, 0.0, None, ""), CachedCompletion("x", 1, 1)
    )
    assert secret_question.encode() not in path.read_bytes()


# --- usage tracker --------------------------------------------------------------------


def test_usage_separates_spent_from_saved():
    usage = UsageTracker()
    usage.add(result(tokens=(100, 10)))
    usage.add(result(tokens=(50, 5), cache_hit=True))
    usage.add(result(model="small", tokens=(80, 8)))
    snap = usage.snapshot()
    assert snap["big"] == {
        "calls": 1,
        "cache_hits": 1,
        "input_tokens": 100,
        "output_tokens": 10,
        "tokens_saved": 55,
    }
    assert snap["small"]["calls"] == 1
    assert usage.tokens_spent == 110 + 88
    assert list(snap) == ["big", "small"]


def test_usage_is_thread_safe():
    usage = UsageTracker()
    threads = [
        threading.Thread(target=lambda: [usage.add(result()) for _ in range(200)]) for _ in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert usage.snapshot()["big"]["calls"] == 1600


# --- gateway -------------------------------------------------------------------------


def test_gateway_without_cache_passes_through_and_tracks(tmp_path):
    usage = UsageTracker()
    client = StubClient(result())
    gateway = LlmGateway(client, usage=usage)
    out = gateway.complete(ROLE, MESSAGES, max_tokens=64)
    assert out.content == "SELECT 1" and not out.cache_hit
    assert client.calls == [(ROLE, 0.0, 64)]
    assert usage.snapshot()["big"]["calls"] == 1


def test_second_identical_call_is_served_from_the_cache(tmp_path):
    usage = UsageTracker()
    client = StubClient(result())
    gateway = LlmGateway(client, cache=ResponseCache(tmp_path / "c.sqlite"), usage=usage)
    first = gateway.complete(ROLE, MESSAGES, prompt_id="p1", schema_hash="h")
    second = gateway.complete(ROLE, MESSAGES, prompt_id="p1", schema_hash="h")
    assert len(client.calls) == 1, "the second call must not reach the client"
    assert (second.content, second.cache_hit, second.attempts) == (first.content, True, 0)
    assert usage.snapshot()["big"] == {
        "calls": 1,
        "cache_hits": 1,
        "input_tokens": 100,
        "output_tokens": 10,
        "tokens_saved": 110,
    }


def test_a_different_prompt_id_or_schema_misses(tmp_path):
    client = StubClient(result(), result(), result())
    gateway = LlmGateway(client, cache=ResponseCache(tmp_path / "c.sqlite"))
    gateway.complete(ROLE, MESSAGES, prompt_id="p1", schema_hash="h")
    gateway.complete(ROLE, MESSAGES, prompt_id="p2", schema_hash="h")
    gateway.complete(ROLE, MESSAGES, prompt_id="p1", schema_hash="h2")
    assert len(client.calls) == 3


def test_nonzero_temperature_is_never_cached(tmp_path):
    cache = ResponseCache(tmp_path / "c.sqlite")
    client = StubClient(result(), result())
    gateway = LlmGateway(client, cache=cache)
    gateway.complete(ROLE, MESSAGES, temperature=0.7)
    gateway.complete(ROLE, MESSAGES, temperature=0.7)
    assert len(client.calls) == 2
    assert len(cache) == 0


def test_a_fallback_answer_is_stored_under_the_fallback_model(tmp_path):
    cache = ResponseCache(tmp_path / "c.sqlite")
    client = StubClient(result(model="small"), result())
    gateway = LlmGateway(client, cache=cache)
    gateway.complete(ROLE, MESSAGES)
    assert cache.get(cache_key("small", "unversioned", MESSAGES, 0.0, None, "")) is not None
    gateway.complete(ROLE, MESSAGES)  # primary key still misses, so the client runs again
    assert len(client.calls) == 2


def test_errors_propagate_and_nothing_is_cached_or_counted(tmp_path):
    cache, usage = ResponseCache(tmp_path / "c.sqlite"), UsageTracker()
    client = StubClient(LlmError(LlmErrorCode.RATE_LIMITED, "rate limited on big, small"))
    gateway = LlmGateway(client, cache=cache, usage=usage)
    with pytest.raises(LlmError):
        gateway.complete(ROLE, MESSAGES)
    assert len(cache) == 0
    assert usage.snapshot() == {}


def test_llm_result_defaults_to_not_a_cache_hit():
    plain = LlmResult(
        content="x",
        role=ROLE,
        model="big",
        input_tokens=1,
        output_tokens=1,
        attempts=1,
        fallback_used=False,
        latency_ms=1,
    )
    assert plain.cache_hit is False
