"""Structured LLM call log: one JSON line per call, success or failure.

Field names follow the OpenTelemetry GenAI conventions where they fit
(gen_ai.request.model, gen_ai.usage.input_tokens, error.type, ...) so Phase 9
tracing can map onto them; project fields use an `llm.` prefix.

Prompt and response text are included only when LLM_LOG_CONTENT is true.
Every record is scrubbed of configured secret values (the API key) before it
is written, whatever field they appear in. Output goes to a JSONL file when
LLM_LOG_PATH is set, otherwise to stdout (what Render's log viewer shows).
"""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from app.llm.registry import LlmRole

if TYPE_CHECKING:
    from app.config import Settings
    from app.llm.client import LlmResult

# The only response headers worth keeping: Groq's rate-limit state.
LOGGED_HEADERS = (
    "x-ratelimit-remaining-requests",
    "x-ratelimit-remaining-tokens",
    "retry-after",
)
REDACTED = "***"


@dataclass(frozen=True)
class CallContext:
    """What was asked for, independent of how the call turned out."""

    role: LlmRole
    messages: Sequence[Mapping[str, str]]
    request_model: str
    temperature: float
    max_tokens: int | None
    prompt_id: str
    schema_hash: str
    request_id: str | None


class CallLogger:
    def __init__(
        self,
        *,
        log_content: bool = False,
        path: Path | None = None,
        secrets: Iterable[str] = (),
        sink: Callable[[str], None] | None = None,
    ) -> None:
        self.log_content = log_content
        self.path = Path(path) if path is not None else None
        self.secrets = tuple(s for s in secrets if s)
        self._lock = threading.Lock()
        self._sink = sink or self._default_sink
        if self.path is not None:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_settings(cls, settings: Settings) -> CallLogger:
        return cls(
            log_content=settings.llm_log_content,
            path=settings.llm_log_path,
            secrets=[settings.groq_api_key.get_secret_value()],
        )

    # --- records ------------------------------------------------------------

    def success(self, result: LlmResult, ctx: CallContext) -> None:
        record = self._base(ctx)
        record.update(
            {
                "gen_ai.response.model": result.model,
                "gen_ai.usage.input_tokens": result.input_tokens,
                "gen_ai.usage.output_tokens": result.output_tokens,
                "llm.attempts": result.attempts,
                "llm.fallback_used": result.fallback_used,
                "llm.cache_hit": result.cache_hit,
                "llm.latency_ms": result.latency_ms,
                "llm.rate_limit": {
                    h: result.headers[h] for h in LOGGED_HEADERS if h in result.headers
                },
            }
        )
        if self.log_content:
            record["gen_ai.output.text"] = result.content
        self._write(record)

    def failure(self, error_type: str, detail: str, ctx: CallContext, latency_ms: int) -> None:
        record = self._base(ctx)
        record.update(
            {"error.type": error_type, "error.detail": detail, "llm.latency_ms": latency_ms}
        )
        self._write(record)

    # --- internals ------------------------------------------------------------

    def _base(self, ctx: CallContext) -> dict[str, Any]:
        record: dict[str, Any] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds"),
            "event": "llm.call",
            "request_id": ctx.request_id,
            "gen_ai.system": "groq",
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": ctx.request_model,
            "gen_ai.request.temperature": ctx.temperature,
            "gen_ai.request.max_tokens": ctx.max_tokens,
            "llm.role": ctx.role.value,
            "llm.prompt_id": ctx.prompt_id,
            "llm.schema_hash": ctx.schema_hash,
            "llm.messages": len(ctx.messages),
            "llm.input_chars": sum(len(m.get("content", "")) for m in ctx.messages),
        }
        if self.log_content:
            record["gen_ai.input.messages"] = [dict(m) for m in ctx.messages]
        return record

    def _write(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, ensure_ascii=False, default=str)
        for secret in self.secrets:
            line = line.replace(secret, REDACTED)
        with self._lock:
            self._sink(line)

    def _default_sink(self, line: str) -> None:
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        else:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
