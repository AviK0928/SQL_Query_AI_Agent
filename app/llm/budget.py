"""Token accounting: what each model call cost, and what the cache saved.

One UsageTracker per scope that needs a total: a request (the agent, step 4.6)
or an eval run (Phase 6). Thread-safe, since FastAPI runs requests on a pool.
"""

from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.llm.client import LlmResult


@dataclass
class ModelUsage:
    calls: int = 0  # answers that needed the network
    cache_hits: int = 0
    input_tokens: int = 0  # spent
    output_tokens: int = 0  # spent
    tokens_saved: int = 0  # what the cache hits would have cost


class UsageTracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._by_model: dict[str, ModelUsage] = {}

    def add(self, result: LlmResult) -> None:
        with self._lock:
            usage = self._by_model.setdefault(result.model, ModelUsage())
            if result.cache_hit:
                usage.cache_hits += 1
                usage.tokens_saved += result.input_tokens + result.output_tokens
            else:
                usage.calls += 1
                usage.input_tokens += result.input_tokens
                usage.output_tokens += result.output_tokens

    def snapshot(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {model: asdict(usage) for model, usage in sorted(self._by_model.items())}

    @property
    def tokens_spent(self) -> int:
        with self._lock:
            return sum(u.input_tokens + u.output_tokens for u in self._by_model.values())
