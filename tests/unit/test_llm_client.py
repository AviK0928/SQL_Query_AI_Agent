"""Unit tests for app/llm/client.py.

A scripted fake transport stands in for Groq and a recorder stands in for
time.sleep, so every retry, wait and fallback path runs instantly and offline.
"""

import logging

import groq
import httpx
import pytest

from app.llm.client import (
    CallTimeoutError,
    LlmClient,
    LlmError,
    LlmErrorCode,
    ModelGoneError,
    RateLimitedError,
    RawCompletion,
    RequestError,
    ServerError,
    groq_transport,
    map_sdk_error,
    parse_retry_after,
)
from app.llm.registry import LlmRole, ModelLimits, ModelRegistry

LIMITS = ModelLimits(rpm=30, rpd=1000, tpm=8000, tpd=200000)
MESSAGES = [{"role": "user", "content": "How many customers?"}]
OK = RawCompletion(content="SELECT 1", input_tokens=120, output_tokens=8, headers={"x": "1"})
ROLE = LlmRole.SQL_GENERATOR


class FakeTransport:
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


def make_client(script, *, fallback="small", max_attempts=3, max_wait_s=20.0):
    registry = ModelRegistry(
        default_model="big", fallback_model=fallback, limits={"big": LIMITS, "small": LIMITS}
    )
    transport = FakeTransport(script)
    sleeps = []
    client = LlmClient(
        registry, transport, max_attempts=max_attempts, max_wait_s=max_wait_s, sleep=sleeps.append
    )
    return client, transport, sleeps


# --- success and retries ------------------------------------------------------


def test_success_on_the_first_try():
    client, transport, sleeps = make_client({"big": [OK]})
    result = client.complete(ROLE, MESSAGES)
    assert (result.content, result.model, result.role) == ("SELECT 1", "big", ROLE)
    assert (result.input_tokens, result.output_tokens) == (120, 8)
    assert (result.attempts, result.fallback_used) == (1, False)
    assert result.headers == {"x": "1"}
    assert transport.calls == ["big"]
    assert sleeps == []


def test_retry_after_is_honoured_exactly():
    client, transport, sleeps = make_client({"big": [RateLimitedError(2.0), OK]})
    result = client.complete(ROLE, MESSAGES)
    assert sleeps == [2.0]
    assert result.attempts == 2
    assert transport.calls == ["big", "big"]


def test_without_retry_after_backoff_is_bounded_and_positive():
    client, _, sleeps = make_client({"big": [RateLimitedError(None), ServerError("503"), OK]})
    client.complete(ROLE, MESSAGES)
    assert len(sleeps) == 2
    assert all(0 < s <= client.max_backoff_s for s in sleeps)


@pytest.mark.parametrize("error", [ServerError("503"), CallTimeoutError("slow")])
def test_transient_errors_are_retried(error):
    client, transport, _ = make_client({"big": [error, OK]})
    assert client.complete(ROLE, MESSAGES).attempts == 2
    assert transport.calls == ["big", "big"]


def test_max_attempts_of_one_means_no_retry():
    client, transport, _ = make_client({"big": [ServerError("503")]}, max_attempts=1)
    with pytest.raises(LlmError) as info:
        client.complete(ROLE, MESSAGES)
    assert info.value.code == LlmErrorCode.UNAVAILABLE
    assert transport.calls == ["big"]


# --- fallback ------------------------------------------------------------------


def test_persistent_429_falls_back_and_logs_it(caplog):
    client, transport, _ = make_client({"big": [RateLimitedError(1.0)] * 3, "small": [OK]})
    with caplog.at_level(logging.WARNING, logger="app.llm.client"):
        result = client.complete(ROLE, MESSAGES)
    assert (result.model, result.fallback_used, result.attempts) == ("small", True, 4)
    assert transport.calls == ["big", "big", "big", "small"]
    assert "llm fallback" in caplog.text and "from=big to=small" in caplog.text


def test_retry_after_longer_than_max_wait_skips_waiting_and_falls_back():
    client, transport, sleeps = make_client(
        {"big": [RateLimitedError(60.0)], "small": [OK]}, max_wait_s=20.0
    )
    result = client.complete(ROLE, MESSAGES)
    assert sleeps == [], "must not sleep 60 s inside a request"
    assert result.model == "small"
    assert transport.calls == ["big", "small"]


def test_retired_model_falls_back_without_retrying():
    client, transport, _ = make_client({"big": [ModelGoneError("gone")], "small": [OK]})
    assert client.complete(ROLE, MESSAGES).model == "small"
    assert transport.calls == ["big", "small"]


def test_persistent_429_everywhere_is_rate_limited():
    client, _, _ = make_client(
        {"big": [RateLimitedError(1.0)] * 3, "small": [RateLimitedError(1.0)] * 3}
    )
    with pytest.raises(LlmError) as info:
        client.complete(ROLE, MESSAGES)
    assert info.value.code == LlmErrorCode.RATE_LIMITED
    assert "big, small" in info.value.detail


def test_retired_model_without_fallback_is_model_unavailable():
    client, transport, _ = make_client({"big": [ModelGoneError("gone")]}, fallback=None)
    with pytest.raises(LlmError) as info:
        client.complete(ROLE, MESSAGES)
    assert info.value.code == LlmErrorCode.MODEL_UNAVAILABLE
    assert transport.calls == ["big"]


# --- final errors that never fall back -----------------------------------------


@pytest.mark.parametrize(
    ("error", "code"),
    [
        (CallTimeoutError("slow"), LlmErrorCode.TIMEOUT),
        (ServerError("503"), LlmErrorCode.UNAVAILABLE),
    ],
)
def test_exhausted_transient_errors_do_not_fall_back(error, code):
    client, transport, _ = make_client({"big": [error] * 3, "small": [OK]})
    with pytest.raises(LlmError) as info:
        client.complete(ROLE, MESSAGES)
    assert info.value.code == code
    assert transport.calls == ["big"] * 3


def test_bad_request_fails_immediately():
    client, transport, _ = make_client({"big": [RequestError("HTTP 400")], "small": [OK]})
    with pytest.raises(LlmError) as info:
        client.complete(ROLE, MESSAGES)
    assert info.value.code == LlmErrorCode.BAD_REQUEST
    assert transport.calls == ["big"]


@pytest.mark.parametrize("detail", ["", "  ", None])
def test_llm_error_requires_a_detail(detail):
    with pytest.raises(ValueError, match="non-empty detail"):
        LlmError(LlmErrorCode.TIMEOUT, detail)


# --- retry-after parsing and SDK error mapping ----------------------------------


@pytest.mark.parametrize(
    ("value", "expected"), [("2", 2.0), ("0.5", 0.5), (None, None), ("soon", None), ("-1", None)]
)
def test_parse_retry_after(value, expected):
    assert parse_retry_after(value) == expected


REQUEST = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")


def status_error(cls, status, body=None, headers=None):
    response = httpx.Response(status, headers=headers or {}, request=REQUEST)
    return cls("error", response=response, body=body)


def test_429_maps_to_rate_limited_with_retry_after():
    mapped = map_sdk_error(status_error(groq.RateLimitError, 429, headers={"retry-after": "3"}))
    assert isinstance(mapped, RateLimitedError)
    assert mapped.retry_after == 3.0


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (404, None, ModelGoneError),
        (400, {"error": {"code": "model_decommissioned"}}, ModelGoneError),
        (400, {"error": {"code": "invalid_request_error"}}, RequestError),
        (503, None, ServerError),
    ],
)
def test_status_errors_are_mapped(status, body, expected):
    assert isinstance(map_sdk_error(status_error(groq.APIStatusError, status, body)), expected)


def test_timeout_and_connection_errors_are_mapped():
    assert isinstance(map_sdk_error(groq.APITimeoutError(request=REQUEST)), CallTimeoutError)
    assert isinstance(
        map_sdk_error(groq.APIConnectionError(message="reset", request=REQUEST)), ServerError
    )


def test_unknown_exceptions_map_to_request_error():
    assert isinstance(map_sdk_error(ValueError("x")), RequestError)


# --- the groq transport, over a fake SDK client ----------------------------------


class _Usage:
    prompt_tokens = 150
    completion_tokens = 12


class _Completion:
    usage = _Usage()
    choices = [type("C", (), {"message": type("M", (), {"content": "SELECT 2"})()})()]


class _Raw:
    headers = httpx.Headers({"X-RateLimit-Remaining-Tokens": "7000"})

    def parse(self):
        return _Completion()


class _FakeSdk:
    def __init__(self, error=None):
        self.error = error
        self.kwargs = None
        create = self._create
        self.chat = type("Chat", (), {})()
        self.chat.completions = type("Completions", (), {})()
        self.chat.completions.with_raw_response = type(
            "Raw", (), {"create": staticmethod(create)}
        )()

    def _create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return _Raw()


def test_groq_transport_returns_content_tokens_and_lowercased_headers():
    sdk = _FakeSdk()
    raw = groq_transport(sdk)("big", MESSAGES, 0.0, 256)
    assert (raw.content, raw.input_tokens, raw.output_tokens) == ("SELECT 2", 150, 12)
    assert raw.headers["x-ratelimit-remaining-tokens"] == "7000"
    assert sdk.kwargs == {
        "model": "big",
        "messages": MESSAGES,
        "temperature": 0.0,
        "max_tokens": 256,
    }


def test_groq_transport_maps_sdk_errors():
    sdk = _FakeSdk(error=status_error(groq.RateLimitError, 429, headers={"retry-after": "4"}))
    with pytest.raises(RateLimitedError) as info:
        groq_transport(sdk)("big", MESSAGES, 0.0, None)
    assert info.value.retry_after == 4.0
