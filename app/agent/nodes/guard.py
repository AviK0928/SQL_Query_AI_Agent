"""guard_input: reject questions that cannot be answered, before any model call.

Cheap, deterministic rules only. A rejected question costs no Groq request.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.agent.replies import EMPTY_QUESTION_REPLY, NO_TEXT_REPLY, TOO_LONG_REPLY

# The API already caps questions at 500 characters; this protects other callers
# (the eval runner, scripts) with the same limit.
MAX_QUESTION_CHARS = 500


@dataclass(frozen=True)
class GuardRejection:
    code: str  # returned as the response's `error`
    reply: str  # returned as the response's `answer`


def guard_input(question: str | None) -> GuardRejection | None:
    """None when the question may proceed, else why it was rejected."""
    text = (question or "").strip()
    if not text:
        return GuardRejection("INPUT_EMPTY", EMPTY_QUESTION_REPLY)
    if len(text) > MAX_QUESTION_CHARS:
        return GuardRejection("INPUT_TOO_LONG", TOO_LONG_REPLY)
    if not any(ch.isalpha() for ch in text):
        return GuardRejection("INPUT_NO_TEXT", NO_TEXT_REPLY)
    return None
