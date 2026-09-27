"""Response-quality judge: grades one agent answer on six criteria (Section 10c).

The judge sees the question, the SQL that ran, the result rows (a sample of at
most JUDGE_ROW_LIMIT, with the full count), the partial-result flags and the
answer, and returns a score from 1 to 5 with a short reason per criterion.

Rules (Section 10d, D43):
- Temperature 0; the reply must be one JSON object that validates against
  Verdict, strictly: integer scores, no missing or extra keys. An invalid reply
  is recorded as a parse failure, never repaired or re-asked, because the
  judge's format compliance is itself measured.
- Output is capped at JUDGE_MAX_TOKENS on every call (D64).
- The call goes through the same gateway as every other model call (rate
  limiter, cache, call log) under the `judge` role.
- The case is sent as JSON, so nothing inside an answer can break out of it.
- No fallback model: switching judges mid-run would mix two instruments.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from string import Template
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.llm.registry import LlmRole
from app.prompts import SCHEMA_DESCRIPTION
from app.prompts.loader import prompt_id

RUBRIC_DIR = Path(__file__).parent
RUBRIC_NAME = "response"
RUBRIC_VERSION = 1
JUDGE_ROW_LIMIT = 50
# Output cap for every judge call (D64). Groq enforces an output-tokens-per-minute
# limit (qwen/qwen3.8-27b free tier: 1,000) and counts a call without max_tokens as
# 2,048 expected output tokens, so such calls are refused with a 429. 800 is 2.7x
# the largest verdict seen in 88 judge calls (294 tokens) and stays under 1,000.
JUDGE_MAX_TOKENS = 800
CRITERIA = ("faithfulness", "relevance", "completeness", "honesty", "sql_intent", "clarity")


def _render_rubric(version: int) -> str:
    text = (RUBRIC_DIR / f"{RUBRIC_NAME}.v{version}.md").read_text(encoding="utf-8")
    if not text.endswith("\n"):
        raise ValueError(f"{RUBRIC_NAME}.v{version}.md must end with a newline")
    template = Template(text[:-1])
    if not template.is_valid() or set(template.get_identifiers()) != {"schema"}:
        raise ValueError(f"{RUBRIC_NAME}.v{version}.md must use exactly one placeholder, $schema")
    return template.substitute(schema=SCHEMA_DESCRIPTION)


JUDGE_SYSTEM_PROMPT = _render_rubric(RUBRIC_VERSION)
JUDGE_PROMPT_ID = prompt_id(f"judge_{RUBRIC_NAME}", JUDGE_SYSTEM_PROMPT)


class CriterionScore(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    score: int = Field(ge=1, le=5)
    reason: str = Field(min_length=1, max_length=400)


class Verdict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    faithfulness: CriterionScore
    relevance: CriterionScore
    completeness: CriterionScore
    honesty: CriterionScore
    sql_intent: CriterionScore
    clarity: CriterionScore

    def scores(self) -> dict[str, int]:
        return {name: getattr(self, name).score for name in CRITERIA}

    def mean(self) -> float:
        return sum(self.scores().values()) / len(CRITERIA)


class JudgeParseError(ValueError):
    """The judge's reply is not one valid Verdict JSON object."""


_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def parse_verdict(text: str) -> Verdict:
    """Validate the reply. Tolerates a reasoning block, a code fence and text
    around the object; nothing else is lenient."""
    body = _THINK.sub("", text).strip()
    fenced = _FENCE.search(body)
    if fenced:
        body = fenced.group(1).strip()
    start, end = body.find("{"), body.rfind("}")
    if start == -1 or end < start:
        raise JudgeParseError("no JSON object in the reply")
    try:
        return Verdict.model_validate_json(body[start : end + 1])
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        raise JudgeParseError(f"invalid verdict: {', '.join(fields) or 'malformed JSON'}") from None


@dataclass(frozen=True)
class JudgeCase:
    """One answered question, as the judge sees it."""

    question: str
    sql: str
    columns: list[str]
    rows: list[list[Any]]
    answer: str
    truncated: bool = False
    limit_reached: bool = False
    earlier_questions: list[str] = field(default_factory=list)


def build_judge_messages(case: JudgeCase) -> list[dict[str, str]]:
    shown = case.rows[:JUDGE_ROW_LIMIT]
    payload = {
        "earlier_questions": case.earlier_questions,
        "question": case.question,
        "sql": case.sql,
        "result": {
            "columns": case.columns,
            "row_count": len(case.rows),
            "rows_shown": len(shown),
            "rows": shown,
        },
        "truncated": case.truncated,
        "limit_reached": case.limit_reached,
        "answer": case.answer,
    }
    return [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
    ]


class _Reply(Protocol):
    content: str
    input_tokens: int
    output_tokens: int


class _Llm(Protocol):
    def complete(self, role: LlmRole, messages: Any, **kwargs: Any) -> _Reply: ...


@dataclass(frozen=True)
class JudgeResult:
    verdict: Verdict | None
    error: str | None
    raw: str
    tokens: int
    cached: bool = False  # answered from the response cache: no call, no quota spent


def judge_response(
    llm: _Llm, case: JudgeCase, *, schema_hash: str, request_id: str | None = None
) -> JudgeResult:
    """One judge call. Provider errors propagate (the runner stops on them);
    an invalid reply is returned as a parse failure. schema_hash is required, so
    no judge call can go unrecorded against its schema (D54)."""
    reply = llm.complete(
        LlmRole.JUDGE,
        build_judge_messages(case),
        temperature=0,
        max_tokens=JUDGE_MAX_TOKENS,
        prompt_id=JUDGE_PROMPT_ID,
        schema_hash=schema_hash,
        request_id=request_id,
    )
    tokens = reply.input_tokens + reply.output_tokens
    cached = bool(getattr(reply, "cache_hit", False))
    try:
        return JudgeResult(parse_verdict(reply.content), None, reply.content, tokens, cached)
    except JudgeParseError as exc:
        return JudgeResult(None, str(exc), reply.content, tokens, cached)
