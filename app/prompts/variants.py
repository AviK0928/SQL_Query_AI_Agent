"""Render any released version of a system prompt, for experiments (Phase 7).

Production uses only ACTIVE_VERSIONS (app/prompts/__init__.py). An experiment
measures a candidate version before it is activated, so the eval runner asks
for it here; the returned id is the same name@hash8 the call log records, so a
report shows exactly which text was measured.
"""

from __future__ import annotations

from app.prompts import (
    CLARIFY_TOKEN,
    LIMIT_REACHED_NOTE,
    OUT_OF_SCOPE_TOKEN,
    READ_ONLY_TOKEN,
    ROWS_HIDDEN_NOTE,
    SCHEMA_DESCRIPTION,
    TRUNCATED_NOTE,
)
from app.prompts.loader import prompt_id, read_prompt, render_prompt

VARIABLE_PROMPTS = ("sql_gen", "sql_repair", "answer")


def system_prompt(name: str, version: int) -> tuple[str, str]:
    """(text, prompt id) of one version of a system prompt, rendered as in production."""
    if name == "sql_gen":
        text = render_prompt(
            name,
            version,
            schema=SCHEMA_DESCRIPTION,
            read_only_token=READ_ONLY_TOKEN,
            clarify_token=CLARIFY_TOKEN,
            out_of_scope_token=OUT_OF_SCOPE_TOKEN,
        )
        return text, prompt_id(name, text)
    if name == "sql_repair":
        text = render_prompt(
            name, version, schema=SCHEMA_DESCRIPTION, out_of_scope_token=OUT_OF_SCOPE_TOKEN
        )
        return text, prompt_id(name, text)
    if name == "answer":
        text = read_prompt(name, version)
        return text, prompt_id(name, text, ROWS_HIDDEN_NOTE, TRUNCATED_NOTE, LIMIT_REACHED_NOTE)
    raise ValueError(f"no variable prompt {name!r}; expected one of {VARIABLE_PROMPTS}")
