"""The agent's typed state: what one question carries through the graph."""

from __future__ import annotations

from typing import TypedDict

from app.sql.validator import ValidatedQuery


class AgentState(TypedDict, total=False):
    question: str
    history: list
    sql: str | None  # latest SQL from the model (raw until validated)
    validated: ValidatedQuery | None  # set only when validation passed
    columns: list
    rows: list
    truncated: bool
    limit_reached: bool
    error: str | None  # an SqlErrorCode value
    error_detail: str | None  # for the retry prompt only; never shown to users
    repairable: bool
    retry_count: int
    out_of_scope: bool
    answer: str
    request_id: str
    blocked: bool  # guard_input rejected the question; no model call was made
    reply: str  # the generator's raw reply, before classification
    needs_clarification: bool  # the answer is a clarifying question
    max_repairs: int  # MAX_REPAIR_ATTEMPTS for this question
    usage: dict[str, int]  # tokens for this question only
