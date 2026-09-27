"""Request id from HTTP entry to the LLM calls, and the access record (Phase 9, D55).

The DoD test of Phase 9 (one request traced end to end) builds on these; this
file covers the id itself: created per request, never taken from the client,
shared by the header, the body, the agent's LLM calls and every log line.
"""

import json
import logging
import uuid

import pytest
from fastapi.testclient import TestClient

from app.agent import Agent
from app.main import create_app
from app.observability.middleware import REQUEST_ID_HEADER
from tests.fakes import FakeLLM

# Built at runtime, so the text never appears in a source line of a stack frame.
SECRET_DETAIL = "token=" + "secret123"
QUESTION = "Which customer lives in Zanzibar-7731?"


@pytest.fixture
def make_client(test_settings):
    def build(llm=None, **kwargs):
        app = create_app(test_settings, llm=llm if llm is not None else FakeLLM())
        return app, TestClient(app, **kwargs)

    return build


def _log_lines(text):
    """The JSON log lines in captured stdout."""
    return [json.loads(line) for line in text.splitlines() if line.startswith("{")]


def test_every_response_gets_a_fresh_uuid_in_the_header(make_client):
    _, client = make_client()
    first = client.get("/health").headers[REQUEST_ID_HEADER]
    second = client.get("/health").headers[REQUEST_ID_HEADER]
    assert str(uuid.UUID(first)) == first and first != second


def test_a_client_supplied_request_id_is_ignored(make_client):
    _, client = make_client()
    response = client.get("/health", headers={REQUEST_ID_HEADER: "forged-id"})
    assert response.headers[REQUEST_ID_HEADER] != "forged-id"


def test_header_body_and_every_llm_call_share_one_id(make_client):
    fake = FakeLLM("SELECT name FROM customers LIMIT 1", "One customer.")
    _, client = make_client(fake)
    response = client.post("/chat", json={"question": "One customer"})
    request_id = response.headers[REQUEST_ID_HEADER]
    assert response.json()["request_id"] == request_id
    assert [k["request_id"] for k in fake.kwargs] == [request_id, request_id]


def test_chat_gets_one_access_record_without_the_question(make_client, capsys):
    fake = FakeLLM("SELECT name FROM customers LIMIT 1", "One customer.")
    _, client = make_client(fake)
    response = client.post("/chat?debug=Zanzibar", json={"question": QUESTION})
    out = capsys.readouterr().out
    (access,) = [line for line in _log_lines(out) if line["event"] == "http.request"]
    assert access["request_id"] == response.headers[REQUEST_ID_HEADER]
    assert access["http.request.method"] == "POST"
    assert access["url.path"] == "/chat"
    assert access["http.response.status_code"] == 200
    assert access["http.duration_ms"] >= 0
    assert access["logger"] == "app.http" and access["level"] == "INFO"
    assert "Zanzibar" not in out, "neither the body nor the query string is logged"


def test_health_probes_get_no_access_record(make_client, capsys):
    _, client = make_client()
    client.get("/health")
    assert not [
        line for line in _log_lines(capsys.readouterr().out) if line["event"] == "http.request"
    ]


def test_log_lines_inside_a_sync_route_carry_the_request_id(make_client, capsys):
    """Sync routes run in a worker thread; the id must follow them there."""
    app, client = make_client()

    def probe():
        logging.getLogger("app.probe").info("probe.ran")
        return {"ok": True}

    app.add_api_route("/probe", probe)
    response = client.get("/probe")
    lines = _log_lines(capsys.readouterr().out)
    (probe_line,) = [line for line in lines if line["event"] == "probe.ran"]
    assert probe_line["request_id"] == response.headers[REQUEST_ID_HEADER]


def test_an_unhandled_route_error_is_recorded_as_500(make_client, capsys):
    app, client = make_client(raise_server_exceptions=False)

    def boom():
        raise RuntimeError(SECRET_DETAIL)

    app.add_api_route("/boom", boom)
    assert client.get("/boom").status_code == 500
    lines = _log_lines(capsys.readouterr().out)
    (access,) = [line for line in lines if line["event"] == "http.request"]
    assert access["http.response.status_code"] == 500 and access["url.path"] == "/boom"
    assert access["request_id"] is not None


def test_a_chat_crash_is_logged_by_type_and_answered_with_the_request_id(make_client, capsys):
    """Replaces the bare print: the log gets the type and stack, never the message (H5)."""

    class ExplodingLLM:
        def complete(self, role, messages, **kwargs):
            raise RuntimeError(SECRET_DETAIL)

    _, client = make_client(ExplodingLLM())
    response = client.post("/chat", json={"question": "Show all customers"})
    request_id = response.headers[REQUEST_ID_HEADER]
    assert response.json()["error"] == "internal_error"
    assert response.json()["request_id"] == request_id
    out = capsys.readouterr().out
    (crash,) = [line for line in _log_lines(out) if line["event"] == "chat.unhandled_error"]
    assert crash["level"] == "ERROR" and crash["logger"] == "app.main"
    assert crash["error.type"] == "RuntimeError" and crash["error.stack"]
    assert crash["request_id"] == request_id
    assert "secret123" not in out


def test_the_agent_uses_the_request_id_it_is_given(test_settings):
    fake = FakeLLM("SELECT name FROM customers LIMIT 1", "One customer.")
    result = Agent(test_settings, llm=fake).ask("One customer", request_id="req-given")
    assert result["request_id"] == "req-given"
    assert [k["request_id"] for k in fake.kwargs] == ["req-given", "req-given"]
