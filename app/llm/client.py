"""Hardened Groq client: retries, retry-after, fallback, typed errors.

Two layers:
- a Transport makes exactly one call and maps provider errors onto a small set
  of TransportError types (the only code that knows about the groq SDK);
- LlmClient adds the policy: tenacity retries on 429/5xx/timeouts, retry-after
  honoured up to LLM_MAX_WAIT_S, and a fallback model on a persistent 429 or a
  retired model (Section 8). Everything else fails fast with an LlmError.

The SDK's own retries are disabled (max_retries=0) so tenacity is the only retry
layer and no 429 is retried twice without us knowing.
Spring comparison: a WebClient wrapped in Resilience4j Retry plus a fallback,
with sleep and clock injected so tests never wait.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    stop_any,
    wait_exponential_jitter,
)
from tenacity.stop import stop_base

from app.llm.registry import LlmRole, ModelRegistry

if TYPE_CHECKING:  # type hints only: limiter.py imports this module's errors
    from app.llm.limiter import RateLimiter

logger = logging.getLogger(__name__)

Message = Mapping[str, str]


# --- errors ------------------------------------------------------------------


class LlmErrorCode(StrEnum):
    RATE_LIMITED = "LLM_RATE_LIMITED"
    TIMEOUT = "LLM_TIMEOUT"
    UNAVAILABLE = "LLM_UNAVAILABLE"
    MODEL_UNAVAILABLE = "LLM_MODEL_UNAVAILABLE"
    BAD_REQUEST = "LLM_BAD_REQUEST"


class LlmError(Exception):
    """A model call failed after the retry and fallback policy ran out."""

    def __init__(self, code: LlmErrorCode, detail: str) -> None:
        if not isinstance(detail, str) or not detail.strip():
            raise ValueError("LlmError needs a non-empty detail message")
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class TransportError(Exception):
    """Provider failure, normalised. Raised by transports only."""


class RateLimitedError(TransportError):
    def __init__(self, retry_after: float | None) -> None:
        super().__init__(f"rate limited (retry-after={retry_after})")
        self.retry_after = retry_after


class ServerError(TransportError):
    """5xx or a connection failure: worth retrying."""


class CallTimeoutError(TransportError):
    """The HTTP call exceeded LLM_TIMEOUT_S."""


class ModelGoneError(TransportError):
    """The model is decommissioned or unknown: retrying cannot help."""


class RequestError(TransportError):
    """Any other 4xx: the request itself is wrong; retrying cannot help."""


RETRYABLE = (RateLimitedError, ServerError, CallTimeoutError)


class _StopIfWaitTooLong(stop_base):
    """tenacity stop condition: give up on this model when retry-after is too long.

    A class because tenacity's typed combinators (stop_any) accept stop_base
    instances, not plain functions.
    """

    def __init__(self, check: Callable[[RetryCallState], bool]) -> None:
        self.check = check

    def __call__(self, retry_state: RetryCallState) -> bool:
        return self.check(retry_state)


# --- transport ---------------------------------------------------------------


@dataclass(frozen=True)
class RawCompletion:
    content: str
    input_tokens: int
    output_tokens: int
    headers: Mapping[str, str] = field(default_factory=dict)


class Transport(Protocol):
    def __call__(
        self, model: str, messages: Sequence[Message], temperature: float, max_tokens: int | None
    ) -> RawCompletion: ...


def parse_retry_after(value: str | None) -> float | None:
    """Seconds from a retry-after header, or None when absent or unreadable."""
    if value is None:
        return None
    try:
        seconds = float(value)
    except ValueError:
        return None
    return seconds if seconds >= 0 else None


def map_sdk_error(exc: Exception) -> TransportError:
    """Translate a groq SDK exception into a TransportError."""
    import groq

    if isinstance(exc, groq.RateLimitError):
        return RateLimitedError(parse_retry_after(exc.response.headers.get("retry-after")))
    if isinstance(exc, groq.APITimeoutError):  # subclass of APIConnectionError: check first
        return CallTimeoutError(str(exc) or "timeout")
    if isinstance(exc, groq.APIConnectionError):
        return ServerError(str(exc) or "connection error")
    if isinstance(exc, groq.APIStatusError):
        text = f"{exc} {exc.body}".lower()
        if exc.status_code == 404 or "decommission" in text or "model_not_found" in text:
            return ModelGoneError(f"HTTP {exc.status_code}: model unavailable")
        if exc.status_code >= 500:
            return ServerError(f"HTTP {exc.status_code}")
        return RequestError(f"HTTP {exc.status_code}")
    return RequestError(type(exc).__name__)


def groq_transport(client: Any) -> Transport:
    """A Transport over a groq.Groq client built with max_retries=0."""

    def call(
        model: str, messages: Sequence[Message], temperature: float, max_tokens: int | None
    ) -> RawCompletion:
        import groq

        try:
            raw = client.chat.completions.with_raw_response.create(
                model=model, messages=list(messages), temperature=temperature, max_tokens=max_tokens
            )
        except groq.APIError as exc:
            raise map_sdk_error(exc) from None
        completion = raw.parse()
        usage = completion.usage
        return RawCompletion(
            content=completion.choices[0].message.content or "",
            input_tokens=int(usage.prompt_tokens) if usage else 0,
            output_tokens=int(usage.completion_tokens) if usage else 0,
            headers={k.lower(): v for k, v in raw.headers.items()},
        )

    return call


def build_groq_transport(api_key: str, timeout_s: float) -> Transport:
    from groq import Groq

    return groq_transport(Groq(api_key=api_key, max_retries=0, timeout=timeout_s))


# Assumed completion size when a call sets no max_tokens (measured and tuned in Phase 6).
DEFAULT_COMPLETION_TOKENS = 512


def estimate_tokens(messages: Sequence[Message], max_tokens: int | None) -> int:
    """Rough pre-call estimate: about 4 characters per token, plus the completion budget.

    Used only to reserve rate-limit capacity; the real usage corrects it afterwards.
    """
    chars = sum(len(m.get("content", "")) for m in messages)
    budget = max_tokens if max_tokens is not None else DEFAULT_COMPLETION_TOKENS
    return chars // 4 + budget


# --- client ------------------------------------------------------------------


@dataclass(frozen=True)
class LlmResult:
    content: str
    role: LlmRole
    model: str
    input_tokens: int
    output_tokens: int
    attempts: int  # across all models tried
    fallback_used: bool
    latency_ms: int
    headers: Mapping[str, str] = field(default_factory=dict)


class LlmClient:
    """Calls the model for a role, with retries and a fallback model."""

    def __init__(
        self,
        registry: ModelRegistry,
        transport: Transport,
        *,
        max_attempts: int,
        max_wait_s: float,
        max_backoff_s: float = 8.0,
        limiter: RateLimiter | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.registry = registry
        self.transport = transport
        self.max_attempts = max_attempts
        self.max_wait_s = max_wait_s
        self.max_backoff_s = max_backoff_s
        self.limiter = limiter
        self.sleep = sleep
        self.clock = clock
        self._backoff = wait_exponential_jitter(initial=0.5, max=max_backoff_s)

    def complete(
        self,
        role: LlmRole,
        messages: Sequence[Message],
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ) -> LlmResult:
        primary = self.registry.model_for(role)
        fallback = self.registry.fallback_for(role)
        started = self.clock()
        attempts = 0
        last: TransportError | None = None

        for model in [primary] + ([fallback] if fallback else []):
            if model != primary:
                logger.warning(
                    "llm fallback: role=%s from=%s to=%s reason=%s",
                    role.value,
                    primary,
                    model,
                    type(last).__name__,
                )
            counter = [0]
            try:
                raw = self._with_retries(model, messages, temperature, max_tokens, counter)
            except (RateLimitedError, ModelGoneError) as exc:
                attempts += counter[0]
                last = exc
                continue  # the only two reasons to try the fallback
            except TransportError as exc:
                attempts += counter[0]
                raise self._final_error(exc, model) from None
            attempts += counter[0]
            return LlmResult(
                content=raw.content,
                role=role,
                model=model,
                input_tokens=raw.input_tokens,
                output_tokens=raw.output_tokens,
                attempts=attempts,
                fallback_used=model != primary,
                latency_ms=int((self.clock() - started) * 1000),
                headers=raw.headers,
            )

        tried = primary if fallback is None else f"{primary}, {fallback}"
        raise self._final_error(last or RequestError("no model was tried"), tried)

    def _with_retries(
        self,
        model: str,
        messages: Sequence[Message],
        temperature: float,
        max_tokens: int | None,
        counter: list[int],
    ) -> RawCompletion:
        estimate = estimate_tokens(messages, max_tokens)

        def attempt() -> RawCompletion:
            if self.limiter is not None:
                # May sleep; raises RateLimitedError locally when the wait is too long.
                self.limiter.acquire(model, estimate)
            counter[0] += 1  # counts network calls only
            try:
                raw = self.transport(model, messages, temperature, max_tokens)
            except RateLimitedError as exc:
                if self.limiter is not None:
                    self.limiter.on_rate_limited(model, exc.retry_after)
                raise
            if self.limiter is not None:
                actual = raw.input_tokens + raw.output_tokens
                self.limiter.record(model, estimate, actual, raw.headers)
            return raw

        retrying = Retrying(
            retry=retry_if_exception_type(RETRYABLE),
            stop=stop_any(
                stop_after_attempt(self.max_attempts),
                _StopIfWaitTooLong(self._stop_if_wait_too_long),
            ),
            wait=self._wait,
            sleep=self.sleep,
            reraise=True,
        )
        result: RawCompletion = retrying(attempt)
        return result

    def _retry_after(self, state: RetryCallState) -> float | None:
        exc = state.outcome.exception() if state.outcome else None
        return exc.retry_after if isinstance(exc, RateLimitedError) else None

    def _stop_if_wait_too_long(self, state: RetryCallState) -> bool:
        wait = self._retry_after(state)
        return wait is not None and wait > self.max_wait_s

    def _wait(self, state: RetryCallState) -> float:
        wait = self._retry_after(state)
        return wait if wait is not None else float(self._backoff(state))

    @staticmethod
    def _final_error(exc: TransportError, model: str) -> LlmError:
        if isinstance(exc, RateLimitedError):
            return LlmError(LlmErrorCode.RATE_LIMITED, f"rate limited on {model}")
        if isinstance(exc, ModelGoneError):
            return LlmError(LlmErrorCode.MODEL_UNAVAILABLE, f"model unavailable: {model}")
        if isinstance(exc, CallTimeoutError):
            return LlmError(LlmErrorCode.TIMEOUT, f"timed out calling {model}")
        if isinstance(exc, ServerError):
            return LlmError(LlmErrorCode.UNAVAILABLE, f"provider error calling {model}")
        return LlmError(LlmErrorCode.BAD_REQUEST, f"request rejected by {model}: {exc}")
