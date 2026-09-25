"""Response cache for LLM calls: a SQLite file, used only when LLM_CACHE_PATH is set.

Keyed by a SHA-256 of everything that determines a temperature-0 answer: model,
prompt id, exact messages, temperature, max_tokens and schema hash (Section 8).
Stores the key, the response text and token counts; never the prompt itself.
Evals and repeated dev runs therefore never spend quota twice on the same call.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CachedCompletion:
    content: str
    input_tokens: int
    output_tokens: int


def cache_key(
    model: str,
    prompt_id: str,
    messages: Sequence[Mapping[str, str]],
    temperature: float,
    max_tokens: int | None,
    schema_hash: str,
) -> str:
    payload = json.dumps(
        {
            "model": model,
            "prompt_id": prompt_id,
            "messages": [dict(m) for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "schema_hash": schema_hash,
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


class ResponseCache:
    """One SQLite file; a short-lived connection per operation, so it is thread-safe."""

    def __init__(self, path: Path, clock: Callable[[], float] = time.time) -> None:
        self.path = Path(path)
        self.clock = clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as con, con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS responses ("
                " key TEXT PRIMARY KEY, content TEXT NOT NULL,"
                " input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL,"
                " created_at REAL NOT NULL)"
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)

    def get(self, key: str) -> CachedCompletion | None:
        with closing(self._connect()) as con:
            row = con.execute(
                "SELECT content, input_tokens, output_tokens FROM responses WHERE key = ?", (key,)
            ).fetchone()
        return CachedCompletion(str(row[0]), int(row[1]), int(row[2])) if row else None

    def put(self, key: str, completion: CachedCompletion) -> None:
        with closing(self._connect()) as con, con:
            con.execute(
                "INSERT OR REPLACE INTO responses VALUES (?, ?, ?, ?, ?)",
                (
                    key,
                    completion.content,
                    completion.input_tokens,
                    completion.output_tokens,
                    self.clock(),
                ),
            )

    def __len__(self) -> int:
        with closing(self._connect()) as con:
            return int(con.execute("SELECT COUNT(*) FROM responses").fetchone()[0])
