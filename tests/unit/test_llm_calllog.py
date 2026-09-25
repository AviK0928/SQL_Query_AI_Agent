"""Unit tests for app/llm/calllog.py and the gateway's use of it."""

import json

import pytest

from app.config import load_settings
from app.llm.cache import ResponseCache
from app.llm.calllog import CallContext, CallLogger
from app.llm.client import LlmError, LlmErrorCode, LlmResult
from app.llm.gateway import LlmGateway
from app.llm.registry import LlmRole, ModelLimits, ModelRegistry

LIMITS = ModelLimits(rpm=30, rpd=1000, tpm=8000, tpd=200000)
KEY = "gsk_TEST_SECRET_never_log_me_123"
ROLE = LlmRole.SQL_GENERATOR
MESSAGES = [{"role": "system", "content": "schema..."}, {"role": "user", "content": "How many?"}]
HEADERS = {
    "x-ratelimit-remaining-tokens": "7200",
    "x-ratelimit-remaining-requests": "990",
    "set-cookie": "session=abc",
}


def result(content="SELECT COUNT(*) FROM customers", **overrides):
    values = dict(
        content=content,
        role=ROLE,
        model="big",
        input_tokens=120,
        output_tokens=9,
        attempts=1,
        fallback_used=False,
        latency_ms=42,
        headers=HEADERS,
    )
    values.update(overrides)
    return LlmResult(**values)


class StubClient:
    def __init__(self, *outcomes):
        self.registry = ModelRegistry(
            default_model="big", fallback_model=None, limits={"big": LIMITS}
        )
        self.outcomes = list(outcomes)

    def complete(self, role, messages, *, temperature, max_tokens):
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def logger(**kwargs):
    lines = []
    return CallLogger(secrets=[KEY], sink=lines.append, **kwargs), lines


def records(lines):
    return [json.loads(line) for line in lines]


def gateway(*outcomes, cache=None, **log_kwargs):
    call_log, lines = logger(**log_kwargs)
    return LlmGateway(StubClient(*outcomes), cache=cache, call_log=call_log), lines


# --- success records --------------------------------------------------------------


def test_a_success_writes_one_json_line_with_otel_style_fields():
    gw, lines = gateway(result())
    gw.complete(
        ROLE,
        MESSAGES,
        max_tokens=256,
        prompt_id="sql_gen.v1",
        schema_hash="207e7a26b02f",
        request_id="req-1",
    )
    assert len(lines) == 1
    rec = records(lines)[0]
    assert rec["event"] == "llm.call" and rec["request_id"] == "req-1"
    assert rec["gen_ai.system"] == "groq"
    assert rec["gen_ai.request.model"] == "big" and rec["gen_ai.response.model"] == "big"
    assert (rec["gen_ai.usage.input_tokens"], rec["gen_ai.usage.output_tokens"]) == (120, 9)
    assert rec["gen_ai.request.max_tokens"] == 256 and rec["gen_ai.request.temperature"] == 0.0
    assert (rec["llm.role"], rec["llm.prompt_id"], rec["llm.schema_hash"]) == (
        "sql_generator",
        "sql_gen.v1",
        "207e7a26b02f",
    )
    assert (rec["llm.attempts"], rec["llm.fallback_used"], rec["llm.cache_hit"]) == (
        1,
        False,
        False,
    )
    assert rec["llm.latency_ms"] == 42
    assert "error.type" not in rec


def test_content_is_off_by_default_but_its_size_is_logged():
    gw, lines = gateway(result())
    gw.complete(ROLE, MESSAGES)
    rec = records(lines)[0]
    assert "gen_ai.input.messages" not in rec and "gen_ai.output.text" not in rec
    assert rec["llm.messages"] == 2
    assert rec["llm.input_chars"] == len("schema...") + len("How many?")
    assert "How many?" not in lines[0]


def test_content_is_logged_only_when_enabled():
    gw, lines = gateway(result(), log_content=True)
    gw.complete(ROLE, MESSAGES)
    rec = records(lines)[0]
    assert rec["gen_ai.input.messages"] == MESSAGES
    assert rec["gen_ai.output.text"] == "SELECT COUNT(*) FROM customers"


def test_only_rate_limit_headers_are_logged():
    gw, lines = gateway(result())
    gw.complete(ROLE, MESSAGES)
    assert records(lines)[0]["llm.rate_limit"] == {
        "x-ratelimit-remaining-requests": "990",
        "x-ratelimit-remaining-tokens": "7200",
    }
    assert "session=abc" not in lines[0]


def test_a_cache_hit_is_logged(tmp_path):
    gw, lines = gateway(result(), cache=ResponseCache(tmp_path / "c.sqlite"))
    gw.complete(ROLE, MESSAGES)
    gw.complete(ROLE, MESSAGES)
    first, second = records(lines)
    assert (first["llm.cache_hit"], second["llm.cache_hit"]) == (False, True)
    assert second["llm.attempts"] == 0


# --- failures ----------------------------------------------------------------------


def test_an_llm_error_is_logged_with_its_code_and_reraised():
    gw, lines = gateway(LlmError(LlmErrorCode.RATE_LIMITED, "rate limited on big"))
    with pytest.raises(LlmError):
        gw.complete(ROLE, MESSAGES, request_id="req-2")
    rec = records(lines)[0]
    assert rec["error.type"] == "LLM_RATE_LIMITED"
    assert rec["error.detail"] == "rate limited on big"
    assert rec["request_id"] == "req-2"
    assert "gen_ai.usage.input_tokens" not in rec


def test_an_unexpected_error_is_logged_by_type_without_its_message():
    gw, lines = gateway(RuntimeError(f"boom with {KEY}"))
    with pytest.raises(RuntimeError):
        gw.complete(ROLE, MESSAGES)
    rec = records(lines)[0]
    assert (rec["error.type"], rec["error.detail"]) == ("RuntimeError", "unexpected error")


# --- the key never leaks -------------------------------------------------------------


def test_secrets_are_redacted_from_every_field():
    gw, lines = gateway(
        result(content=f"echo {KEY}"),
        LlmError(LlmErrorCode.BAD_REQUEST, f"rejected key {KEY}"),
        log_content=True,
    )
    leaky = [{"role": "user", "content": f"my key is {KEY}"}]
    gw.complete(ROLE, leaky)
    with pytest.raises(LlmError):
        gw.complete(ROLE, leaky)
    assert all(KEY not in line for line in lines)
    assert all("***" in line for line in lines)


def test_empty_secrets_are_ignored():
    call_log = CallLogger(secrets=["", None], sink=lambda line: None)
    assert call_log.secrets == ()


# --- sinks and wiring ----------------------------------------------------------------


CTX = CallContext(
    role=ROLE,
    messages=MESSAGES,
    request_model="big",
    temperature=0.0,
    max_tokens=None,
    prompt_id="p",
    schema_hash="h",
    request_id=None,
)


def test_file_sink_appends_json_lines_and_creates_the_folder(tmp_path):
    path = tmp_path / "logs" / "llm_calls.jsonl"
    call_log = CallLogger(path=path)
    call_log.success(result(), CTX)
    call_log.success(result(), CTX)
    lines = path.read_text().splitlines()
    assert len(lines) == 2 and all(json.loads(line)["event"] == "llm.call" for line in lines)


def test_default_sink_is_stdout(capsys):
    CallLogger().success(result(), CTX)
    assert json.loads(capsys.readouterr().out)["event"] == "llm.call"


def test_logger_is_built_from_settings(tmp_path):
    settings = load_settings(
        env_file=None,
        groq_api_key=KEY,
        groq_model="big",
        llm_limits={"big": LIMITS.model_dump()},
        llm_log_content=True,
        llm_log_path=tmp_path / "calls.jsonl",
    )
    call_log = CallLogger.from_settings(settings)
    assert call_log.log_content is True
    assert call_log.path == tmp_path / "calls.jsonl"
    assert call_log.secrets == (KEY,)
