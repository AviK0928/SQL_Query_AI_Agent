"""Proves the conftest offline guard works, including the A-04 regression."""

import os
import socket

import pytest
from fastapi.testclient import TestClient

from app.config import load_settings
from app.main import create_app
from tests.fakes import NetworkBlockedError


def test_groq_key_is_removed_for_every_test():
    assert "GROQ_API_KEY" not in os.environ


def test_outbound_connection_is_blocked_and_recorded(offline_guard):
    with pytest.raises(NetworkBlockedError):
        socket.create_connection(("api.groq.com", 443), timeout=1)
    assert offline_guard, "the attempt must be recorded, not only refused"
    offline_guard.clear()  # expected attempt; do not fail teardown


def test_raw_ip_connection_is_blocked(offline_guard):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(NetworkBlockedError):
            sock.connect(("8.8.8.8", 53))
    finally:
        sock.close()
    offline_guard.clear()


def test_loopback_is_still_allowed():
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    client = socket.create_connection(server.getsockname(), timeout=1)
    client.close()
    server.close()


def test_a04_leak_is_now_caught(offline_guard):
    """A-04 regression. With a real-looking key and no fake injected, the app
    builds the real Groq stack, and a real question reaches Groq. The
    app turns the failure into an LLM_UNAVAILABLE answer, so only the recorded attempt
    proves the guard saw it."""
    settings = load_settings(
        env_file=None,
        groq_api_key="dummy-key-for-guard-test",
        groq_model="fake/model",
        llm_limits={"fake/model": {"rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000}},
        llm_max_attempts=1,  # one attempt: the guard only needs to see it once
    )
    client = TestClient(create_app(settings))  # llm=None: the real Groq client

    response = client.post("/chat", json={"question": "How many customers are there?"})

    assert response.json()["error"] == "LLM_UNAVAILABLE"  # was internal_error before Phase 4 (D23)
    assert offline_guard, "the Groq call must have been attempted and blocked"
    offline_guard.clear()
