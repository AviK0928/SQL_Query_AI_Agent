"""LangGraph flow: question -> SQL -> validate -> execute -> answer.

Validation and execution go through the SQL safety layer in app/sql/: the
sqlglot validator (S1) and the read-only executor (S2). Failures arrive as
SqlSafetyError codes. Only repairable codes (a parse error, an unknown table or
column, an invalid LIMIT) earn the single retry; a forbidden write, a stacked
statement or a timeout is final, because retrying it spends a Groq request with
no chance of a safe, useful result.

The retry path increments retry_count, so a second failure can only route to
the answer node -- an infinite loop is structurally impossible, not merely
guarded against.

Every model call goes through the LLM gateway (app/llm/gateway.py) with a
role per node (sql_generator, sql_repair, synthesizer), the prompt id, the
schema hash and a request id shared by all calls for one question. An LlmError
from generation or repair becomes a fixed answer and an LLM_* error code; if
only the summary fails, the rows that were already fetched are still returned.

Dependencies (settings, LLM gateway, schema reader, executor) are passed to the
Agent constructor. There are no module-level clients or graphs; tests build an
Agent with a fake LLM instead of patching globals.
Spring comparison: constructor injection instead of static fields.
"""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, StateGraph

from app.db import Database
from app.llm.cache import ResponseCache
from app.llm.calllog import CallLogger
from app.llm.client import LlmClient, LlmError, LlmErrorCode, build_groq_transport
from app.llm.gateway import LlmGateway
from app.llm.limiter import RateLimiter
from app.llm.registry import LlmRole, ModelRegistry
from app.prompts import (
    ANSWER_PROMPT_ID,
    OUT_OF_SCOPE_TOKEN,
    READ_ONLY_TOKEN,
    RETRY_PROMPT_ID,
    SQL_PROMPT_ID,
    build_answer_messages,
    build_retry_messages,
    build_sql_messages,
)
from app.sql.errors import USER_MESSAGES, SqlErrorCode, SqlSafetyError
from app.sql.executor import ReadOnlyExecutor
from app.sql.validator import ValidatedQuery, validate_sql

if TYPE_CHECKING:
    from app.config import Settings

TEMPERATURE = 0

READ_ONLY_REPLY = (
    "I can only read from this database, not change it. "
    "Try asking a question about the existing data instead."
)

OUT_OF_SCOPE_REPLY = (
    "I can only answer questions about the e-commerce database "
    "(customers, products, orders and order items). Try asking about "
    "customers, sales or products."
)

LLM_ERROR_REPLIES = {
    LlmErrorCode.RATE_LIMITED: "The AI service is busy right now. Please try again in a minute.",
    LlmErrorCode.TIMEOUT: "The AI service took too long to respond. Please try again.",
    LlmErrorCode.UNAVAILABLE: "The AI service is unavailable right now. Please try again shortly.",
    LlmErrorCode.MODEL_UNAVAILABLE: "The AI model is unavailable. Please try again later.",
    LlmErrorCode.BAD_REQUEST: "That question could not be processed. Try a shorter or simpler one.",
}

SUMMARY_UNAVAILABLE_REPLY = "Here are the results; a summary couldn't be generated right now."

NO_USAGE = {"calls": 0, "cache_hits": 0, "input_tokens": 0, "output_tokens": 0}


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
    usage: dict[str, int]  # tokens for this question only


def _clean_sql(text):
    """Strip markdown fences and stray prose the model may add."""
    text = text.strip()
    fence = re.match(r"^```(?:sql)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    return text


def _error_state(exc: SqlSafetyError) -> dict[str, Any]:
    return {"error": exc.code.value, "error_detail": exc.detail, "repairable": exc.repairable}


def build_llm(settings: Settings) -> LlmGateway:
    """The production LLM stack, configured only from Settings (never os.environ):
    Groq transport -> LlmClient (rate limiter, retries, fallback) -> LlmGateway
    (cache when LLM_CACHE_PATH is set, call log). Builds objects only; no network."""
    registry = ModelRegistry.from_settings(settings)
    client = LlmClient(
        registry,
        build_groq_transport(settings.groq_api_key.get_secret_value(), settings.llm_timeout_s),
        max_attempts=settings.llm_max_attempts,
        max_wait_s=settings.llm_max_wait_s,
        limiter=RateLimiter(
            registry, margin=settings.llm_safety_margin, max_wait_s=settings.llm_max_wait_s
        ),
    )
    cache = ResponseCache(settings.llm_cache_path) if settings.llm_cache_path else None
    return LlmGateway(client, cache=cache, call_log=CallLogger.from_settings(settings))


def _add_usage(usage: dict[str, int] | None, result: Any) -> dict[str, int]:
    total = dict(usage or NO_USAGE)
    if getattr(result, "cache_hit", False):
        total["cache_hits"] += 1
    else:
        total["calls"] += 1
        total["input_tokens"] += getattr(result, "input_tokens", 0)
        total["output_tokens"] += getattr(result, "output_tokens", 0)
    return total


def route_after_execute(state):
    """The only branch in the graph: one retry, and only for repairable errors."""
    if state.get("error") and state.get("repairable") and state.get("retry_count", 0) == 0:
        return "retry"
    return "answer"


class Agent:
    """Owns the compiled graph and its dependencies."""

    def __init__(
        self,
        settings: Settings,
        llm: Any = None,
        db: Database | None = None,
        executor: ReadOnlyExecutor | None = None,
    ):
        self.settings = settings
        self.llm = llm if llm is not None else build_llm(settings)
        self.db = db if db is not None else Database.from_settings(settings)
        self.executor = (
            executor if executor is not None else ReadOnlyExecutor.from_settings(settings)
        )
        # Read once at startup: the validator's allowlist is the real schema, and
        # the schema hash is recorded with every model call (principle 8).
        self.allowed_tables = self.executor.allowed_tables()
        self.schema_hash = self.db.schema_hash()
        self._graph = self._build_graph()

    # --- model calls ----------------------------------------------------

    def _complete(self, state, role, messages, prompt_id):
        """One model call; returns (text, usage including this call)."""
        result = self.llm.complete(
            role,
            messages,
            temperature=TEMPERATURE,
            prompt_id=prompt_id,
            schema_hash=self.schema_hash,
            request_id=state.get("request_id"),
        )
        return result.content, _add_usage(state.get("usage"), result)

    # --- nodes ----------------------------------------------------------

    def generate_sql(self, state):
        """LLM call 1: question -> SQL, or a refusal token."""
        messages = build_sql_messages(state["question"], state.get("history"))
        raw, usage = self._complete(state, LlmRole.SQL_GENERATOR, messages, SQL_PROMPT_ID)
        text = _clean_sql(raw)

        # Specific before general: a write request gets the read-only reply.
        if READ_ONLY_TOKEN in text.upper():
            return {"out_of_scope": True, "sql": None, "answer": READ_ONLY_REPLY, "usage": usage}

        if OUT_OF_SCOPE_TOKEN in text.upper():
            return {"out_of_scope": True, "sql": None, "answer": OUT_OF_SCOPE_REPLY, "usage": usage}

        return {"sql": text, "out_of_scope": False, "error": None, "usage": usage}

    def validate(self, state):
        """Pure Python. No LLM, no database."""
        if state.get("out_of_scope"):
            return {}

        try:
            validated = validate_sql(
                state.get("sql"),
                allowed_tables=self.allowed_tables,
                max_rows=self.settings.max_rows,
            )
        except SqlSafetyError as exc:
            return {**_error_state(exc), "validated": None}

        return {
            "sql": validated.sql,
            "validated": validated,
            "error": None,
            "error_detail": None,
            "repairable": False,
        }

    def execute(self, state):
        """Run the validated query against the read-only database."""
        if state.get("out_of_scope") or state.get("error"):
            return {}

        try:
            result = self.executor.execute(state["validated"])
        except SqlSafetyError as exc:
            return _error_state(exc)

        return {
            "columns": result.columns,
            "rows": result.rows,
            "truncated": result.truncated,
            "limit_reached": result.limit_reached,
        }

    def retry(self, state):
        """LLM call 2 (optional): show the model its error and ask for a fix."""
        messages = build_retry_messages(
            state["question"], state.get("sql") or "", state.get("error_detail") or ""
        )
        raw, usage = self._complete(state, LlmRole.SQL_REPAIR, messages, RETRY_PROMPT_ID)
        text = _clean_sql(raw)
        cleared = {
            "error": None,
            "error_detail": None,
            "repairable": False,
            "validated": None,
            "usage": usage,
        }

        if OUT_OF_SCOPE_TOKEN in text.upper():
            return {
                **cleared,
                "out_of_scope": True,
                "sql": None,
                "answer": OUT_OF_SCOPE_REPLY,
                "retry_count": 1,
            }

        return {**cleared, "sql": text, "retry_count": 1}

    def format_answer(self, state):
        """LLM call 3: turn rows into a sentence."""
        if state.get("out_of_scope"):
            return {"answer": state.get("answer", OUT_OF_SCOPE_REPLY)}

        if state.get("error"):
            # A fixed message per code: database internals never reach the user.
            return {"answer": USER_MESSAGES[SqlErrorCode(state["error"])]}

        messages = build_answer_messages(
            state["question"],
            state["columns"],
            state["rows"],
            state.get("truncated", False),
            state.get("limit_reached", False),
        )
        try:
            text, usage = self._complete(state, LlmRole.SYNTHESIZER, messages, ANSWER_PROMPT_ID)
        except LlmError as exc:
            # The query ran and its rows are real; only the summary is missing.
            return {"answer": SUMMARY_UNAVAILABLE_REPLY, "error": exc.code.value}
        return {"answer": text.strip(), "usage": usage}

    # --- graph ----------------------------------------------------------

    def _build_graph(self):
        g = StateGraph(AgentState)

        g.add_node("generate_sql", self.generate_sql)
        g.add_node("validate", self.validate)
        g.add_node("execute", self.execute)
        g.add_node("retry", self.retry)
        g.add_node("format_answer", self.format_answer)

        g.set_entry_point("generate_sql")
        g.add_edge("generate_sql", "validate")
        g.add_edge("validate", "execute")
        g.add_conditional_edges(
            "execute",
            route_after_execute,
            {"retry": "retry", "answer": "format_answer"},
        )
        g.add_edge("retry", "validate")  # retried SQL is re-validated
        g.add_edge("format_answer", END)

        return g.compile()

    def ask(self, question, history=None):
        """Entry point. Returns a plain dict for the API layer.

        `sql` is the SQL that passed validation (and was executed), or None when
        nothing passed: rejected SQL is never presented as the query that ran.
        `error` is an SqlErrorCode or LLM_* value, never database or provider text.
        `usage` totals this question's model calls (not sent to API clients).
        """
        request_id = str(uuid.uuid4())
        try:
            final = self._graph.invoke(
                {
                    "question": question,
                    "history": (history or [])[-self.settings.max_history_turns :],
                    "retry_count": 0,
                    "request_id": request_id,
                    "usage": dict(NO_USAGE),
                }
            )
        except LlmError as exc:
            # Generation or repair failed: nothing ran, so there is nothing to show.
            return {
                "answer": LLM_ERROR_REPLIES[exc.code],
                "sql": None,
                "columns": [],
                "rows": [],
                "truncated": False,
                "limit_reached": False,
                "error": exc.code.value,
                "out_of_scope": False,
                "request_id": request_id,
                "usage": dict(NO_USAGE),
            }
        validated = final.get("validated")

        return {
            "answer": final.get("answer", ""),
            "sql": validated.sql if validated is not None else None,
            "columns": final.get("columns", []),
            "rows": final.get("rows", []),
            "truncated": final.get("truncated", False),
            "limit_reached": final.get("limit_reached", False),
            "error": final.get("error"),
            "out_of_scope": final.get("out_of_scope", False),
            "request_id": request_id,
            "usage": final.get("usage", dict(NO_USAGE)),
        }
