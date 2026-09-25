"""Unit tests for app/llm/limiter.py and its use by the client.

A fake clock whose sleep advances time makes every wait exact and instant.
Limits: rpm 30, tpm 8000 at a 0.8 margin -> 24 requests and 6400 tokens per
minute, refilled at 0.4 requests and ~106.7 tokens per second.
"""

from datetime import date

import pytest

from app.llm.client import (
    LlmClient,
    RateLimitedError,
    RawCompletion,
    RequestError,
    build_groq_transport,
    estimate_tokens,
)
from app.llm.limiter import RateLimiter
from app.llm.registry import LlmRole, ModelLimits, ModelRegistry

LIMITS = ModelLimits(rpm=30, rpd=1000, tpm=8000, tpd=200000)
TOKENS_PER_S = 6400 / 60


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.sleeps = []
        self.day = date(2026, 9, 25)

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def make(limits=None, *, max_wait_s=20.0, fallback="small"):
    limits = limits or {"big": LIMITS, "small": LIMITS}
    registry = ModelRegistry(default_model="big", fallback_model=fallback, limits=limits)
    clock = FakeClock()
    limiter = RateLimiter(
        registry,
        margin=0.8,
        max_wait_s=max_wait_s,
        clock=clock,
        sleep=clock.sleep,
        today=lambda: clock.day,
        seconds_to_midnight=lambda: 3600.0,
    )
    return limiter, clock, registry


# --- request and token buckets ------------------------------------------------


def test_requests_up_to_the_scaled_rpm_do_not_wait():
    limiter, clock, _ = make()
    for _ in range(24):
        assert limiter.acquire("big", 10) == 0
    assert clock.sleeps == []


def test_the_request_after_the_bucket_is_empty_waits_one_refill():
    limiter, clock, _ = make()
    for _ in range(24):
        limiter.acquire("big", 10)
    waited = limiter.acquire("big", 10)
    assert waited == pytest.approx(60 / 24)
    assert clock.sleeps == [pytest.approx(2.5)]


def test_tokens_wait_for_exactly_the_deficit():
    limiter, clock, _ = make()
    limiter.acquire("big", 4000)
    waited = limiter.acquire("big", 4000)  # 2400 left, 1600 short
    assert waited == pytest.approx(1600 / TOKENS_PER_S)


def test_a_wait_longer_than_the_maximum_raises_locally_without_reserving():
    limiter, clock, _ = make(max_wait_s=5.0)
    limiter.acquire("big", 6000)
    with pytest.raises(RateLimitedError) as info:
        limiter.acquire("big", 6000)  # needs ~52 s
    assert info.value.retry_after == pytest.approx(5600 / TOKENS_PER_S)
    assert clock.sleeps == []
    clock.now += 60
    assert limiter.acquire("big", 6000) == 0, "the rejected call must not have reserved anything"


def test_a_request_larger_than_the_minute_budget_can_never_fit():
    limiter, _, _ = make()
    with pytest.raises(RequestError, match="exceed the per-minute budget of 6400"):
        limiter.acquire("big", 7000)


def test_models_have_independent_buckets():
    limiter, clock, _ = make()
    for _ in range(24):
        limiter.acquire("big", 10)
    assert limiter.acquire("small", 10) == 0


def test_unknown_model_is_a_request_error():
    limiter, _, _ = make()
    with pytest.raises(RequestError, match="no rate limits configured"):
        limiter.acquire("other", 10)


# --- daily budgets ---------------------------------------------------------------


def test_daily_request_budget_is_enforced_and_resets_at_midnight():
    tiny = ModelLimits(rpm=30, rpd=2, tpm=8000, tpd=200000)  # rpd 2 * 0.8 -> 1 per day
    limiter, clock, _ = make({"big": tiny}, fallback=None)
    limiter.acquire("big", 10)
    with pytest.raises(RateLimitedError) as info:
        limiter.acquire("big", 10)
    assert info.value.retry_after == 3600.0
    clock.day = date(2026, 9, 26)
    assert limiter.acquire("big", 10) == 0


def test_daily_token_budget_is_enforced():
    tiny = ModelLimits(rpm=30, rpd=1000, tpm=8000, tpd=1000)  # 800 tokens per day
    limiter, _, _ = make({"big": tiny}, fallback=None)
    limiter.acquire("big", 700)
    with pytest.raises(RateLimitedError):
        limiter.acquire("big", 200)


# --- feedback: actual usage, Groq headers, 429 -----------------------------------


def test_unused_estimate_is_refunded():
    limiter, _, _ = make()
    limiter.acquire("big", 4000)
    limiter.record("big", 4000, 1000, {})
    assert limiter.acquire("big", 4000) == 0, "3000 tokens were refunded"


def test_remaining_tokens_header_caps_the_bucket():
    limiter, _, _ = make()
    limiter.acquire("big", 100)
    # Groq says only 2000 tokens remain; 1600 of those are our margin's reserve.
    limiter.record("big", 100, 100, {"x-ratelimit-remaining-tokens": "2000"})
    assert limiter.acquire("big", 400) == 0
    assert limiter.acquire("big", 100) > 0


def test_remaining_requests_header_raises_the_daily_count():
    tiny = ModelLimits(rpm=30, rpd=10, tpm=8000, tpd=200000)  # 8 per day at the margin
    limiter, _, _ = make({"big": tiny}, fallback=None)
    limiter.acquire("big", 10)
    headers = {"x-ratelimit-limit-requests": "10", "x-ratelimit-remaining-requests": "2"}
    limiter.record("big", 10, 10, headers)  # Groq has seen 8 today
    with pytest.raises(RateLimitedError):
        limiter.acquire("big", 10)


def test_malformed_headers_are_ignored():
    limiter, _, _ = make()
    limiter.acquire("big", 100)
    limiter.record("big", 100, 100, {"x-ratelimit-remaining-tokens": "lots"})
    assert limiter.acquire("big", 100) == 0


def test_a_429_blocks_the_model_for_every_caller():
    limiter, clock, _ = make()
    limiter.on_rate_limited("big", 3.0)
    assert limiter.acquire("big", 10) == pytest.approx(3.0)
    limiter.on_rate_limited("big", None)  # no retry-after: token bucket drained instead
    assert limiter.acquire("big", 10) > 0


# --- integration with the client ---------------------------------------------------


def test_client_falls_back_when_the_limiter_says_the_wait_is_too_long():
    limiter, clock, registry = make(max_wait_s=5.0)
    limiter.acquire("big", 6000)  # primary saturated locally
    calls = []

    def transport(model, messages, temperature, max_tokens):
        calls.append(model)
        return RawCompletion(content="SELECT 1", input_tokens=50, output_tokens=5)

    client = LlmClient(
        registry, transport, max_attempts=3, max_wait_s=5.0, limiter=limiter, sleep=clock.sleep
    )
    messages = [{"role": "user", "content": "x" * 400}]  # ~100 tokens + 5900 completion budget
    result = client.complete(LlmRole.SQL_GENERATOR, messages, max_tokens=5900)
    assert calls == ["small"], "the saturated primary was never called over the network"
    assert result.fallback_used


def test_client_reports_a_server_429_to_the_limiter():
    limiter, clock, registry = make()
    outcomes = [
        RateLimitedError(2.0),
        RawCompletion(content="ok", input_tokens=10, output_tokens=1),
    ]

    def transport(model, messages, temperature, max_tokens):
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    client = LlmClient(
        registry, transport, max_attempts=3, max_wait_s=20.0, limiter=limiter, sleep=clock.sleep
    )
    assert (
        client.complete(LlmRole.SQL_GENERATOR, [{"role": "user", "content": "q"}]).content == "ok"
    )
    assert clock.sleeps.count(pytest.approx(2.0)) >= 1


def test_estimate_is_characters_over_four_plus_the_completion_budget():
    messages = [{"role": "system", "content": "a" * 400}, {"role": "user", "content": "b" * 40}]
    assert estimate_tokens(messages, 100) == 110 + 100
    assert estimate_tokens(messages, None) == 110 + 512


def test_building_the_real_transport_makes_no_network_call():
    assert callable(build_groq_transport("test-key-not-real", 5.0))


def test_default_day_functions_use_utc():
    from app.llm.limiter import _seconds_to_utc_midnight, _utc_today

    assert isinstance(_utc_today(), date)
    assert 0 < _seconds_to_utc_midnight() <= 86400
