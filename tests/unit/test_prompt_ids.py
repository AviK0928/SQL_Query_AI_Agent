"""Prompt ids: name@hash8 of the rendered prompt text, so any edit changes the id.

test_active_ids_match_the_phase_7_baseline pins the ids of the prompts in use.
It fails whenever the text sent to the model changes: a new version activated,
the schema file or a token edited, or the loader rendering differently. Update
the pinned ids only together with the eval evidence recorded in PROMPTS.md.
"""

import re

from app.prompts import ANSWER_PROMPT_ID, RETRY_PROMPT_ID, SQL_PROMPT_ID
from app.prompts.loader import prompt_id

# D41: the prompts of run 2026-09-27-full-gpt-oss-120b, the Phase 7 baseline.
PINNED_IDS = {
    "sql_gen": "sql_gen@5fb4fe06",
    "sql_repair": "sql_repair@a5c30252",
    "answer": "answer@3c3a3566",
}


def test_ids_name_the_prompt_and_hash_its_text():
    for pid, name in [
        (SQL_PROMPT_ID, "sql_gen"),
        (RETRY_PROMPT_ID, "sql_repair"),
        (ANSWER_PROMPT_ID, "answer"),
    ]:
        assert re.fullmatch(rf"{name}@[0-9a-f]{{8}}", pid)


def test_ids_are_distinct():
    assert len({SQL_PROMPT_ID, RETRY_PROMPT_ID, ANSWER_PROMPT_ID}) == 3


def test_any_edit_changes_the_id_and_the_same_text_does_not():
    assert prompt_id("p", "text") == prompt_id("p", "text")
    assert prompt_id("p", "text") != prompt_id("p", "text.")
    assert prompt_id("p", "a", "b") != prompt_id("p", "a", "c")


def test_active_ids_match_the_phase_7_baseline():
    assert {
        "sql_gen": SQL_PROMPT_ID,
        "sql_repair": RETRY_PROMPT_ID,
        "answer": ANSWER_PROMPT_ID,
    } == PINNED_IDS
