"""Builds the production LLM stack and totals per-question usage."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agent.replies import NO_USAGE
from app.llm.cache import ResponseCache
from app.llm.calllog import CallLogger
from app.llm.client import LlmClient, build_groq_transport
from app.llm.gateway import LlmGateway
from app.llm.limiter import RateLimiter
from app.llm.registry import ModelRegistry

if TYPE_CHECKING:
    from app.config import Settings


def build_llm(settings: Settings) -> LlmGateway:
    """The production LLM stack, configured only from Settings (never os.environ):
    Groq transport -> LlmClient (rate limiter, retries, fallback) -> LlmGateway
    (cache when LLM_CACHE_PATH is set, call log). Builds objects only; no network."""
    registry = ModelRegistry.from_settings(settings)
    client = LlmClient(
        registry,
        build_groq_transport(settings.groq_api_key.get_secret_value(), settings.llm_timeout_s),
        max_attempts=settings.llm_max_attempts,
        max_wait_s=settings.llm_max_wait_s,
        limiter=RateLimiter(
            registry, margin=settings.llm_safety_margin, max_wait_s=settings.llm_max_wait_s
        ),
    )
    cache = ResponseCache(settings.llm_cache_path) if settings.llm_cache_path else None
    return LlmGateway(client, cache=cache, call_log=CallLogger.from_settings(settings))


def _add_usage(usage: dict[str, int] | None, result: Any) -> dict[str, int]:
    total = dict(usage or NO_USAGE)
    if getattr(result, "cache_hit", False):
        total["cache_hits"] += 1
    else:
        total["calls"] += 1
        total["input_tokens"] += getattr(result, "input_tokens", 0)
        total["output_tokens"] += getattr(result, "output_tokens", 0)
    return total
