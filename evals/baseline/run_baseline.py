"""Phase 0 baseline: run the CURRENT, unmodified agent against live Groq.

This is a throwaway-by-design harness. It exists to freeze how the pre-refactor
agent behaves so later phases can prove they match or beat it. The real eval
harness (evals/runner.py) replaces it in Phase 6.

Properties:
- Live LLM, but throttled: a minimum interval between every model call.
- Resumable: results are appended to a JSONL checkpoint after each item;
  re-running skips items already recorded as ok.
- Stops on the first rate-limit error instead of hammering the API.
- Records model id, temperature, prompt hash, schema hash and git commit.
- Pre-run budget estimate with an explicit confirmation.

Run from the repo root:
    GROQ_MODEL=<model-id> python -m evals.baseline.run_baseline
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import app.agent as agent
from app import prompts
from app.db import DB_PATH, MAX_ROWS

HERE = Path(__file__).resolve().parent
QUESTIONS = HERE / "questions_v0.jsonl"
RESULTS_DIR = HERE / "results"
TEMPERATURE = 0.0
MAX_ROWS_IN_FILE = 50

DISCLOSES_TRUNCATION = re.compile(
    r"\b(first|only|truncat\w*|partial|limit\w*|showing|subset|more rows|not all)\b", re.I
)
DISCLOSES_EMPTY = re.compile(
    r"\b(no|none|not find|couldn't find|could not find|zero|0)\b", re.I
)


# --- reproducibility metadata -------------------------------------------

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def prompt_hash() -> str:
    return _sha(
        prompts.SQL_SYSTEM_PROMPT + prompts.RETRY_SYSTEM_PROMPT + prompts.ANSWER_SYSTEM_PROMPT
    )


def schema_hash() -> str:
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        ddl = [r[0] or "" for r in con.execute(
            "SELECT sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY name"
        )]
    finally:
        con.close()
    return _sha("\n".join(ddl))


def git_commit() -> str:
    out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                         capture_output=True, text=True, check=False)
    return out.stdout.strip() if out.returncode == 0 else "unknown"


# --- live LLM wrapper ----------------------------------------------------

class RecordingLLM:
    """Wraps the real client: enforces a call interval, records usage and latency."""

    def __init__(self, inner, min_interval_s: float):
        self.inner = inner
        self.min_interval_s = min_interval_s
        self._last = 0.0
        self.calls: list[dict] = []

    def invoke(self, messages):
        wait = self.min_interval_s - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        start = time.monotonic()
        try:
            response = self.inner.invoke(messages)
        finally:
            self._last = time.monotonic()
        usage = getattr(response, "usage_metadata", None) or {}
        self.calls.append({
            "latency_s": round(self._last - start, 3),
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "model_reported": (getattr(response, "response_metadata", {}) or {}).get("model_name"),
        })
        return response


def is_rate_limit(exc: BaseException) -> bool:
    return type(exc).__name__ == "RateLimitError" or "429" in str(exc)


# --- deterministic checks ------------------------------------------------

def _norm(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return round(float(v), 2)
    if isinstance(v, str):
        return v.strip()
    return v


def reference_rows(sql: str) -> list[tuple]:
    con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    try:
        return [tuple(_norm(v) for v in r) for r in con.execute(sql).fetchall()]
    finally:
        con.close()


def exact_match(got: list[list], ref: list[tuple]) -> bool:
    return Counter(tuple(_norm(v) for v in r) for r in got) == Counter(ref)


def projection_match(got: list[list], ref: list[tuple]) -> bool:
    """Same row count, and every reference row's values appear inside a distinct
    generated row. Tolerates extra columns and column order. Provisional metric."""
    if len(got) != len(ref):
        return False
    pool = [Counter(_norm(v) for v in r) for r in got]
    for ref_row in ref:
        need = Counter(ref_row)
        hit = next((i for i, g in enumerate(pool) if not need - g), None)
        if hit is None:
            return False
        pool.pop(hit)
    return True


def truncation_pass(checks: dict) -> bool:
    """A partial result passes only if the system KNEW it was partial (flag set)
    and the answer says so. Keyword disclosure without the flag cannot state the
    true extent: baseline item b13 said "first 20 of the 100 rows" when the real
    total was 300, and the keyword-only rule graded that as a pass."""
    if not checks["result_is_partial"]:
        return True
    return checks["truncated_flag"] and checks["discloses_truncation"]


def evaluate(item: dict, result: dict) -> dict:
    kind = item["kind"]
    answer = result.get("answer", "")
    checks: dict = {}

    if kind in ("sql", "empty", "truncation"):
        ref = reference_rows(item["reference_sql"])
        got = result.get("rows", [])
        checks["ref_row_count"] = len(ref)
        checks["got_row_count"] = len(got)
        checks["exact_match"] = exact_match(got, ref)
        checks["projection_match"] = projection_match(got, ref)

    if kind == "sql":
        checks["pass"] = bool(checks["projection_match"]) and not result.get("error")
    elif kind == "empty":
        checks["discloses_empty"] = bool(DISCLOSES_EMPTY.search(answer))
        checks["pass"] = checks["got_row_count"] == 0 and checks["discloses_empty"]
    elif kind == "truncation":
        partial = result.get("truncated") or checks["got_row_count"] < checks["ref_row_count"]
        checks["result_is_partial"] = bool(partial)
        checks["truncated_flag"] = bool(result.get("truncated"))
        checks["discloses_truncation"] = bool(DISCLOSES_TRUNCATION.search(answer))
        checks["pass"] = truncation_pass(checks)
    elif kind == "refuse_read_only":
        checks["pass"] = bool(result.get("out_of_scope")) and "only read" in answer
    elif kind == "refuse_out_of_scope":
        checks["pass"] = bool(result.get("out_of_scope")) and "only read" not in answer
    elif kind == "refuse_any":
        checks["pass"] = bool(result.get("out_of_scope")) or bool(result.get("error"))
    elif kind == "clarify":
        checks["pass"] = None  # no clarify path exists yet; reviewed by hand
    return checks


# --- runner --------------------------------------------------------------

def load_items() -> list[dict]:
    return [json.loads(line) for line in QUESTIONS.read_text().splitlines() if line.strip()]


def load_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {json.loads(l)["id"] for l in path.read_text().splitlines()
            if l.strip() and json.loads(l).get("status") == "ok"}


def estimate(items: list[dict]) -> tuple[int, int]:
    turns = sum(len(i["turns"]) for i in items)
    max_calls = turns * 3  # generate + retry + answer, worst case
    per_call_tokens = len(prompts.SQL_SYSTEM_PROMPT) // 4 + 400
    return max_calls, max_calls * per_call_tokens


def run_item(item: dict, llm: RecordingLLM) -> dict:
    history: list[dict] = []
    result: dict = {}
    first_call = len(llm.calls)
    for question in item["turns"]:
        result = agent.ask(question, history)
        if result["sql"] and not result["error"]:  # mirrors app/main.py
            history = (history + [{"question": question, "sql": result["sql"]}])[-agent.MAX_HISTORY_TURNS:]
    calls = llm.calls[first_call:]
    return {
        "answer": result.get("answer"),
        "sql": result.get("sql"),
        "columns": result.get("columns", []),
        "rows_sample": result.get("rows", [])[:MAX_ROWS_IN_FILE],
        "row_count": len(result.get("rows", [])),
        "truncated": result.get("truncated", False),
        "error": result.get("error"),
        "out_of_scope": result.get("out_of_scope", False),
        "checks": evaluate(item, result),
        "llm_calls": len(calls),
        "input_tokens": sum(c["input_tokens"] or 0 for c in calls),
        "output_tokens": sum(c["output_tokens"] or 0 for c in calls),
        "latency_s": round(sum(c["latency_s"] for c in calls), 3),
        "model_reported": next((c["model_reported"] for c in calls if c["model_reported"]), None),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--min-interval", type=float, default=4.0,
                        help="Seconds between LLM calls. Set from your model's RPM limit.")
    parser.add_argument("--only", nargs="*", help="Run only these item ids.")
    parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
    parser.add_argument("--summary-only", metavar="JSONL",
                        help="Re-grade and summarise an existing results file. No API calls.")
    args = parser.parse_args()

    if args.summary_only:
        summarize(Path(args.summary_only))
        return 0

    model = os.environ.get("GROQ_MODEL")
    if not model or not os.environ.get("GROQ_API_KEY"):
        print("GROQ_MODEL and GROQ_API_KEY must both be set. No default model is assumed.")
        return 2

    items = load_items()
    if args.only:
        items = [i for i in items if i["id"] in set(args.only)]

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_model = re.sub(r"[^A-Za-z0-9._-]", "_", model)
    out_path = RESULTS_DIR / f"baseline_{prompt_hash()}_{safe_model}.jsonl"
    done = load_done(out_path)
    todo = [i for i in items if i["id"] not in done]

    calls, tokens = estimate(todo)
    print(f"Model: {model}  temperature={TEMPERATURE}  prompt={prompt_hash()}  "
          f"schema={schema_hash()}  commit={git_commit()}  row_cap={MAX_ROWS}")
    print(f"Items: {len(todo)} to run, {len(done)} already done -> {out_path}")
    print(f"Worst-case estimate: {calls} requests, ~{tokens:,} tokens, "
          f">= {calls * args.min_interval / 60:.1f} min at {args.min_interval}s/call")
    print("Compare against your remaining daily quota in the Groq console.")
    if not todo:
        return 0
    if not args.yes and input("Proceed? [y/N] ").strip().lower() != "y":
        print("Aborted.")
        return 1

    from langchain_groq import ChatGroq
    llm = RecordingLLM(
        ChatGroq(model=model, temperature=TEMPERATURE, timeout=60, max_retries=2),
        min_interval_s=args.min_interval,
    )
    agent.set_llm(llm)

    meta = {"model": model, "temperature": TEMPERATURE, "prompt_hash": prompt_hash(),
            "schema_hash": schema_hash(), "commit": git_commit()}

    for item in todo:
        record = {"id": item["id"], "tier": item["tier"], "kind": item["kind"],
                  "turns": item["turns"], **meta,
                  "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        try:
            record.update(run_item(item, llm), status="ok")
        except Exception as exc:  # recorded, never swallowed silently
            record.update(status="error", exception=type(exc).__name__,
                          message=str(exc)[:300], rate_limited=is_rate_limit(exc))
        with out_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        verdict = record.get("checks", {}).get("pass") if record["status"] == "ok" else "ERROR"
        print(f"  {item['id']} {item['tier']:<4} {item['kind']:<20} pass={verdict}")
        if record.get("rate_limited"):
            print("Rate limited. Stopping. Re-run the same command later to resume.")
            return 3

    summarize(out_path)
    return 0


def summarize(path: Path) -> None:
    rows = [json.loads(l) for l in path.read_text().splitlines() if l.strip()]
    latest = {r["id"]: r for r in rows}  # last attempt per item wins
    ok = [r for r in latest.values() if r["status"] == "ok"]
    for r in ok:  # apply the current truncation rule to stored checks
        if r["kind"] == "truncation":
            r["checks"]["pass"] = truncation_pass(r["checks"])
    graded = [r for r in ok if r["checks"].get("pass") is not None]
    passed = sum(1 for r in graded if r["checks"]["pass"])
    for r in sorted(latest.values(), key=lambda x: x["id"]):
        verdict = r["checks"].get("pass") if r["status"] == "ok" else "ERROR"
        print(f"  {r['id']} {r['tier']:<4} {r['kind']:<20} pass={verdict}")
    print(f"\nSummary: {passed}/{len(graded)} graded items passed; "
          f"{len(latest) - len(ok)} errored; {len(ok) - len(graded)} need manual review")
    print(f"Tokens in/out: {sum(r['input_tokens'] for r in ok):,} / "
          f"{sum(r['output_tokens'] for r in ok):,}   LLM calls: {sum(r['llm_calls'] for r in ok)}")
    lat = sorted(r["latency_s"] for r in ok)
    if lat:
        print(f"Per-item LLM latency p50={lat[len(lat) // 2]}s max={lat[-1]}s")


if __name__ == "__main__":
    sys.exit(main())
