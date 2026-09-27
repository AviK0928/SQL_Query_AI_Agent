"""The post-deploy smoke test, against a scripted opener (Phase 10, D57)."""

import io
import json
import urllib.error

import pytest

from app.smoke import main

BASE = "https://agent.example.test"
REQ_ID = "0b5c3a52-94c4-4c1e-9d4e-3a4a0f7f2d11"
GOOD_CHAT = {"answer": "There are 20 customers.", "error": None, "request_id": REQ_ID}


class Response:
    def __init__(self, status, body, headers=None):
        self.status = status
        self.headers = headers or {}
        self._raw = body if isinstance(body, bytes) else json.dumps(body).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def http_error(status, body):
    raw = json.dumps(body).encode()
    return urllib.error.HTTPError(f"{BASE}/chat", status, "err", {}, io.BytesIO(raw))


class Opener:
    """Scripted by path: each value is a Response to return or an exception to raise."""

    def __init__(self, **by_path):
        self.by_path = by_path
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append((request.get_method(), request.full_url, request.data, timeout))
        outcome = self.by_path[request.full_url.removeprefix(BASE)]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


HEALTHY = Response(200, {"status": "ok"})


def run(capsys, argv, opener):
    code = main(argv, opener=opener)
    return code, capsys.readouterr().out


def test_health_only_by_default_and_no_quota_spent(capsys):
    opener = Opener(**{"/health": HEALTHY})
    code, out = run(capsys, [BASE + "/"], opener)
    assert code == 0
    assert [r[:2] for r in opener.requests] == [("GET", f"{BASE}/health")]
    assert opener.requests[0][3] == 90.0
    assert "(no --question: /chat not checked, no LLM quota spent)" in out
    assert out.rstrip().endswith("SMOKE TEST PASSED")


def test_a_question_is_asked_and_its_request_id_checked(capsys):
    chat = Response(200, GOOD_CHAT, headers={"X-Request-ID": REQ_ID})
    opener = Opener(**{"/health": HEALTHY, "/chat": chat})
    code, out = run(capsys, [BASE, "--question", "How many customers?", "--timeout", "5"], opener)
    assert code == 0
    method, url, data, timeout = opener.requests[1]
    assert (method, url, json.loads(data), timeout) == (
        "POST",
        f"{BASE}/chat",
        {"question": "How many customers?"},
        5.0,
    )
    assert f"request_id={REQ_ID} answer: 'There are 20 customers.'" in out
    assert "ok   POST /chat" in out and "SMOKE TEST PASSED" in out


@pytest.mark.parametrize(
    ("chat", "problem"),
    [
        (http_error(429, {"error": "RATE_LIMITED"}), "POST /chat returned 429"),
        (Response(200, b"<html>"), "POST /chat returned 200 None"),
        (
            Response(200, {**GOOD_CHAT, "error": "LLM_RATE_LIMITED"}, {"x-request-id": REQ_ID}),
            "POST /chat answered with error LLM_RATE_LIMITED",
        ),
        (
            Response(200, {**GOOD_CHAT, "answer": ""}, {"x-request-id": REQ_ID}),
            "POST /chat returned an empty answer",
        ),
        (Response(200, GOOD_CHAT), "X-Request-ID header does not match"),
        (
            Response(200, GOOD_CHAT, {"X-Request-ID": "other"}),
            "X-Request-ID header does not match",
        ),
        (TimeoutError("slow"), "POST /chat failed: TimeoutError"),
    ],
)
def test_every_chat_failure_is_reported(capsys, chat, problem):
    code, out = run(
        capsys, [BASE, "--question", "q"], Opener(**{"/health": HEALTHY, "/chat": chat})
    )
    assert code == 1
    assert "FAIL POST /chat" in out and problem in out
    assert out.rstrip().endswith("SMOKE TEST FAILED")


@pytest.mark.parametrize(
    ("health", "problem"),
    [
        (Response(200, {"status": "starting"}), "GET /health returned 200 {'status': 'starting'}"),
        (http_error(503, {}), "GET /health returned 503 {}"),
        (Response(502, b""), "GET /health returned 502 None"),
        (urllib.error.URLError("no route"), "GET /health failed: URLError"),
    ],
)
def test_a_failed_health_check_skips_the_question(capsys, health, problem):
    opener = Opener(**{"/health": health})
    code, out = run(capsys, [BASE, "--question", "q"], opener)
    assert code == 1
    assert problem in out and "SMOKE TEST FAILED" in out
    assert len(opener.requests) == 1, "no quota spent after a failed health check"


def test_a_url_without_a_scheme_is_refused(capsys):
    opener = Opener()
    code, out = run(capsys, ["agent.example.test"], opener)
    assert code == 1 and opener.requests == []
    assert out == "FAIL base URL must start with https:// or http://: agent.example.test\n"
