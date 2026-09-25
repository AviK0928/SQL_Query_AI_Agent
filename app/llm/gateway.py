"""What the agent calls: cache and token accounting around the resilient client.

LlmClient owns resilience (rate limits, retries, fallback). The gateway adds
cross-cutting concerns without touching that policy: a cache lookup before the
client runs, a cache write after it succeeds, and usage tracking for both.
Step 4.5 adds the LLM call log here, where cache hits are visible too.
Spring comparison: a decorator bean wrapping the service.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence

from app.llm.budget import UsageTracker
from app.llm.cache import CachedCompletion, ResponseCache, cache_key
from app.llm.client import LlmClient, LlmResult
from app.llm.registry import LlmRole


class LlmGateway:
    def __init__(
        self,
        client: LlmClient,
        *,
        cache: ResponseCache | None = None,
        usage: UsageTracker | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.cache = cache
        self.usage = usage
        self.clock = clock

    def complete(
        self,
        role: LlmRole,
        messages: Sequence[Mapping[str, str]],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        prompt_id: str = "unversioned",
        schema_hash: str = "",
    ) -> LlmResult:
        """Only temperature-0 calls are cached: at higher temperatures a stored
        answer would defeat the sampling."""
        started = self.clock()
        cacheable = self.cache is not None and temperature == 0

        def key(model: str) -> str:
            return cache_key(model, prompt_id, messages, temperature, max_tokens, schema_hash)

        if cacheable and self.cache is not None:
            primary = self.client.registry.model_for(role)
            hit = self.cache.get(key(primary))
            if hit is not None:
                return self._track(
                    LlmResult(
                        content=hit.content,
                        role=role,
                        model=primary,
                        input_tokens=hit.input_tokens,
                        output_tokens=hit.output_tokens,
                        attempts=0,
                        fallback_used=False,
                        latency_ms=int((self.clock() - started) * 1000),
                        cache_hit=True,
                    )
                )

        result = self.client.complete(
            role, messages, temperature=temperature, max_tokens=max_tokens
        )
        if cacheable and self.cache is not None:
            # Keyed to the model that answered: a fallback answer never masks the primary.
            self.cache.put(
                key(result.model),
                CachedCompletion(result.content, result.input_tokens, result.output_tokens),
            )
        return self._track(result)

    def _track(self, result: LlmResult) -> LlmResult:
        if self.usage is not None:
            self.usage.add(result)
        return result
