"""Prompt ids: name@hash8 of the prompt text, so any edit changes the id."""

import re

from app.prompts import ANSWER_PROMPT_ID, RETRY_PROMPT_ID, SQL_PROMPT_ID, _prompt_id


def test_ids_name_the_prompt_and_hash_its_text():
    for prompt_id, name in [
        (SQL_PROMPT_ID, "sql_gen"),
        (RETRY_PROMPT_ID, "sql_repair"),
        (ANSWER_PROMPT_ID, "answer"),
    ]:
        assert re.fullmatch(rf"{name}@[0-9a-f]{{8}}", prompt_id)


def test_ids_are_distinct():
    assert len({SQL_PROMPT_ID, RETRY_PROMPT_ID, ANSWER_PROMPT_ID}) == 3


def test_any_edit_changes_the_id_and_the_same_text_does_not():
    assert _prompt_id("p", "text") == _prompt_id("p", "text")
    assert _prompt_id("p", "text") != _prompt_id("p", "text.")
    assert _prompt_id("p", "a", "b") != _prompt_id("p", "a", "c")
