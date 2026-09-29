"""Structured LLM call log: one JSON line per call, success or failure.

This log is the project's view of what the app asks the LLM and what it gets
back (D56): every call's role (which step made it), model, prompt id, tokens,
latency, retries, cache hit and outcome, and with LLM_LOG_CONTENT=true the
messages sent and the reply received. Its `request_id` is the id created at
HTTP entry (D55), so one question's calls can be read together.

Field names follow the OpenTelemetry GenAI conventions where they fit
(gen_ai.request.model, gen_ai.usage.input_tokens, error.type, ...), a common
vocabulary for LLM logs; project fields use an `llm.` prefix.

Prompt and response text are included only when LLM_LOG_CONTENT is true. Each
message and the reply are clipped to LLM_LOG_MAX_CHARS characters, with a
marker saying how much was cut, so one oversized prompt cannot flood the log.
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
DEFAULT_MAX_CHARS = 4000  # the Settings default; no committed eval call has a longer message


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
        max_chars: int = DEFAULT_MAX_CHARS,
        path: Path | None = None,
        secrets: Iterable[str] = (),
        sink: Callable[[str], None] | None = None,
    ) -> None:
        self.log_content = log_content
        self.max_chars = max_chars
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
            max_chars=settings.llm_log_max_chars,
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
            record["gen_ai.output.text"] = self._clip(result.content)
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
            record["gen_ai.input.messages"] = [
                {**m, "content": self._clip(m.get("content", ""))} for m in ctx.messages
            ]
        return record

    def _clip(self, text: str) -> str:
        """The text, or its first max_chars characters plus a marker of what was cut.

        Secrets are redacted before the cut: a key split by the cut would no
        longer match the whole-line redaction in _write, and its prefix would leak.
        """
        for secret in self.secrets:
            text = text.replace(secret, REDACTED)
        cut = len(text) - self.max_chars
        return text if cut <= 0 else f"{text[: self.max_chars]}...[clipped {cut} chars]"

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
