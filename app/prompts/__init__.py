"""Prompt assembly for the SQL agent.

Everything the LLM ever sees is assembled here, from the versioned files in this
package (see loader.py). Prompts are not a security boundary (D2 in RECORDS.md):
the guarantees are enforced in code by app/sql/validator.py and
app/sql/executor.py. Instructions here reduce retries and cost, nothing more.

ACTIVE_VERSIONS picks which file version each prompt uses. Every call-log line
records a prompt id (name@hash8 of the rendered text), and PROMPTS.md maps each
version to its id, so a logged id always identifies the exact text sent.
Activating a new version changes an id pinned in tests/unit/test_prompt_ids.py;
that test is updated only together with the eval evidence in PROMPTS.md.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Final

from app.prompts.loader import prompt_id, read_fragments, read_prompt, render_prompt

__all__ = [
    "ACTIVE_VERSIONS",
    "ANSWER_ROW_LIMIT",
    "ANSWER_PROMPT_ID",
    "ANSWER_SYSTEM_PROMPT",
    "CLARIFY_TOKEN",
    "LIMIT_REACHED_NOTE",
    "OUT_OF_SCOPE_TOKEN",
    "READ_ONLY_TOKEN",
    "RETRY_PROMPT_ID",
    "RETRY_SYSTEM_PROMPT",
    "ROWS_HIDDEN_NOTE",
    "SCHEMA_DESCRIPTION",
    "SQL_PROMPT_ID",
    "SQL_SYSTEM_PROMPT",
    "TRUNCATED_NOTE",
    "Message",
    "build_answer_messages",
    "build_retry_messages",
    "build_sql_messages",
    "prompt_id",
]

Message = dict[str, str]

# Refusal markers the model emits; not credentials (bandit B105 false positive, S4).
OUT_OF_SCOPE_TOKEN: Final = "OUT_OF_SCOPE"  # nosec B105
READ_ONLY_TOKEN: Final = "READ_ONLY"  # nosec B105
CLARIFY_TOKEN: Final = "CLARIFY"  # nosec B105

ACTIVE_VERSIONS: Final[Mapping[str, int]] = {
    "schema": 1,
    "sql_gen": 5,
    "sql_repair": 2,
    "answer": 2,
    "answer_notes": 1,
}

# Rows shown to the synthesizer; the notes below tell it when rows are hidden.
ANSWER_ROW_LIMIT: Final = 20

SCHEMA_DESCRIPTION: Final = read_prompt("schema", ACTIVE_VERSIONS["schema"])

SQL_SYSTEM_PROMPT: Final = render_prompt(
    "sql_gen",
    ACTIVE_VERSIONS["sql_gen"],
    schema=SCHEMA_DESCRIPTION,
    read_only_token=READ_ONLY_TOKEN,
    clarify_token=CLARIFY_TOKEN,
    out_of_scope_token=OUT_OF_SCOPE_TOKEN,
)

RETRY_SYSTEM_PROMPT: Final = render_prompt(
    "sql_repair",
    ACTIVE_VERSIONS["sql_repair"],
    schema=SCHEMA_DESCRIPTION,
    out_of_scope_token=OUT_OF_SCOPE_TOKEN,
)

ANSWER_SYSTEM_PROMPT: Final = read_prompt("answer", ACTIVE_VERSIONS["answer"])

# Result notes. Each flag is disclosed independently: a hidden-rows note must
# not replace the truncation note, or a capped result looks complete.
_NOTES = read_fragments("answer_notes", ACTIVE_VERSIONS["answer_notes"])
ROWS_HIDDEN_NOTE: Final = _NOTES["rows_hidden"]
TRUNCATED_NOTE: Final = _NOTES["truncated"]
LIMIT_REACHED_NOTE: Final = _NOTES["limit_reached"]

SQL_PROMPT_ID: Final = prompt_id("sql_gen", SQL_SYSTEM_PROMPT)
RETRY_PROMPT_ID: Final = prompt_id("sql_repair", RETRY_SYSTEM_PROMPT)
ANSWER_PROMPT_ID: Final = prompt_id(
    "answer", ANSWER_SYSTEM_PROMPT, ROWS_HIDDEN_NOTE, TRUNCATED_NOTE, LIMIT_REACHED_NOTE
)


def build_sql_messages(
    question: str, history: Sequence[Mapping[str, str]] | None = None
) -> list[Message]:
    """Messages for the initial SQL generation call."""
    messages: list[Message] = [{"role": "system", "content": SQL_SYSTEM_PROMPT}]
    for turn in history or []:
        messages.append({"role": "user", "content": turn["question"]})
        messages.append({"role": "assistant", "content": turn["sql"]})
    messages.append({"role": "user", "content": question})
    return messages


def build_retry_messages(question: str, failed_sql: str, error: str) -> list[Message]:
    """Messages for the retry call after a validation or SQL error."""
    return [
        {"role": "system", "content": RETRY_SYSTEM_PROMPT},
        {"role": "user", "content": question},
        {"role": "assistant", "content": failed_sql},
        {
            "role": "user",
            "content": f"That query failed with this error:\n{error}\n\nReturn a corrected query.",
        },
    ]


def build_answer_messages(
    question: str,
    columns: Sequence[str],
    rows: Sequence[Sequence[object]],
    truncated: bool = False,
    limit_reached: bool = False,
) -> list[Message]:
    """Messages for turning result rows into a sentence."""
    notes = []
    if rows:
        header = " | ".join(columns)
        body = "\n".join(" | ".join(str(v) for v in row) for row in rows[:ANSWER_ROW_LIMIT])
        table = f"{header}\n{body}"
        if len(rows) > ANSWER_ROW_LIMIT:
            notes.append(ROWS_HIDDEN_NOTE.format(shown=len(rows)))
    else:
        table = "(no rows returned)"

    if truncated:
        notes.append(TRUNCATED_NOTE)
    if limit_reached:
        notes.append(LIMIT_REACHED_NOTE)
    table = "\n".join([table, *notes])

    return [
        {"role": "system", "content": ANSWER_SYSTEM_PROMPT},
        {"role": "user", "content": f"Question: {question}\n\nResults:\n{table}"},
    ]
