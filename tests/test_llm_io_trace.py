"""Phase 9 definition of done: one request traced from HTTP entry to final answer (D55, D56).

One POST /chat runs over the production stack (app, agent, gateway with the
call log, client) with only the transport to Groq scripted. The request id in
the X-Request-ID header must find, in the call log, every LLM call that request
made, in order, with the messages sent and the replies received; the viewer
must print that conversation; and the app's own log lines carry the same id.
"""

import json

from fastapi.testclient import TestClient

from app.config import load_settings
from app.llm.calllog import CallLogger
from app.llm.client import LlmClient, RawCompletion
from app.llm.gateway import LlmGateway
from app.llm.registry import ModelRegistry
from app.main import create_app
from app.observability.middleware import REQUEST_ID_HEADER
from app.observability.view import main as view
from tests.fakes import TEST_LLM_LIMITS, TEST_MODEL

QUESTION = "How many customers are there?"
SQL = "SELECT COUNT(*) FROM customers"
ANSWER = "There are 20 customers."


def test_one_chat_request_is_traceable_from_http_entry_to_the_answer(tmp_path, capsys):
    log_path = tmp_path / "calls.jsonl"
    settings = load_settings(
        env_file=None,
        groq_api_key="test-key-not-real",
        groq_model=TEST_MODEL,
        llm_limits=TEST_LLM_LIMITS,
        llm_log_content=True,
        llm_log_path=log_path,
    )
    replies = [
        RawCompletion(SQL, input_tokens=300, output_tokens=12),
        RawCompletion(ANSWER, input_tokens=200, output_tokens=8),
    ]

    def transport(model, messages, temperature, max_tokens):
        return replies.pop(0)

    client = LlmClient(
        ModelRegistry.from_settings(settings),
        transport,
        max_attempts=settings.llm_max_attempts,
        max_wait_s=settings.llm_max_wait_s,
        sleep=lambda seconds: None,
    )
    gateway = LlmGateway(client, call_log=CallLogger.from_settings(settings))
    http = TestClient(create_app(settings, llm=gateway))

    response = http.post("/chat", json={"question": QUESTION})
    request_id = response.headers[REQUEST_ID_HEADER]
    body = response.json()
    assert (body["request_id"], body["answer"], body["error"]) == (request_id, ANSWER, None)
    app_log = capsys.readouterr().out

    # 1. The app's own log: the access record carries the id.
    access = [json.loads(line) for line in app_log.splitlines() if '"http.request"' in line]
    assert [a["request_id"] for a in access] == [request_id]

    # 2. The call log: both calls, in order, with the id, what was sent and what came back.
    calls = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert [c["request_id"] for c in calls] == [request_id, request_id]
    assert [c["llm.role"] for c in calls] == ["sql_generator", "synthesizer"]
    assert QUESTION in calls[0]["gen_ai.input.messages"][-1]["content"]
    assert calls[0]["gen_ai.output.text"] == SQL
    assert "COUNT(*)\n20" in calls[1]["gen_ai.input.messages"][-1]["content"], "the rows sent"
    assert calls[1]["gen_ai.output.text"] == ANSWER

    # 3. The viewer prints that conversation for the id.
    assert view([str(log_path), "--request-id", request_id]) == 0
    printed = capsys.readouterr().out
    assert printed.splitlines()[0] == f"=== request {request_id}: 2 LLM call(s)"
    assert printed.index(QUESTION) < printed.index(SQL) < printed.index(ANSWER)
