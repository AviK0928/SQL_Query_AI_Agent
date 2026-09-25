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
    Field,
    PositiveFloat,
    PositiveInt,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.llm.registry import LlmRole, ModelLimits

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

    # --- LLM client (Phase 4) ------------------------------------------------
    # Per-model limits from the Groq console, as JSON. Required, and every model
    # the app can call must have an entry: limits are never hard-coded.
    llm_limits: dict[str, ModelLimits]
    groq_fallback_model: str | None = None  # used on persistent 429 or a retired model
    # Per-role overrides of GROQ_MODEL, as JSON; Phase 6 fills these in with evals.
    llm_role_models: dict[LlmRole, str] = Field(default_factory=dict)
    llm_safety_margin: float = Field(0.8, gt=0, le=1)  # share of each limit the client uses
    llm_max_wait_s: PositiveFloat = 20.0  # longest the rate limiter may make a request wait
    llm_timeout_s: PositiveFloat = 30.0  # per HTTP call to Groq
    llm_max_attempts: PositiveInt = 3  # per model, including the first try
    llm_cache_path: Path | None = None  # response cache; off unless set (dev, evals)
    llm_log_content: bool = False  # log prompts and responses; off in production
    llm_log_path: Path | None = None  # JSONL call log; stdout when unset

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

    @field_validator("groq_fallback_model")
    @classmethod
    def _fallback_blank_is_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @field_validator("llm_role_models")
    @classmethod
    def _role_models_not_blank(cls, value: dict[LlmRole, str]) -> dict[LlmRole, str]:
        cleaned = {role: model.strip() for role, model in value.items()}
        if any(not model for model in cleaned.values()):
            raise ValueError("role models must not be blank")
        return cleaned

    @field_validator("db_path")
    @classmethod
    def _db_file_exists(cls, value: Path) -> Path:
        if not value.is_absolute():
            value = PROJECT_ROOT / value  # relative paths are relative to the repo, not the cwd
        if not value.is_file():
            raise ValueError(f"database file not found: {value}")
        return value

    @model_validator(mode="after")
    def _every_model_has_limits(self) -> Settings:
        if self.groq_fallback_model == self.groq_model:
            raise ValueError("GROQ_FALLBACK_MODEL must differ from GROQ_MODEL")
        used = {"GROQ_MODEL": self.groq_model}
        if self.groq_fallback_model:
            used["GROQ_FALLBACK_MODEL"] = self.groq_fallback_model
        for role, model in self.llm_role_models.items():
            used[f"LLM_ROLE_MODELS[{role.value}]"] = model
        missing = [
            f"{model!r} (from {source})"
            for source, model in used.items()
            if model not in self.llm_limits
        ]
        if missing:
            raise ValueError("LLM_LIMITS has no entry for " + ", ".join(missing))
        return self


def load_settings(env_file: Path | None = DEFAULT_ENV_FILE, **overrides: Any) -> Settings:
    """Build Settings from overrides, then env vars, then the .env file.

    Pass env_file=None to ignore .env (tests do this). Raises ConfigError that
    lists each bad variable and the reason. The underlying ValidationError is
    suppressed on purpose: its `input_value` field can contain the API key.
    """
    try:
        return Settings(_env_file=env_file, **overrides)
    except ValidationError as exc:
        problems = []
        for err in exc.errors(include_input=False, include_url=False):
            name = ".".join(str(part) for part in err["loc"]).upper()
            problems.append(f"{name} ({err['msg']})" if name else err["msg"])
        raise ConfigError("Invalid configuration: " + "; ".join(problems)) from None
