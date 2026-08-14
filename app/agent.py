"""LangGraph flow: question -> SQL -> validate -> execute -> answer.

One conditional edge allows a single retry. The retry path increments
retry_count, so a second failure can only route to the answer node --
an infinite loop is structurally impossible, not merely guarded against.
"""

import os
import re
from typing import Optional, TypedDict

from langgraph.graph import END, StateGraph

from app.db import run_query
from app.prompts import (
    OUT_OF_SCOPE_TOKEN,
    build_answer_messages,
    build_retry_messages,
    build_sql_messages,
)
from app.validator import validate_sql

MODEL_NAME = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
MAX_HISTORY_TURNS = 3

OUT_OF_SCOPE_REPLY = (
    "I can only answer questions about the e-commerce database "
    "(customers, products, orders and order items). Try asking about "
    "customers, sales or products."
)

_llm = None


def get_llm():
    """Create the LLM client on first use, not at import time.

    Import-time creation would make every test and the /health endpoint
    require an API key.
    """
    global _llm
    if _llm is None:
        if not os.environ.get("GROQ_API_KEY"):
            raise RuntimeError("GROQ_API_KEY is not set.")
        from langchain_groq import ChatGroq
        _llm = ChatGroq(model=MODEL_NAME, temperature=0)
    return _llm


def set_llm(llm):
    """Inject a client. Used by tests to supply a fake."""
    global _llm
    _llm = llm


class AgentState(TypedDict, total=False):
    question: str
    history: list
    sql: Optional[str]
    columns: list
    rows: list
    truncated: bool
    error: Optional[str]
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


# --- nodes --------------------------------------------------------------

def generate_sql(state):
    """LLM call 1: question -> SQL, or the out-of-scope token."""
    messages = build_sql_messages(state["question"], state.get("history"))
    raw = get_llm().invoke(messages).content
    text = _clean_sql(raw)

    if OUT_OF_SCOPE_TOKEN in text.upper():
        return {"out_of_scope": True, "sql": None, "answer": OUT_OF_SCOPE_REPLY}

    return {"sql": text, "out_of_scope": False, "error": None}


def validate(state):
    """Pure Python. No LLM, no database."""
    if state.get("out_of_scope"):
        return {}

    is_valid, cleaned, error = validate_sql(state.get("sql"))
    if not is_valid:
        return {"error": error}
    return {"sql": cleaned, "error": None}


def execute(state):
    """Run the query against the read-only database."""
    if state.get("out_of_scope") or state.get("error"):
        return {}

    result = run_query(state["sql"])
    return {
        "columns": result["columns"],
        "rows": result["rows"],
        "truncated": result["truncated"],
        "error": result["error"],
    }


def retry(state):
    """LLM call 2 (optional): show the model its error and ask for a fix."""
    messages = build_retry_messages(state["question"], state.get("sql") or "", state["error"])
    text = _clean_sql(get_llm().invoke(messages).content)

    if OUT_OF_SCOPE_TOKEN in text.upper():
        return {"out_of_scope": True, "sql": None, "error": None,
                "answer": OUT_OF_SCOPE_REPLY, "retry_count": 1}

    return {"sql": text, "error": None, "retry_count": 1}


def format_answer(state):
    """LLM call 3: turn rows into a sentence."""
    if state.get("out_of_scope"):
        return {"answer": state.get("answer", OUT_OF_SCOPE_REPLY)}

    if state.get("error"):
        return {"answer": f"I couldn't run a query for that. ({state['error']})"}

    messages = build_answer_messages(
        state["question"], state["columns"], state["rows"], state.get("truncated", False)
    )
    return {"answer": get_llm().invoke(messages).content.strip()}


# --- edges --------------------------------------------------------------

def route_after_execute(state):
    """The only branch in the graph."""
    if state.get("error") and state.get("retry_count", 0) == 0:
        return "retry"
    return "answer"


def build_graph():
    g = StateGraph(AgentState)

    g.add_node("generate_sql", generate_sql)
    g.add_node("validate", validate)
    g.add_node("execute", execute)
    g.add_node("retry", retry)
    g.add_node("format_answer", format_answer)

    g.set_entry_point("generate_sql")
    g.add_edge("generate_sql", "validate")
    g.add_edge("validate", "execute")
    g.add_conditional_edges(
        "execute", route_after_execute,
        {"retry": "retry", "answer": "format_answer"},
    )
    g.add_edge("retry", "validate")      # retried SQL is re-validated
    g.add_edge("format_answer", END)

    return g.compile()


_graph = None


def ask(question, history=None):
    """Entry point. Returns a plain dict for the API layer."""
    global _graph
    if _graph is None:
        _graph = build_graph()

    final = _graph.invoke({
        "question": question,
        "history": (history or [])[-MAX_HISTORY_TURNS:],
        "retry_count": 0,
    })

    return {
        "answer": final.get("answer", ""),
        "sql": final.get("sql"),
        "columns": final.get("columns", []),
        "rows": final.get("rows", []),
        "truncated": final.get("truncated", False),
        "error": final.get("error"),
        "out_of_scope": final.get("out_of_scope", False),
    }
