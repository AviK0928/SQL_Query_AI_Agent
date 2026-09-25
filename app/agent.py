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

Dependencies (settings, LLM client, schema reader, executor) are passed to the
Agent constructor. There are no module-level clients or graphs; tests build an
Agent with a fake LLM instead of patching globals.
Spring comparison: constructor injection instead of static fields.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, TypedDict

from langgraph.graph import END, StateGraph

from app.db import Database
from app.prompts import (
    OUT_OF_SCOPE_TOKEN,
    READ_ONLY_TOKEN,
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


def _clean_sql(text):
    """Strip markdown fences and stray prose the model may add."""
    text = text.strip()
    fence = re.match(r"^```(?:sql)?\s*(.*?)\s*```$", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    return text


def _error_state(exc: SqlSafetyError) -> dict[str, Any]:
    return {"error": exc.code.value, "error_detail": exc.detail, "repairable": exc.repairable}


def build_llm(settings: Settings) -> Any:
    """The real Groq client, configured only from Settings (never os.environ)."""
    from langchain_groq import ChatGroq

    return ChatGroq(
        model_name=settings.groq_model,  # field name; `model` is its alias (mypy call-arg)
        temperature=TEMPERATURE,
        api_key=settings.groq_api_key,
    )


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
        # Read once at startup: the validator's allowlist is the real schema.
        self.allowed_tables = self.executor.allowed_tables()
        self._graph = self._build_graph()

    # --- nodes ----------------------------------------------------------

    def generate_sql(self, state):
        """LLM call 1: question -> SQL, or a refusal token."""
        messages = build_sql_messages(state["question"], state.get("history"))
        raw = self.llm.invoke(messages).content
        text = _clean_sql(raw)

        # Specific before general: a write request gets the read-only reply.
        if READ_ONLY_TOKEN in text.upper():
            return {"out_of_scope": True, "sql": None, "answer": READ_ONLY_REPLY}

        if OUT_OF_SCOPE_TOKEN in text.upper():
            return {"out_of_scope": True, "sql": None, "answer": OUT_OF_SCOPE_REPLY}

        return {"sql": text, "out_of_scope": False, "error": None}

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
        text = _clean_sql(self.llm.invoke(messages).content)
        cleared = {"error": None, "error_detail": None, "repairable": False, "validated": None}

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
        return {"answer": self.llm.invoke(messages).content.strip()}

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
        `error` is an SqlErrorCode value, never database text.
        """
        final = self._graph.invoke(
            {
                "question": question,
                "history": (history or [])[-self.settings.max_history_turns :],
                "retry_count": 0,
            }
        )
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
        }
