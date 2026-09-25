"""classify_intent: decide what the generator's reply is, without another model call.

The SQL generator either writes a query or replies with a token (kept from the
original design: one call, so generation and classification can never
disagree). Checked specific before general (principle 7): a write request is
READ_ONLY even if other tokens appear; CLARIFY must open the reply.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.agent.replies import CLARIFY_FALLBACK
from app.prompts import CLARIFY_TOKEN, OUT_OF_SCOPE_TOKEN, READ_ONLY_TOKEN


class Intent(StrEnum):
    SQL = "sql"
    READ_ONLY = "read_only"
    CLARIFY = "clarify"
    OUT_OF_SCOPE = "out_of_scope"


@dataclass(frozen=True)
class Classified:
    intent: Intent
    text: str  # the SQL for Intent.SQL, the clarifying question for Intent.CLARIFY


def classify_reply(reply: str) -> Classified:
    stripped = reply.strip()
    upper = stripped.upper()
    if READ_ONLY_TOKEN in upper:
        return Classified(Intent.READ_ONLY, "")
    if upper.startswith(CLARIFY_TOKEN):
        question = stripped[len(CLARIFY_TOKEN) :].lstrip(" :").strip()
        return Classified(Intent.CLARIFY, question or CLARIFY_FALLBACK)
    if OUT_OF_SCOPE_TOKEN in upper:
        return Classified(Intent.OUT_OF_SCOPE, "")
    return Classified(Intent.SQL, stripped)
