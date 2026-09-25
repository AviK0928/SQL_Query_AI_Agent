"""Client-side rate limiting per model: token buckets plus daily budgets.

Each model gets two token buckets sized from its configured limits times
LLM_SAFETY_MARGIN: requests per minute and tokens per minute, refilled
continuously. A caller reserves capacity under a lock (a bucket may go briefly
negative) and then sleeps for the deficit outside the lock, so concurrent
requests queue fairly. If the wait would exceed LLM_MAX_WAIT_S the limiter
raises RateLimitedError locally, without a network call, and the client's
existing policy takes over (fallback model, or LLM_RATE_LIMITED).

Groq's own view wins when it disagrees: a 429 blocks the model for every
caller, `x-ratelimit-remaining-tokens` caps the token bucket, and
`x-ratelimit-remaining-requests` (per day) raises the daily request count.
Daily counters are in memory and reset on restart (README L7).
Spring comparison: Resilience4j RateLimiter, one instance per downstream model.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from app.llm.client import RateLimitedError, RequestError
from app.llm.registry import ModelLimits, ModelRegistry


def _utc_today() -> date:
    return datetime.now(UTC).date()


def _seconds_to_utc_midnight() -> float:
    now = datetime.now(UTC)
    midnight = datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), UTC)
    return (midnight - now).total_seconds()


def _header_int(headers: Mapping[str, str], name: str) -> int | None:
    try:
        return int(float(headers[name]))
    except (KeyError, ValueError):
        return None


@dataclass
class _ModelState:
    limits: ModelLimits  # already scaled by the safety margin
    reserve_tokens: int  # tpm headroom the margin keeps back
    request_level: float
    token_level: float
    updated: float
    day: date
    requests_today: int = 0
    tokens_today: int = 0
    blocked_until: float = 0.0


class RateLimiter:
    """Per-model request and token budgets, shared by every caller in the process."""

    def __init__(
        self,
        registry: ModelRegistry,
        *,
        margin: float,
        max_wait_s: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        today: Callable[[], date] = _utc_today,
        seconds_to_midnight: Callable[[], float] = _seconds_to_utc_midnight,
    ) -> None:
        self.max_wait_s = max_wait_s
        self.clock = clock
        self.sleep = sleep
        self.today = today
        self.seconds_to_midnight = seconds_to_midnight
        self._lock = threading.Lock()
        now = clock()
        self._state: dict[str, _ModelState] = {}
        for model in registry.models():
            raw = registry.limits_for(model)
            scaled = raw.with_margin(margin)
            self._state[model] = _ModelState(
                limits=scaled,
                reserve_tokens=raw.tpm - scaled.tpm,
                request_level=float(scaled.rpm),
                token_level=float(scaled.tpm),
                updated=now,
                day=today(),
            )

    # --- public API -----------------------------------------------------------

    def acquire(self, model: str, est_tokens: int) -> float:
        """Reserve one request and est_tokens; sleep if needed. Returns seconds waited."""
        with self._lock:
            st = self._refresh(model)
            lim = st.limits
            if est_tokens > lim.tpm:
                raise RequestError(
                    f"estimated {est_tokens} tokens exceed the per-minute budget of {lim.tpm}"
                )
            if st.requests_today + 1 > lim.rpd or st.tokens_today + est_tokens > lim.tpd:
                raise RateLimitedError(self.seconds_to_midnight())
            now = self.clock()
            wait = max(
                (1 - st.request_level) / (lim.rpm / 60) if st.request_level < 1 else 0.0,
                (est_tokens - st.token_level) / (lim.tpm / 60)
                if st.token_level < est_tokens
                else 0.0,
                st.blocked_until - now,
                0.0,
            )
            if wait > self.max_wait_s:
                raise RateLimitedError(wait)
            st.request_level -= 1
            st.token_level -= est_tokens
            st.requests_today += 1
            st.tokens_today += est_tokens
        if wait > 0:
            self.sleep(wait)
        return wait

    def record(
        self, model: str, est_tokens: int, actual_tokens: int, headers: Mapping[str, str]
    ) -> None:
        """Correct the reservation with real usage and Groq's rate-limit headers."""
        with self._lock:
            st = self._refresh(model)
            st.token_level += est_tokens - actual_tokens
            st.tokens_today += actual_tokens - est_tokens
            remaining_tokens = _header_int(headers, "x-ratelimit-remaining-tokens")
            if remaining_tokens is not None:
                st.token_level = min(st.token_level, float(remaining_tokens - st.reserve_tokens))
            remaining_requests = _header_int(headers, "x-ratelimit-remaining-requests")
            limit_requests = _header_int(headers, "x-ratelimit-limit-requests")
            if remaining_requests is not None and limit_requests is not None:
                st.requests_today = max(st.requests_today, limit_requests - remaining_requests)

    def on_rate_limited(self, model: str, retry_after: float | None) -> None:
        """Groq said 429: block this model for every caller until retry-after passes."""
        with self._lock:
            st = self._refresh(model)
            st.token_level = min(st.token_level, 0.0)
            if retry_after is not None:
                st.blocked_until = max(st.blocked_until, self.clock() + retry_after)

    # --- internals ------------------------------------------------------------

    def _refresh(self, model: str) -> _ModelState:
        try:
            st = self._state[model]
        except KeyError:
            raise RequestError(f"no rate limits configured for model {model!r}") from None
        now = self.clock()
        elapsed = max(0.0, now - st.updated)
        st.request_level = min(
            float(st.limits.rpm), st.request_level + elapsed * st.limits.rpm / 60
        )
        st.token_level = min(float(st.limits.tpm), st.token_level + elapsed * st.limits.tpm / 60)
        st.updated = now
        today = self.today()
        if today != st.day:
            st.day, st.requests_today, st.tokens_today = today, 0, 0
        return st
