"""Unit tests for app/config.py."""

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import DEFAULT_DB_PATH, ConfigError, Settings, load_settings
from app.llm.registry import LlmRole

SETTINGS_ENV_VARS = tuple(name.upper() for name in Settings.model_fields)
FAKE_KEY = "gsk_TEST_SECRET_do_not_print_123"
LIMITS = {"rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000}
M_LIMITS = {"m": LIMITS}


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Colab sets GROQ_MODEL in the kernel; tests must not depend on the host."""
    for name in SETTINGS_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def load(**overrides):
    return load_settings(env_file=None, **overrides)


def valid(**overrides):
    """The smallest valid configuration: key, model, and limits for that model."""
    base = {"groq_api_key": FAKE_KEY, "groq_model": "m", "llm_limits": M_LIMITS}
    return load(**{**base, **overrides})


# --- valid configuration ---------------------------------------------------


def test_minimal_valid_settings_use_defaults():
    s = valid(groq_model="some/model", llm_limits={"some/model": LIMITS})
    assert s.groq_model == "some/model"
    assert s.groq_api_key.get_secret_value() == FAKE_KEY
    assert s.db_path == DEFAULT_DB_PATH
    assert s.max_rows == 200
    assert s.query_timeout_s == 5.0
    assert s.max_history_turns == 3
    assert s.llm_limits["some/model"].tpm == 8000
    assert s.groq_fallback_model is None
    assert s.llm_role_models == {}
    assert s.llm_safety_margin == 0.8
    assert s.llm_max_wait_s == 20.0
    assert s.llm_timeout_s == 30.0
    assert s.llm_max_attempts == 3
    assert s.llm_cache_path is None
    assert s.llm_log_content is False
    assert s.llm_log_path is None


def test_values_are_read_from_environment(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", FAKE_KEY)
    monkeypatch.setenv("GROQ_MODEL", "env/model")
    monkeypatch.setenv("MAX_ROWS", "25")
    monkeypatch.setenv("LLM_LIMITS", json.dumps({"env/model": LIMITS, "env/small": LIMITS}))
    monkeypatch.setenv("GROQ_FALLBACK_MODEL", "env/small")
    monkeypatch.setenv("LLM_ROLE_MODELS", json.dumps({"synthesizer": "env/small"}))
    monkeypatch.setenv("LLM_LOG_CONTENT", "true")
    s = load()
    assert s.groq_model == "env/model"
    assert s.max_rows == 25
    assert s.llm_limits["env/small"].rpm == 30
    assert s.groq_fallback_model == "env/small"
    assert s.llm_role_models == {LlmRole.SYNTHESIZER: "env/small"}
    assert s.llm_log_content is True


def test_values_are_read_from_env_file(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"GROQ_API_KEY={FAKE_KEY}\nGROQ_MODEL=file/model\n"
        f"LLM_LIMITS={json.dumps({'file/model': LIMITS})}\n"
    )
    s = load_settings(env_file=env_file)
    assert s.groq_model == "file/model"


def test_environment_overrides_env_file(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        f"GROQ_API_KEY={FAKE_KEY}\nGROQ_MODEL=file/model\n"
        f"LLM_LIMITS={json.dumps({'file/model': LIMITS, 'env/model': LIMITS})}\n"
    )
    monkeypatch.setenv("GROQ_MODEL", "env/model")
    assert load_settings(env_file=env_file).groq_model == "env/model"


def test_model_names_are_stripped():
    s = valid(groq_model="  m  ", groq_fallback_model="  f ", llm_limits={"m": LIMITS, "f": LIMITS})
    assert (s.groq_model, s.groq_fallback_model) == ("m", "f")


def test_blank_fallback_means_no_fallback():
    assert valid(groq_fallback_model="   ").groq_fallback_model is None


def test_relative_db_path_resolves_against_project_root():
    s = valid(db_path=Path("database/ecommerce.db"))
    assert s.db_path == DEFAULT_DB_PATH


# --- missing or invalid configuration ------------------------------------


def test_missing_model_is_refused_with_its_name():
    """A-01: there is no default model to fall back to."""
    with pytest.raises(ConfigError, match="GROQ_MODEL"):
        load(groq_api_key=FAKE_KEY, llm_limits=M_LIMITS)


def test_missing_key_and_model_are_both_named():
    with pytest.raises(ConfigError) as info:
        load()
    assert "GROQ_API_KEY" in str(info.value)
    assert "GROQ_MODEL" in str(info.value)


def test_missing_limits_are_refused_with_their_name():
    """Limits are never hard-coded, so there is no default to fall back to."""
    with pytest.raises(ConfigError, match="LLM_LIMITS"):
        load(groq_api_key=FAKE_KEY, groq_model="m")


@pytest.mark.parametrize(
    ("overrides", "source"),
    [
        ({"groq_model": "unlisted"}, "GROQ_MODEL"),
        ({"groq_fallback_model": "unlisted"}, "GROQ_FALLBACK_MODEL"),
        ({"llm_role_models": {"sql_generator": "unlisted"}}, "LLM_ROLE_MODELS[sql_generator]"),
    ],
)
def test_every_model_in_use_needs_limits(overrides, source):
    with pytest.raises(ConfigError) as info:
        valid(**overrides)
    message = str(info.value)
    assert "LLM_LIMITS has no entry for 'unlisted'" in message
    assert source in message


def test_fallback_must_differ_from_the_primary_model():
    with pytest.raises(ConfigError, match="must differ from GROQ_MODEL"):
        valid(groq_fallback_model="m")


def test_unknown_role_is_refused():
    with pytest.raises(ConfigError, match="LLM_ROLE_MODELS"):
        valid(llm_role_models={"not_a_role": "m"})


@pytest.mark.parametrize(
    ("overrides", "bad_var"),
    [
        ({"groq_model": "   "}, "GROQ_MODEL"),
        ({"groq_api_key": "  "}, "GROQ_API_KEY"),
        ({"max_rows": 0}, "MAX_ROWS"),
        ({"query_timeout_s": -1}, "QUERY_TIMEOUT_S"),
        ({"max_history_turns": 0}, "MAX_HISTORY_TURNS"),
        ({"db_path": Path("database/does_not_exist.db")}, "DB_PATH"),
        ({"llm_limits": {"m": {**LIMITS, "rpm": 0}}}, "LLM_LIMITS"),
        ({"llm_limits": {"m": {"rpm": 30}}}, "LLM_LIMITS"),
        ({"llm_safety_margin": 0}, "LLM_SAFETY_MARGIN"),
        ({"llm_safety_margin": 1.5}, "LLM_SAFETY_MARGIN"),
        ({"llm_max_wait_s": 0}, "LLM_MAX_WAIT_S"),
        ({"llm_timeout_s": 0}, "LLM_TIMEOUT_S"),
        ({"llm_max_attempts": 0}, "LLM_MAX_ATTEMPTS"),
        ({"llm_role_models": {"synthesizer": " "}}, "LLM_ROLE_MODELS"),
    ],
)
def test_invalid_values_are_refused_with_their_name(overrides, bad_var):
    with pytest.raises(ConfigError, match=bad_var):
        valid(**overrides)


# --- the key never leaks ---------------------------------------------------


def test_config_error_never_contains_the_key():
    """pydantic's own error embeds `input_value`, which would include the key."""
    with pytest.raises(ConfigError) as info:
        load(groq_api_key=FAKE_KEY, max_rows=0)  # model and limits missing, rows invalid
    assert FAKE_KEY not in str(info.value)
    assert info.value.__cause__ is None
    assert info.value.__suppress_context__ is True


def test_settings_repr_hides_the_key():
    s = valid()
    assert FAKE_KEY not in repr(s)
    assert FAKE_KEY not in str(s)


def test_settings_are_read_only():
    s = valid()
    with pytest.raises(ValidationError):
        s.max_rows = 5


def test_settings_class_is_the_public_type():
    assert isinstance(valid(), Settings)


@pytest.mark.parametrize("value", [-1, 4])
def test_repair_attempts_are_bounded(value):
    with pytest.raises(ConfigError, match="MAX_REPAIR_ATTEMPTS"):
        valid(max_repair_attempts=value)


def test_repair_attempts_default_to_one():
    assert valid().max_repair_attempts == 1
