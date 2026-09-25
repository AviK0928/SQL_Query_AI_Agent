"""The LangGraph flow and the Agent that owns it (moved from app/agent.py in Phase 5).

Flow: guard_input -> generate_sql -> classify_intent -> validate -> execute
-> (repair, if repairable, up to MAX_REPAIR_ATTEMPTS times) -> format_answer.
A rejected question ends at
guard_input with no model call; a refusal or a clarifying question ends at
classify_intent. Node decisions live in app/agent/nodes/ as pure functions.
"""

from __future__ import annotations

import re
import uuid
from typing import TYPE_CHECKING, Any

from langgraph.graph import END, StateGraph

from app.agent.llm import _add_usage, build_llm
from app.agent.nodes.classify import Intent, classify_reply
from app.agent.nodes.guard import guard_input
from app.agent.replies import (
    LLM_ERROR_REPLIES,
    NO_USAGE,
    OUT_OF_SCOPE_REPLY,
    READ_ONLY_REPLY,
    SUMMARY_UNAVAILABLE_REPLY,
    TEMPERATURE,
)
from app.agent.state import AgentState
from app.db import Database
from app.llm.client import LlmError
from app.llm.registry import LlmRole
from app.prompts import (
    ANSWER_PROMPT_ID,
    OUT_OF_SCOPE_TOKEN,
    RETRY_PROMPT_ID,
    SQL_PROMPT_ID,
    build_answer_messages,
    build_retry_messages,
    build_sql_messages,
)
from app.sql.errors import USER_MESSAGES, SqlErrorCode, SqlSafetyError
from app.sql.executor import ReadOnlyExecutor
from app.sql.validator import validate_sql

if TYPE_CHECKING:
    from app.config import Settings


def _clean_sql(text):
    """Strip markdown fences and stray prose the model may add."""
    text = text.strip()
    fence = re.match(r"^```(?:sql)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    return text


def _error_state(exc: SqlSafetyError) -> dict[str, Any]:
    return {"error": exc.code.value, "error_detail": exc.detail, "repairable": exc.repairable}


def route_after_guard(state):
    return "end" if state.get("blocked") else "generate_sql"


def route_after_classify(state):
    """Only SQL continues; refusals and clarifying questions are already answered."""
    if state.get("out_of_scope") or state.get("needs_clarification"):
        return "end"
    return "validate"  # even an empty reply: the validator answers EMPTY_QUERY


def route_after_execute(state):
    """Repair only repairable errors, and at most MAX_REPAIR_ATTEMPTS times."""
    if (
        state.get("error")
        and state.get("repairable")
        and state.get("retry_count", 0) < state.get("max_repairs", 1)
    ):
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

    def guard(self, state):
        """No model call: reject empty, oversized or non-text questions."""
        rejection = guard_input(state.get("question"))
        if rejection is None:
            return {"blocked": False}
        return {"blocked": True, "error": rejection.code, "answer": rejection.reply, "sql": None}

    def generate_sql(self, state):
        """LLM call 1: question -> SQL, or a token (READ_ONLY, CLARIFY, OUT_OF_SCOPE)."""
        messages = build_sql_messages(state["question"], state.get("history"))
        raw, usage = self._complete(state, LlmRole.SQL_GENERATOR, messages, SQL_PROMPT_ID)
        return {"reply": _clean_sql(raw), "usage": usage}

    def classify(self, state):
        """No model call: decide what the generator's reply is."""
        result = classify_reply(state.get("reply", ""))
        if result.intent is Intent.READ_ONLY:
            return {"out_of_scope": True, "sql": None, "answer": READ_ONLY_REPLY}
        if result.intent is Intent.OUT_OF_SCOPE:
            return {"out_of_scope": True, "sql": None, "answer": OUT_OF_SCOPE_REPLY}
        if result.intent is Intent.CLARIFY:
            return {"needs_clarification": True, "sql": None, "answer": result.text}
        return {"sql": result.text, "out_of_scope": False, "error": None}

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
                "retry_count": state.get("retry_count", 0) + 1,
            }

        return {**cleared, "sql": text, "retry_count": state.get("retry_count", 0) + 1}

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

        g.add_node("guard_input", self.guard)
        g.add_node("generate_sql", self.generate_sql)
        g.add_node("classify_intent", self.classify)
        g.add_node("validate", self.validate)
        g.add_node("execute", self.execute)
        g.add_node("retry", self.retry)
        g.add_node("format_answer", self.format_answer)

        g.set_entry_point("guard_input")
        g.add_conditional_edges(
            "guard_input", route_after_guard, {"generate_sql": "generate_sql", "end": END}
        )
        g.add_edge("generate_sql", "classify_intent")
        g.add_conditional_edges(
            "classify_intent", route_after_classify, {"validate": "validate", "end": END}
        )
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
                    "blocked": False,
                    "max_repairs": self.settings.max_repair_attempts,
                    "needs_clarification": False,
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
                "needs_clarification": False,
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
            "needs_clarification": final.get("needs_clarification", False),
            "request_id": request_id,
            "usage": final.get("usage", dict(NO_USAGE)),
        }
