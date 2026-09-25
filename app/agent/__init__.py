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

Package layout (Phase 5): graph.py holds the Agent and its LangGraph flow,
state.py the typed state, replies.py the fixed replies, llm.py the production
LLM stack. Public names are re-exported here, so `from app.agent import Agent`
keeps working unchanged.
"""

from app.agent.graph import Agent, route_after_execute
from app.agent.llm import build_llm
from app.agent.replies import (
    LLM_ERROR_REPLIES,
    OUT_OF_SCOPE_REPLY,
    READ_ONLY_REPLY,
    SUMMARY_UNAVAILABLE_REPLY,
    TEMPERATURE,
)
from app.agent.state import AgentState

__all__ = [
    "LLM_ERROR_REPLIES",
    "OUT_OF_SCOPE_REPLY",
    "READ_ONLY_REPLY",
    "SUMMARY_UNAVAILABLE_REPLY",
    "TEMPERATURE",
    "Agent",
    "AgentState",
    "build_llm",
    "route_after_execute",
]
