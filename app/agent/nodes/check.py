"""check_answer: deterministic honesty checks on the final answer, no model call.

The answer prompt asks the model to disclose empty and partial results, but a
prompt is a request (principle 3). This node makes disclosure guaranteed: if the
answer does not say that the result is empty, capped or limited, a fixed
sentence is appended. It also checks that every number in the answer can be
traced to the rows, the row count or the question; unmatched numbers are
recorded as a finding (for evals and logs) but not changed in the text, because
legitimately derived figures such as percentages would otherwise be flagged.

Text is compared after Unicode NFKC normalisation and whitespace collapsing, so
non-breaking spaces and Indian digit grouping (1,83,530) are handled (see
DISCOVERIES).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

EMPTY_UNDISCLOSED = "EMPTY_UNDISCLOSED"
TRUNCATION_UNDISCLOSED = "TRUNCATION_UNDISCLOSED"
LIMIT_UNDISCLOSED = "LIMIT_UNDISCLOSED"
UNSUPPORTED_NUMBERS = "UNSUPPORTED_NUMBERS"

EMPTY_NOTE = "No matching records were found."
TRUNCATION_NOTE = "Only the first {n} rows were returned, so this may not include every match."
LIMIT_NOTE = "The query was limited to {n} rows, so more matching records may exist."

_DISCLOSES_EMPTY = re.compile(
    r"\b(no|none|zero|nothing|not find|couldn't find|could not find|not found)\b"
)
_DISCLOSES_PARTIAL = re.compile(
    r"\b(first|only|truncat\w*|partial|limit\w*|showing|subset|at least|not all"
    r"|more (?:rows|records|results|matches))\b"
)
# 1,83,530 / 183,530 / 183530 / 12.5 — not part of a word or a longer number.
_NUMBER = re.compile(r"(?<![\w.])(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?(?![\w])")


@dataclass(frozen=True)
class AnswerCheck:
    answer: str  # the original answer, plus any disclosure sentences
    findings: tuple[str, ...]  # finding codes, in check order


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).split()).lower()


def numbers_in(text: str) -> list[float]:
    return [float(m.group().replace(",", "")) for m in _NUMBER.finditer(normalize(text))]


def _supported(rows: Sequence[Sequence[Any]], question: str) -> list[float]:
    values: list[float] = [float(len(rows))]
    values += numbers_in(question)
    for row in rows:
        for cell in row:
            if isinstance(cell, bool):
                continue
            if isinstance(cell, (int, float)):
                values.append(float(cell))
            elif isinstance(cell, str):
                values += numbers_in(cell)
    return values


def _matches(number: float, supported: Sequence[float]) -> bool:
    """Equal up to rounding: within 0.01, or 0.5% for larger values."""
    return any(abs(number - s) <= max(0.01, 0.005 * abs(s)) for s in supported)


def check_answer(
    answer: str,
    rows: Sequence[Sequence[Any]],
    *,
    question: str = "",
    truncated: bool = False,
    limit_reached: bool = False,
) -> AnswerCheck:
    text = normalize(answer)
    findings: list[str] = []
    notes: list[str] = []

    if not rows and not _DISCLOSES_EMPTY.search(text):
        findings.append(EMPTY_UNDISCLOSED)
        notes.append(EMPTY_NOTE)
    if truncated and not _DISCLOSES_PARTIAL.search(text):
        findings.append(TRUNCATION_UNDISCLOSED)
        notes.append(TRUNCATION_NOTE.format(n=len(rows)))
    elif limit_reached and not _DISCLOSES_PARTIAL.search(text):
        findings.append(LIMIT_UNDISCLOSED)
        notes.append(LIMIT_NOTE.format(n=len(rows)))

    supported = _supported(rows, question)
    if any(not _matches(n, supported) for n in numbers_in(answer)):
        findings.append(UNSUPPORTED_NUMBERS)

    final = " ".join([answer.strip(), *notes]) if notes else answer
    return AnswerCheck(final, tuple(findings))
