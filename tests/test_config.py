"""Unit tests for app/config.py."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import DEFAULT_DB_PATH, ConfigError, Settings, load_settings

SETTINGS_ENV_VARS = (
    "GROQ_API_KEY",
    "GROQ_MODEL",
    "DB_PATH",
    "MAX_ROWS",
    "QUERY_TIMEOUT_S",
    "MAX_HISTORY_TURNS",
)
FAKE_KEY = "gsk_TEST_SECRET_do_not_print_123"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Colab sets GROQ_MODEL in the kernel; tests must not depend on the host."""
    for name in SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def load(**overrides):
    return load_settings(env_file=None, **overrides)


# --- valid configuration ---------------------------------------------------


def test_minimal_valid_settings_use_defaults():
    s = load(groq_api_key=FAKE_KEY, groq_model="some/model")
    assert s.groq_model == "some/model"
    assert s.groq_api_key.get_secret_value() == FAKE_KEY
    assert s.db_path == DEFAULT_DB_PATH
    assert s.max_rows == 200
    assert s.query_timeout_s == 5.0
    assert s.max_history_turns == 3


def test_values_are_read_from_environment(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", FAKE_KEY)
    monkeypatch.setenv("GROQ_MODEL", "env/model")
    monkeypatch.setenv("MAX_ROWS", "25")
    s = load()
    assert s.groq_model == "env/model"
    assert s.max_rows == 25


def test_values_are_read_from_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(f"GROQ_API_KEY={FAKE_KEY}\nGROQ_MODEL=file/model\n")
    s = load_settings(env_file=env_file)
    assert s.groq_model == "file/model"


def test_environment_overrides_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(f"GROQ_API_KEY={FAKE_KEY}\nGROQ_MODEL=file/model\n")
    monkeypatch.setenv("GROQ_MODEL", "env/model")
    assert load_settings(env_file=env_file).groq_model == "env/model"


def test_model_is_stripped():
    assert load(groq_api_key=FAKE_KEY, groq_model="  m  ").groq_model == "m"


def test_relative_db_path_resolves_against_project_root():
    s = load(groq_api_key=FAKE_KEY, groq_model="m", db_path=Path("database/ecommerce.db"))
    assert s.db_path == DEFAULT_DB_PATH


# --- missing or invalid configuration ------------------------------------


def test_missing_model_is_refused_with_its_name():
    """A-01: there is no default model to fall back to."""
    with pytest.raises(ConfigError, match="GROQ_MODEL"):
        load(groq_api_key=FAKE_KEY)


def test_missing_key_and_model_are_both_named():
    with pytest.raises(ConfigError) as info:
        load()
    assert "GROQ_API_KEY" in str(info.value)
    assert "GROQ_MODEL" in str(info.value)


@pytest.mark.parametrize(
    ("overrides", "bad_var"),
    [
        ({"groq_model": "   "}, "GROQ_MODEL"),
        ({"groq_api_key": "  "}, "GROQ_API_KEY"),
        ({"max_rows": 0}, "MAX_ROWS"),
        ({"query_timeout_s": -1}, "QUERY_TIMEOUT_S"),
        ({"max_history_turns": 0}, "MAX_HISTORY_TURNS"),
        ({"db_path": Path("database/does_not_exist.db")}, "DB_PATH"),
    ],
)
def test_invalid_values_are_refused_with_their_name(overrides, bad_var):
    values = {"groq_api_key": FAKE_KEY, "groq_model": "m", **overrides}
    with pytest.raises(ConfigError, match=bad_var):
        load(**values)


# --- the key never leaks ---------------------------------------------------


def test_config_error_never_contains_the_key():
    """pydantic's own error embeds `input_value`, which would include the key."""
    with pytest.raises(ConfigError) as info:
        load(groq_api_key=FAKE_KEY, max_rows=0)  # model missing, rows invalid
    assert FAKE_KEY not in str(info.value)
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__ is True


def test_settings_repr_hides_the_key():
    s = load(groq_api_key=FAKE_KEY, groq_model="m")
    assert FAKE_KEY not in repr(s)
    assert FAKE_KEY not in str(s)


def test_settings_are_read_only():
    s = load(groq_api_key=FAKE_KEY, groq_model="m")
    with pytest.raises(ValidationError):
        s.max_rows = 5


def test_settings_class_is_the_public_type():
    assert isinstance(load(groq_api_key=FAKE_KEY, groq_model="m"), Settings)
