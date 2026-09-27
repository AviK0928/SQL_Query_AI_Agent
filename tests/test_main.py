"""App factory, startup validation, lazy module app, and the session store."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.main as main
from app.config import ConfigError
from app.main import SessionStore, create_app
from tests.fakes import TEST_LLM_LIMITS, TEST_MODEL, FakeLLM


@pytest.fixture
def no_env_file(monkeypatch):
    """Ignore a developer's local .env so only the test's environment counts."""
    monkeypatch.setattr(main, "ENV_FILE", None)


def test_create_app_refuses_to_start_without_config(no_env_file):
    with pytest.raises(ConfigError, match="GROQ_API_KEY"):
        create_app()


def test_module_app_is_lazy_and_validated(no_env_file, monkeypatch):
    """`uvicorn app.main:app` still works, but only once config is valid."""
    vars(main).pop("app", None)
    try:
        with pytest.raises(ConfigError):
            _ = main.app

        monkeypatch.setenv("GROQ_API_KEY", "test-key-not-real")
        monkeypatch.setenv("GROQ_MODEL", TEST_MODEL)
        monkeypatch.setenv("LLM_LIMITS", json.dumps(TEST_LLM_LIMITS))
        assert isinstance(main.app, FastAPI)
        assert main.app is main.app, "built once, then cached"
    finally:
        vars(main).pop("app", None)


def test_session_store_keeps_only_recent_turns():
    store = SessionStore(max_sessions=10, max_turns=2)
    for i in range(5):
        store.append("s", f"q{i}", f"SELECT {i}")
    assert [t["question"] for t in store.get("s")] == ["q3", "q4"]


def test_session_store_evicts_oldest_session():
    store = SessionStore(max_sessions=2, max_turns=3)
    store.append("a", "q", "SELECT 1")
    store.append("b", "q", "SELECT 1")
    store.append("c", "q", "SELECT 1")
    assert len(store) == 2
    assert store.get("a") == []
    assert store.get("c") != []


def test_without_a_frontend_the_root_describes_the_api(test_settings, monkeypatch, tmp_path):
    """A deployment without frontend/ still starts: / explains the API, /static is not mounted."""
    missing = tmp_path / "no-frontend"
    monkeypatch.setattr(main, "FRONTEND_DIR", missing)
    monkeypatch.setattr(main, "INDEX_FILE", missing / "index.html")
    client = TestClient(create_app(test_settings, llm=FakeLLM()))
    root = client.get("/")
    assert root.status_code == 200
    assert root.json()["endpoints"] == ["/health", "/schema", "/chat", "/docs"]
    assert client.get("/static/app.js").status_code == 404


def test_a_frontend_without_index_still_serves_its_files(test_settings, monkeypatch, tmp_path):
    (tmp_path / "app.js").write_text("// built asset\n")
    monkeypatch.setattr(main, "FRONTEND_DIR", tmp_path)
    monkeypatch.setattr(main, "INDEX_FILE", tmp_path / "index.html")
    client = TestClient(create_app(test_settings, llm=FakeLLM()))
    assert "API is running" in client.get("/").json()["message"]
    assert client.get("/static/app.js").text == "// built asset\n"
