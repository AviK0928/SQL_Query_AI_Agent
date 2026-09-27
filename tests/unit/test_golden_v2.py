"""golden_v2: the documented changes from golden_v1, and the alternates rule (EVALS.md).

Alternates exist so an item grades correctness, not representation (a month
label, a fraction instead of a percentage). They must never widen what counts
as a correct answer: each alternate must have exactly the reference's column
and row counts, and only the documented items may carry alternates. Adding one
means editing this file, which makes the change reviewable.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from app.config import DEFAULT_DB_PATH
from evals.runner import references

DATASETS = Path(__file__).resolve().parents[2] / "evals" / "datasets"
CHANGED = {"g13", "g19", "g39"}
WITH_ALTERNATES = {"g13", "g19"}


def load(name: str) -> dict[str, dict[str, Any]]:
    lines = (DATASETS / name).read_text().splitlines()
    return {it["id"]: it for it in (json.loads(line) for line in lines if line.strip())}


V1, V2 = load("golden_v1.jsonl"), load("golden_v2.jsonl")


def shape(sql: str) -> tuple[int, int]:
    con = sqlite3.connect(f"file:{DEFAULT_DB_PATH}?mode=ro", uri=True)
    try:
        cur = con.execute(sql)
        return len(cur.description or []), len(cur.fetchall())
    finally:
        con.close()


def test_v2_differs_from_v1_only_in_the_documented_items() -> None:
    assert V2.keys() == V1.keys()
    assert {i for i in V1 if V1[i] != V2[i]} == CHANGED


def test_only_documented_items_carry_alternates() -> None:
    assert {i for i, it in V2.items() if it.get("alt_reference_sql")} == WITH_ALTERNATES


@pytest.mark.parametrize("item_id", sorted(WITH_ALTERNATES))
def test_alternates_have_the_reference_shape(item_id: str) -> None:
    item = V2[item_id]
    expected = shape(item["reference_sql"])
    assert expected[1] > 0
    for alt in item["alt_reference_sql"]:
        assert shape(alt) == expected, alt


def test_references_returns_the_reference_then_each_alternate() -> None:
    item = {"reference_sql": "SELECT 1", "alt_reference_sql": ["SELECT 2", "SELECT 3"]}
    assert references(item, DEFAULT_DB_PATH) == [[[1]], [[2]], [[3]]]
    assert references({"reference_sql": "SELECT 1"}, DEFAULT_DB_PATH) == [[[1]]]
