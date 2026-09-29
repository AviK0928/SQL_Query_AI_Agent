"""Judge calibration: the item set, the hand-label sheet and the agreement rule.

The judge's scores are trusted only per criterion, and only after they agree
with hand labels on a fixed set of cases (Section 10d, D44). Everything here is
deterministic and offline; evals/judges/run.py makes the model calls.

Selection rule (fixed before any judge score was seen): from the Phase 7
baseline run, repeat 0, every item that ran SQL; per tier the first two by id,
plus every item that failed a deterministic check.

Trust rule (agreed 27 Sep 2026, before any judge score was seen): a criterion
is trusted when the judge is within 1 point of the hand label on at least 80%
of items AND its pass/fail call (4-5 pass, 1-3 fail) matches on at least 85%.
A reply that failed to parse counts as a disagreement on every criterion.
"""

from __future__ import annotations

import csv
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from evals.judges.response import CRITERIA, JudgeCase, Verdict

BASELINE_RUN = "2026-09-27-full-gpt-oss-120b"  # D41
PER_TIER = 2
WITHIN_ONE_MIN = 0.80
PASS_MATCH_MIN = 0.85
PASS_SCORE = 4
SHEET_ROWS = 50  # rows written into the label sheet; the judge sees the same number

SHEET_COLUMNS = (
    "id",
    "tier",
    "earlier_questions",
    "question",
    "sql",
    "result",
    "flags",
    "answer",
    "reference_sql",
    "reference_rows",
    "matches_reference",
    *CRITERIA,
    "notes",
)


def _natural(item_id: str) -> tuple[str, int]:
    m = re.fullmatch(r"([a-z]+)(\d+)", item_id)
    return (m.group(1), int(m.group(2))) if m else (item_id, 0)


def judgeable(record: Mapping[str, Any]) -> bool:
    """An answered question with SQL that ran: the only kind the rubric grades."""
    return (
        record.get("status") == "ok"
        and record.get("suite") in ("golden", "adversarial")
        and bool(record.get("sql"))
        and not record.get("error")
    )


def failed_check(record: Mapping[str, Any]) -> bool:
    return any(record.get(k) is False for k in ("correct", "disclosed", "behaviour_ok"))


def select_items(records: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    pool = sorted(
        (r for r in records if r.get("repeat") == 0 and judgeable(r)),
        key=lambda r: _natural(r["id"]),
    )
    chosen: list[Mapping[str, Any]] = []
    per_tier: dict[str, int] = {}
    for rec in pool:
        taken = per_tier.get(rec["tier"], 0)
        if taken < PER_TIER or failed_check(rec):
            chosen.append(rec)
            per_tier[rec["tier"]] = taken + 1
    return chosen


def build_case(
    record: Mapping[str, Any],
    item: Mapping[str, Any],
    run_query: Any,
    reference_rows: Any,
) -> dict[str, Any]:
    """Re-run the stored SQL to recover the rows and flags the answer was written from.

    run_query(sql) returns an executor QueryResult; reference_rows(sql) the
    reference's row count. The stored row count must match, or the database has
    changed since the run and the case would misrepresent it.
    """
    result = run_query(record["sql"])
    if len(result.rows) != record["rows"]:
        raise ValueError(
            f"{record['id']}: {len(result.rows)} rows now, {record['rows']} in the run"
        )
    turns = list(item["turns"])
    ref_sql = item.get("reference_sql")
    return {
        "id": record["id"],
        "repeat": record["repeat"],
        "tier": record["tier"],
        "kind": record["kind"],
        "earlier_questions": turns[:-1],
        "question": turns[-1],
        "sql": record["sql"],
        "columns": result.columns,
        "rows": result.rows,
        "truncated": result.truncated,
        "limit_reached": result.limit_reached,
        "answer": record["answer"],
        "reference_sql": ref_sql,
        "reference_rows": reference_rows(ref_sql) if ref_sql else None,
        "matches_reference": record.get("correct"),
    }


def to_judge_case(case: Mapping[str, Any]) -> JudgeCase:
    return JudgeCase(
        question=case["question"],
        sql=case["sql"],
        columns=list(case["columns"]),
        rows=[list(r) for r in case["rows"]],
        answer=case["answer"],
        truncated=case["truncated"],
        limit_reached=case["limit_reached"],
        earlier_questions=list(case["earlier_questions"]),
    )


# --- the label sheet -------------------------------------------------------------------


def _table(case: Mapping[str, Any]) -> str:
    shown = case["rows"][:SHEET_ROWS]
    lines = [" | ".join(case["columns"])]
    lines += [" | ".join(str(v) for v in row) for row in shown]
    lines.append(f"({len(case['rows'])} rows; {len(shown)} shown)")
    return "\n".join(lines)


def write_label_sheet(cases: Sequence[Mapping[str, Any]], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=SHEET_COLUMNS)
        writer.writeheader()
        for c in cases:
            flags = [f for f in ("truncated", "limit_reached") if c[f]]
            writer.writerow(
                {
                    "id": c["id"],
                    "tier": c["tier"],
                    "earlier_questions": " / ".join(c["earlier_questions"]),
                    "question": c["question"],
                    "sql": c["sql"],
                    "result": _table(c),
                    "flags": ", ".join(flags) or "none",
                    "answer": c["answer"],
                    "reference_sql": c["reference_sql"] or "",
                    "reference_rows": "" if c["reference_rows"] is None else c["reference_rows"],
                    "matches_reference": {True: "yes", False: "no", None: ""}[
                        c["matches_reference"]
                    ],
                    **dict.fromkeys(CRITERIA, ""),
                    "notes": "",
                }
            )


class LabelError(ValueError):
    """The label sheet is incomplete or invalid; the message lists every problem."""


def read_labels(path: Path, ids: Sequence[str]) -> dict[str, dict[str, int]]:
    """Every expected id exactly once, every criterion an integer 1-5."""
    problems: list[str] = []
    labels: dict[str, dict[str, int]] = {}
    with path.open(newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        missing_cols = [c for c in ("id", *CRITERIA) if c not in (reader.fieldnames or [])]
        if missing_cols:
            raise LabelError(f"missing columns: {missing_cols}")
        for row in reader:
            item_id = (row["id"] or "").strip()
            if not item_id:
                continue
            if item_id in labels:
                problems.append(f"{item_id}: listed twice")
                continue
            scores: dict[str, int] = {}
            if not any((row[crit] or "").strip() for crit in CRITERIA):
                problems.append(f"{item_id}: not labelled")
                labels[item_id] = scores
                continue
            for crit in CRITERIA:
                raw = (row[crit] or "").strip()
                if raw not in {"1", "2", "3", "4", "5"}:
                    problems.append(f"{item_id}.{crit}: {raw!r} is not 1-5")
                else:
                    scores[crit] = int(raw)
            labels[item_id] = scores
    problems += [f"{i}: missing from the sheet" for i in ids if i not in labels]
    problems += [f"{i}: not in the calibration set" for i in labels if i not in set(ids)]
    if problems:
        raise LabelError("; ".join(problems))
    return labels


# --- agreement -----------------------------------------------------------------------------


def agreement(
    labels: Mapping[str, Mapping[str, int]], verdicts: Mapping[str, Verdict | None]
) -> dict[str, dict[str, Any]]:
    """Per criterion: items, share within one point, share with the same pass/fail
    call, and whether both meet the trust rule. Missing or unparsed verdicts
    count as disagreements."""
    out: dict[str, dict[str, Any]] = {}
    n = len(labels)
    for crit in CRITERIA:
        within = same = 0
        for item_id, human in labels.items():
            verdict = verdicts.get(item_id)
            if verdict is None:
                continue
            judged = getattr(verdict, crit).score
            within += abs(judged - human[crit]) <= 1
            same += (judged >= PASS_SCORE) == (human[crit] >= PASS_SCORE)
        w, s = (within / n, same / n) if n else (0.0, 0.0)
        out[crit] = {
            "n": n,
            "within_one": round(w, 3),
            "pass_match": round(s, 3),
            "trusted": w >= WITHIN_ONE_MIN and s >= PASS_MATCH_MIN,
        }
    return out


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
