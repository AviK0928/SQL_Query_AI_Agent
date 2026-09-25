"""Unit tests for app/llm/registry.py."""

import pytest
from pydantic import ValidationError

from app.config import load_settings
from app.llm.registry import LlmRole, ModelLimits, ModelRegistry

LIMITS = ModelLimits(rpm=30, rpd=1000, tpm=8000, tpd=200000)


def registry(**overrides):
    base = {
        "default_model": "big",
        "fallback_model": "small",
        "limits": {"big": LIMITS, "small": LIMITS, "judge": LIMITS},
        "role_models": {},
    }
    return ModelRegistry(**{**base, **overrides})


def test_every_role_uses_the_default_model_without_overrides():
    reg = registry()
    assert {reg.model_for(role) for role in LlmRole} == {"big"}


def test_a_role_override_wins():
    reg = registry(role_models={LlmRole.JUDGE: "judge"})
    assert reg.model_for(LlmRole.JUDGE) == "judge"
    assert reg.model_for(LlmRole.SQL_GENERATOR) == "big"


def test_fallback_is_offered_for_a_role():
    assert registry().fallback_for(LlmRole.SQL_GENERATOR) == "small"


def test_no_fallback_when_it_is_the_model_already_serving_the_role():
    reg = registry(role_models={LlmRole.SYNTHESIZER: "small"})
    assert reg.fallback_for(LlmRole.SYNTHESIZER) is None
    assert reg.fallback_for(LlmRole.SQL_GENERATOR) == "small"


def test_no_fallback_configured():
    assert registry(fallback_model=None).fallback_for(LlmRole.SQL_GENERATOR) is None


def test_limits_are_looked_up_per_model():
    assert registry().limits_for("small") is LIMITS
    with pytest.raises(KeyError, match="no limits configured for model 'other'"):
        registry().limits_for("other")


def test_models_lists_everything_that_can_be_called():
    reg = registry(role_models={LlmRole.JUDGE: "judge"})
    assert reg.models() == {"big", "small", "judge"}
    assert registry(fallback_model=None).models() == {"big"}


def test_margin_scales_every_limit_and_never_reaches_zero():
    scaled = LIMITS.with_margin(0.8)
    assert (scaled.rpm, scaled.rpd, scaled.tpm, scaled.tpd) == (24, 800, 6400, 160000)
    tiny = ModelLimits(rpm=1, rpd=1, tpm=1, tpd=1).with_margin(0.5)
    assert (tiny.rpm, tiny.rpd, tiny.tpm, tiny.tpd) == (1, 1, 1, 1)


@pytest.mark.parametrize("margin", [0, -0.1, 1.01])
def test_margin_outside_zero_to_one_is_rejected(margin):
    with pytest.raises(ValueError, match="margin"):
        LIMITS.with_margin(margin)


def test_limits_are_immutable_and_strict():
    with pytest.raises(ValidationError):
        LIMITS.rpm = 1
    with pytest.raises(ValidationError):
        ModelLimits(rpm=30, rpd=1000, tpm=8000, tpd=200000, rph=5)


def test_registry_is_built_from_settings():
    settings = load_settings(
        env_file=None,
        groq_api_key="test-key-not-real",
        groq_model="big",
        groq_fallback_model="small",
        llm_limits={"big": LIMITS.model_dump(), "small": LIMITS.model_dump()},
        llm_role_models={"synthesizer": "small"},
    )
    reg = ModelRegistry.from_settings(settings)
    assert reg.model_for(LlmRole.SQL_GENERATOR) == "big"
    assert reg.model_for(LlmRole.SYNTHESIZER) == "small"
    assert reg.fallback_for(LlmRole.SYNTHESIZER) is None
    assert reg.limits_for("big") == LIMITS
