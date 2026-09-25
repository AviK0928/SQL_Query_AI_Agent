"""Model roles, per-model rate limits, and which model serves which role.

Models are chosen per role, not globally (Phase 6 assigns them with evals).
Until then every role uses GROQ_MODEL. Limits are configuration, read from the
Groq console and supplied through LLM_LIMITS; nothing here hard-codes a model
or a number.

Spring comparison: a typed @ConfigurationProperties-backed registry bean that
other components ask for "the model for role X" instead of reading config.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, PositiveInt

if TYPE_CHECKING:
    from app.config import Settings


class LlmRole(StrEnum):
    """What a model call is for. Each role can have its own model (Phase 6)."""

    CLASSIFIER = "classifier"
    SQL_GENERATOR = "sql_generator"
    SQL_REPAIR = "sql_repair"
    SYNTHESIZER = "synthesizer"
    JUDGE = "judge"


class ModelLimits(BaseModel):
    """Groq free-tier limits for one model, as shown in the console.

    rpm/tpm are per minute, rpd/tpd per day. They change over time, so they are
    configuration: re-read the console and update LLM_LIMITS when they do.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    rpm: PositiveInt
    rpd: PositiveInt
    tpm: PositiveInt
    tpd: PositiveInt

    def with_margin(self, margin: float) -> ModelLimits:
        """The share of each limit the client may use; never below 1."""
        if not 0 < margin <= 1:
            raise ValueError("margin must be in (0, 1]")
        return ModelLimits(
            rpm=max(1, int(self.rpm * margin)),
            rpd=max(1, int(self.rpd * margin)),
            tpm=max(1, int(self.tpm * margin)),
            tpd=max(1, int(self.tpd * margin)),
        )


@dataclass(frozen=True)
class ModelRegistry:
    """Answers: which model serves a role, what is its fallback, what are its limits."""

    default_model: str
    fallback_model: str | None
    limits: Mapping[str, ModelLimits]
    role_models: Mapping[LlmRole, str] = field(default_factory=dict)

    @classmethod
    def from_settings(cls, settings: Settings) -> ModelRegistry:
        return cls(
            default_model=settings.groq_model,
            fallback_model=settings.groq_fallback_model,
            limits=dict(settings.llm_limits),
            role_models=dict(settings.llm_role_models),
        )

    def model_for(self, role: LlmRole) -> str:
        return self.role_models.get(role, self.default_model)

    def fallback_for(self, role: LlmRole) -> str | None:
        """The fallback model, unless it is the model already serving the role."""
        if self.fallback_model is None or self.fallback_model == self.model_for(role):
            return None
        return self.fallback_model

    def limits_for(self, model: str) -> ModelLimits:
        try:
            return self.limits[model]
        except KeyError:
            raise KeyError(f"no limits configured for model {model!r}") from None

    def models(self) -> frozenset[str]:
        """Every model this configuration can call."""
        used = {self.default_model, *self.role_models.values()}
        if self.fallback_model is not None:
            used.add(self.fallback_model)
        return frozenset(used)
