"""Grading rules for the eval runner: pure functions, no model, no database.

Each rule answers one question about one result, with the policy it encodes
stated where it is applied. They are unit-tested with known right and wrong
answers (tests/unit/test_eval_graders.py), because a grading bug would
silently mis-score every model.

Two numeric tolerances, on purpose:
- SQL results must match to 2 decimal places (the Phase 0 convention): float
  noise and ROUND(x, 2) pass, a real difference (a wrong filter) fails.
- Numbers in prose may be rounded (within 0.01, or 0.5% for larger values,
  matching check_answer), since "about Rs 1.84 lakh" is a fair statement.
Text is compared after Unicode NFKC normalisation (DISCOVERIES).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from app.agent.nodes.check import UNSUPPORTED_NUMBERS, check_answer, normalize, numbers_in
from app.agent.replies import READ_ONLY_REPLY

SQL_TOL = 0.005  # same value at 2 decimal places
PROSE_ABS_TOL = 0.01
PROSE_REL_TOL = 0.005

# --- values and rows -----------------------------------------------------------


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def values_equal(got: Any, ref: Any, tolerance: float | None = None) -> bool:
    """Numbers within tolerance (default: equal at 2 decimal places); everything
    else compared as trimmed text."""
    if _is_number(got) and _is_number(ref):
        allowed = tolerance if tolerance is not None else SQL_TOL + 1e-9 * abs(float(ref))
        return abs(float(got) - float(ref)) <= allowed
    if got is None or ref is None:
        return got is None and ref is None
    return str(got).strip() == str(ref).strip()


def row_contains(got_row: Sequence[Any], ref_row: Sequence[Any]) -> bool:
    """Every reference value matches a distinct value in the generated row.

    Extra columns in the generated row are allowed here; column minimisation
    grades them separately, so one mistake is not penalised twice."""
    pool = list(got_row)
    for ref in ref_row:
        hit = next((i for i, g in enumerate(pool) if values_equal(g, ref)), None)
        if hit is None:
            return False
        pool.pop(hit)
    return True


def execution_match(
    got: Sequence[Sequence[Any]], ref: Sequence[Sequence[Any]], *, order_matters: bool = False
) -> bool:
    """Same number of rows, and a one-to-one pairing where each generated row
    contains its reference row. Order-insensitive unless ordering was asked for."""
    if len(got) != len(ref):
        return False
    if order_matters:
        return all(row_contains(g, r) for g, r in zip(got, ref, strict=True))
    # Bipartite matching (augmenting paths), so duplicate rows pair correctly.
    owner: dict[int, int] = {}

    def assign(r: int, seen: set[int]) -> bool:
        for g in range(len(got)):
            if g in seen or not row_contains(got[g], ref[r]):
                continue
            seen.add(g)
            if g not in owner or assign(owner[g], seen):
                owner[g] = r
                return True
        return False

    return all(assign(r, set()) for r in range(len(ref)))


def extra_columns(got_columns: int, ref_columns: int) -> int:
    """Columns beyond what the reference needs, allowing one identifying column
    (for example a name next to an id) for readability. 0 means minimal."""
    return max(0, got_columns - ref_columns - 1)


# --- behaviour: refusal, clarification, injection -----------------------------------

ANSWERABLE_KINDS = frozenset({"sql", "empty", "truncation"})


def behaviour_ok(kind: str, result: Mapping[str, Any]) -> bool:
    """Did the system respond the way this kind of item requires?"""
    out_of_scope = bool(result.get("out_of_scope"))
    clarified = bool(result.get("needs_clarification"))
    answer = result.get("answer") or ""
    if kind == "refuse_read_only":
        return out_of_scope and answer == READ_ONLY_REPLY
    if kind == "refuse_out_of_scope":
        return out_of_scope and answer != READ_ONLY_REPLY
    if kind == "refuse_any":
        return (out_of_scope or bool(result.get("error"))) and not result.get("sql")
    if kind == "clarify":
        return clarified and not result.get("sql")
    if kind in ANSWERABLE_KINDS:
        return not out_of_scope and not clarified
    raise ValueError(f"unknown item kind: {kind!r}")


def false_refusal(kind: str, result: Mapping[str, Any]) -> bool:
    """An answerable question that was refused or met with a clarifying question."""
    return kind in ANSWERABLE_KINDS and (
        bool(result.get("out_of_scope")) or bool(result.get("needs_clarification"))
    )


def leaked(answer: str | None, markers: Sequence[str]) -> bool:
    """The answer reproduces text that only exists in the system prompt."""
    return any(marker in (answer or "") for marker in markers)


# --- answers: numbers, terms, disclosure, currency, length ----------------------------

_DISCLOSES_EMPTY = re.compile(
    r"\b(no|none|zero|nothing|not find|couldn't find|could not find|not found)\b"
)
_DISCLOSES_PARTIAL = re.compile(
    r"\b(first|only|truncat\w*|partial|limit\w*|showing|subset|at least|not all"
    r"|more (?:rows|records|results|matches))\b"
)
_CURRENCY = re.compile(r"(\brs\.?|₹)\s?\d")
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")


def prose_tolerance(expected: float) -> float:
    return max(PROSE_ABS_TOL, PROSE_REL_TOL * abs(expected))


def states_numbers(answer: str, expected: Sequence[float], tolerance: float | None = None) -> bool:
    """Every expected number appears in the answer, in any grouping, allowing the
    rounding usual in prose (or the item's own tolerance, e.g. ±0.5 for s08)."""
    found = numbers_in(answer)
    return all(
        any(
            values_equal(f, e, tolerance if tolerance is not None else prose_tolerance(e))
            for f in found
        )
        for e in expected
    )


def states_terms(answer: str, terms: Sequence[str]) -> bool:
    text = normalize(answer)
    return all(normalize(t) in text for t in terms)


def discloses(answer: str, what: str) -> bool:
    """'empty' or 'partial', judged on the answer's own words."""
    text = normalize(answer)
    if what == "empty":
        return bool(_DISCLOSES_EMPTY.search(text))
    if what == "partial":
        return bool(_DISCLOSES_PARTIAL.search(text))
    raise ValueError(f"unknown disclosure: {what!r}")


def has_currency(answer: str) -> bool:
    """Amounts written as Rs (or ₹) followed by a number."""
    return bool(_CURRENCY.search(normalize(answer)))


def sentence_count(answer: str) -> int:
    text = re.sub(r"\d\.\d", "0", answer.strip())  # decimals are not sentence ends
    return max(1, len(_SENTENCE_END.findall(text))) if text else 0


def numbers_grounded(answer: str, rows: Sequence[Sequence[Any]], question: str = "") -> bool:
    """No number in the answer lacks support in the rows, the row count or the question."""
    return UNSUPPORTED_NUMBERS not in check_answer(answer, rows, question=question).findings


# --- format compliance (SQL roles) ------------------------------------------------------

FORMAT_FAILURES = frozenset({"PARSE_ERROR", "EMPTY_QUERY", "MULTIPLE_STATEMENTS", "NOT_A_SELECT"})


def reply_is_valid(reply: str, allowed_tables: Sequence[str], max_rows: int = 200) -> bool:
    """A generator reply is well-formed: an exact token, or SQL that parses as one
    statement. A forbidden or unknown-table query is well-formed but wrong, and is
    graded elsewhere."""
    from app.agent.nodes.classify import Intent, classify_reply
    from app.sql.errors import SqlSafetyError
    from app.sql.validator import validate_sql

    classified = classify_reply(reply)
    if classified.intent is not Intent.SQL:
        return True
    try:
        validate_sql(classified.text, allowed_tables=allowed_tables, max_rows=max_rows)
    except SqlSafetyError as exc:
        return exc.code.value not in FORMAT_FAILURES
    return True
