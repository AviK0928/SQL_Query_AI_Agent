"""LangGraph flow: question -> SQL -> validate -> execute -> answer.

One conditional edge allows a single retry. The retry path increments
retry_count, so a second failure can only route to the answer node --
an infinite loop is structurally impossible, not merely guarded against.

Dependencies (settings, LLM client, database) are passed to the Agent
constructor. There are no module-level clients or graphs; tests build an
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
from app.validator import validate_sql

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
    sql: str | None
    columns: list
    rows: list
    truncated: bool
    error: str | None
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


def build_llm(settings: Settings) -> Any:
    """The real Groq client, configured only from Settings (never os.environ)."""
    from langchain_groq import ChatGroq

    return ChatGroq(
        model_name=settings.groq_model,  # field name; `model` is its alias (mypy call-arg)
        temperature=TEMPERATURE,
        api_key=settings.groq_api_key,
    )


def route_after_execute(state):
    """The only branch in the graph."""
    if state.get("error") and state.get("retry_count", 0) == 0:
        return "retry"
    return "answer"


class Agent:
    """Owns the compiled graph and its dependencies."""

    def __init__(self, settings: Settings, llm: Any = None, db: Database | None = None):
        self.settings = settings
        self.llm = llm if llm is not None else build_llm(settings)
        self.db = db if db is not None else Database.from_settings(settings)
        self._graph = self._build_graph()

    # --- nodes ----------------------------------------------------------

    def generate_sql(self, state):
        """LLM call 1: question -> SQL, or the out-of-scope token."""
        messages = build_sql_messages(state["question"], state.get("history"))
        raw = self.llm.invoke(messages).content
        text = _clean_sql(raw)

        if READ_ONLY_TOKEN in text.upper():
            return {"out_of_scope": True, "sql": None, "answer": READ_ONLY_REPLY}

        if OUT_OF_SCOPE_TOKEN in text.upper():
            return {"out_of_scope": True, "sql": None, "answer": OUT_OF_SCOPE_REPLY}

        return {"sql": text, "out_of_scope": False, "error": None}

    def validate(self, state):
        """Pure Python. No LLM, no database."""
        if state.get("out_of_scope"):
            return {}

        is_valid, cleaned, error = validate_sql(state.get("sql"))
        if not is_valid:
            return {"error": error}
        return {"sql": cleaned, "error": None}

    def execute(self, state):
        """Run the query against the read-only database."""
        if state.get("out_of_scope") or state.get("error"):
            return {}

        result = self.db.run_query(state["sql"])
        return {
            "columns": result["columns"],
            "rows": result["rows"],
            "truncated": result["truncated"],
            "error": result["error"],
        }

    def retry(self, state):
        """LLM call 2 (optional): show the model its error and ask for a fix."""
        messages = build_retry_messages(state["question"], state.get("sql") or "", state["error"])
        text = _clean_sql(self.llm.invoke(messages).content)

        if OUT_OF_SCOPE_TOKEN in text.upper():
            return {
                "out_of_scope": True,
                "sql": None,
                "error": None,
                "answer": OUT_OF_SCOPE_REPLY,
                "retry_count": 1,
            }

        return {"sql": text, "error": None, "retry_count": 1}

    def format_answer(self, state):
        """LLM call 3: turn rows into a sentence."""
        if state.get("out_of_scope"):
            return {"answer": state.get("answer", OUT_OF_SCOPE_REPLY)}

        if state.get("error"):
            return {"answer": f"I couldn't run a query for that. ({state['error']})"}

        messages = build_answer_messages(
            state["question"], state["columns"], state["rows"], state.get("truncated", False)
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
        """Entry point. Returns a plain dict for the API layer."""
        final = self._graph.invoke(
            {
                "question": question,
                "history": (history or [])[-self.settings.max_history_turns :],
                "retry_count": 0,
            }
        )

        return {
            "answer": final.get("answer", ""),
            "sql": final.get("sql"),
            "columns": final.get("columns", []),
            "rows": final.get("rows", []),
            "truncated": final.get("truncated", False),
            "error": final.get("error"),
            "out_of_scope": final.get("out_of_scope", False),
        }
