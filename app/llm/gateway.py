"""What the agent calls: cache, token accounting and the call log around the
resilient client.

LlmClient owns resilience (rate limits, retries, fallback). The gateway adds
cross-cutting concerns without touching that policy: a cache lookup before the
client runs, a cache write after it succeeds, usage tracking, and one call-log
record per call, including cache hits and failures.
Spring comparison: a decorator bean wrapping the service.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence

from app.llm.budget import UsageTracker
from app.llm.cache import CachedCompletion, ResponseCache, cache_key
from app.llm.calllog import CallContext, CallLogger
from app.llm.client import LlmClient, LlmError, LlmResult
from app.llm.registry import LlmRole


class LlmGateway:
    def __init__(
        self,
        client: LlmClient,
        *,
        cache: ResponseCache | None = None,
        usage: UsageTracker | None = None,
        call_log: CallLogger | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.client = client
        self.cache = cache
        self.usage = usage
        self.call_log = call_log
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
        request_id: str | None = None,
    ) -> LlmResult:
        """Only temperature-0 calls are cached: at higher temperatures a stored
        answer would defeat the sampling."""
        started = self.clock()
        primary = self.client.registry.model_for(role)
        cacheable = self.cache is not None and temperature == 0
        ctx = CallContext(
            role=role,
            messages=messages,
            request_model=primary,
            temperature=temperature,
            max_tokens=max_tokens,
            prompt_id=prompt_id,
            schema_hash=schema_hash,
            request_id=request_id,
        )

        def key(model: str) -> str:
            return cache_key(model, prompt_id, messages, temperature, max_tokens, schema_hash)

        if cacheable and self.cache is not None:
            hit = self.cache.get(key(primary))
            if hit is not None:
                return self._done(
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
                    ),
                    ctx,
                )

        try:
            result = self.client.complete(
                role, messages, temperature=temperature, max_tokens=max_tokens
            )
        except Exception as exc:
            if self.call_log is not None:
                error_type = exc.code.value if isinstance(exc, LlmError) else type(exc).__name__
                detail = exc.detail if isinstance(exc, LlmError) else "unexpected error"
                self.call_log.failure(error_type, detail, ctx, int((self.clock() - started) * 1000))
            raise

        if cacheable and self.cache is not None:
            # Keyed to the model that answered: a fallback answer never masks the primary.
            self.cache.put(
                key(result.model),
                CachedCompletion(result.content, result.input_tokens, result.output_tokens),
            )
        return self._done(result, ctx)

    def _done(self, result: LlmResult, ctx: CallContext) -> LlmResult:
        if self.usage is not None:
            self.usage.add(result)
        if self.call_log is not None:
            self.call_log.success(result, ctx)
        return result
