"""Typed, validated application settings.

Every environment variable the app reads is declared here, with its type,
default and constraints. Missing or invalid settings fail at startup with a
message that names the variables and never echoes their values.

Spring comparison: a @ConfigurationProperties class with @Validated, bound
once at boot instead of read ad hoc through os.environ.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic import (
    PositiveFloat,
    PositiveInt,
    SecretStr,
    ValidationError,
    field_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "database" / "ecommerce.db"
DEFAULT_ENV_FILE = PROJECT_ROOT / ".env"


class ConfigError(RuntimeError):
    """Raised when settings are missing or invalid. Never contains values."""


class Settings(BaseSettings):
    """Application settings. Field names map to upper-case env vars."""

    model_config = SettingsConfigDict(
        env_file_encoding="utf-8",
        extra="ignore",  # unrelated env vars and .env keys are not errors
        frozen=True,  # settings are read-only after startup
    )

    groq_api_key: SecretStr
    groq_model: str  # no default: a stale fallback model is how A-01 happened
    db_path: Path = DEFAULT_DB_PATH
    max_rows: PositiveInt = 200
    query_timeout_s: PositiveFloat = 5.0
    max_history_turns: PositiveInt = 3

    @field_validator("groq_api_key")
    @classmethod
    def _key_not_blank(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value().strip():
            raise ValueError("must not be blank")
        return value

    @field_validator("groq_model")
    @classmethod
    def _model_not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be blank")
        return value

    @field_validator("db_path")
    @classmethod
    def _db_file_exists(cls, value: Path) -> Path:
        if not value.is_absolute():
            value = PROJECT_ROOT / value  # relative paths are relative to the repo, not the cwd
        if not value.is_file():
            raise ValueError(f"database file not found: {value}")
        return value


def load_settings(env_file: Path | None = DEFAULT_ENV_FILE, **overrides: Any) -> Settings:
    """Build Settings from overrides, then env vars, then the .env file.

    Pass env_file=None to ignore .env (tests do this). Raises ConfigError that
    lists each bad variable and the reason. The underlying ValidationError is
    suppressed on purpose: its `input_value` field can contain the API key.
    """
    try:
        return Settings(_env_file=env_file, **overrides)
    except ValidationError as exc:
        problems = [
            f"{'.'.join(str(part) for part in err['loc']).upper()} ({err['msg']})"
            for err in exc.errors(include_input=False, include_url=False)
        ]
        raise ConfigError("Invalid configuration: " + "; ".join(problems)) from None
