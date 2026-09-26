"""Eval runner: live runs against the production stack, graded, resumable, reported.

    python -m evals.runner --role sql_generator --model openai/gpt-oss-20b \
        --suites golden adversarial --repeats 3 --tag smoke-20b [--ids g01 g02] [--yes]

Design (Phase 6, agreed before any run):
- One variable at a time: only the role under test uses the candidate model;
  every other role stays on GROQ_MODEL (production).
- No fallback during evals: an answer must come from the model being scored.
  A persistent 429 stops the run; re-running the same command resumes it.
- A separate response cache per repeat: a resumed run reuses its own answers,
  while the 3 repeats stay independent (consistency is measured, not faked).
- Paced by --min-interval between questions so the client's rate limiter does
  not sleep, which would count as model latency.

Each run writes evals/reports/<date>-<tag>/: manifest.json, results.jsonl (one
graded record per item per repeat), calls.jsonl (the call log, with content) and
report.md. Grading rules live in evals/graders.py, scoring in evals/scoring.py.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import statistics
import subprocess
import sys
import time
from collections import defaultdict
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals import graders as g
from evals import scoring as sc

HERE = Path(__file__).resolve().parent
DATASETS = HERE / "datasets"
REPORTS = HERE / "reports"
SUITES = {
    "golden": "golden_v2.jsonl",
    "adversarial": "adversarial_v1.jsonl",
    "repair": "repair_v1.jsonl",
    "synthesizer": "synthesizer_v1.jsonl",
}
ROLE_SUITES = {
    "sql_generator": ("golden", "adversarial"),
    "sql_repair": ("repair",),
    "synthesizer": ("synthesizer",),
}
EST_CALLS = {"golden": 2.2, "adversarial": 1.0, "repair": 1.0, "synthesizer": 1.0}
EST_TOKENS_PER_CALL = 700


# --- inputs -----------------------------------------------------------------------


def load_suite(name: str) -> list[dict[str, Any]]:
    path = DATASETS / SUITES[name]
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def reference(sql: str, db_path: Path) -> tuple[int, list[list[Any]]]:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cur = con.execute(sql)
        return len(cur.description or []), [list(r) for r in cur.fetchall()]
    finally:
        con.close()


def references(item: dict[str, Any], db_path: Path) -> list[list[list[Any]]]:
    """Result rows of the reference and of each accepted alternate (golden_v2).

    An alternate may differ from the reference only in representation (a month
    label, a fraction instead of a percentage); tests/unit/test_golden_v2.py
    enforces the same column and row counts.
    """
    sqls = [item["reference_sql"], *item.get("alt_reference_sql", [])]
    return [reference(sql, db_path)[1] for sql in sqls]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


def git_commit() -> str:
    out = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    return out.stdout.strip() or "unknown"


# --- the model wrapper: records every reply for format compliance --------------------


class Recorder:
    """Wraps the LLM gateway (or a test fake): passes calls through, keeps replies."""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.replies: list[tuple[str, str]] = []

    def complete(self, role: Any, messages: Any, **kwargs: Any) -> Any:
        result = self.inner.complete(role, messages, **kwargs)
        self.replies.append((str(getattr(role, "value", role)), result.content))
        return result


def settings_for(base: Any, role: str, model: str, run_dir: Path, repeat: int) -> Any:
    """Production settings with only the role under test changed (one variable)."""
    return base.model_copy(
        update={
            "llm_role_models": {**base.llm_role_models, role: model},
            "groq_fallback_model": None,
            "llm_cache_path": run_dir / "cache" / f"repeat{repeat}.sqlite",
            "llm_log_content": True,
            "llm_log_path": run_dir / "calls.jsonl",
        }
    )


# --- running and grading one item ------------------------------------------------------


def ask_all_turns(agent: Any, turns: Sequence[str]) -> dict[str, Any]:
    """Multi-turn items replay history the way app/main.py does."""
    history: list[dict[str, str]] = []
    result: dict[str, Any] = {}
    calls = tokens = 0
    for question in turns:
        result = agent.ask(question, history)
        usage = result.get("usage") or {}
        calls += usage.get("calls", 0)
        tokens += usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
        if result.get("sql") and not result.get("error"):
            history = (history + [{"question": question, "sql": result["sql"]}])[-3:]
        elif result.get("needs_clarification"):
            turn = {"question": question, "sql": f"CLARIFY: {result['answer']}"}
            history = (history + [turn])[-3:]
    return {**result, "calls": calls, "tokens": tokens}


def grade_agent_item(
    item: dict[str, Any],
    result: dict[str, Any],
    suite: str,
    db_path: Path,
    replies: list[str],
    tables: Sequence[str],
) -> dict[str, Any]:
    kind = item["kind"]
    graded: dict[str, Any] = {
        "behaviour_ok": g.behaviour_ok(kind, result),
        "false_refusal": g.false_refusal(kind, result),
        "format_valid": [g.reply_is_valid(r, tables) for r in replies],
    }
    if suite == "adversarial":
        graded["leaked"] = g.leaked(result.get("answer"), item.get("leak_markers", []))
    if kind in ("sql", "empty", "truncation") and item.get("reference_sql"):
        ref_cols, ref_rows = reference(item["reference_sql"], db_path)
        got = result.get("rows") or []
        if kind == "sql":
            # golden_v2: an item may list alternates that differ only in representation.
            graded["correct"] = not result.get("error") and any(
                g.execution_match(got, ref, order_matters=item.get("order_matters", False))
                for ref in references(item, db_path)
            )
            if got:
                got_cols = len(result.get("columns") or [])
                graded["extra_columns"] = g.extra_columns(got_cols, ref_cols)
        elif kind == "empty":
            graded["correct"] = not result.get("error") and not got
            graded["disclosed"] = g.discloses(result.get("answer") or "", "empty")
        else:  # truncation: the system must know it is partial and say so
            flagged = bool(result.get("truncated") or result.get("limit_reached"))
            graded["disclosed"] = flagged and g.discloses(result.get("answer") or "", "partial")
    return graded


def grade_repair(
    item: dict[str, Any],
    reply: str,
    rows: list[list[Any]] | None,
    db_path: Path,
    tables: Sequence[str],
) -> dict[str, Any]:
    _, ref_rows = reference(item["reference_sql"], db_path)
    correct = rows is not None and g.execution_match(rows, ref_rows)
    return {"correct": correct, "format_valid": [g.reply_is_valid(reply, tables)]}


def grade_synthesizer(item: dict[str, Any], answer: str) -> dict[str, Any]:
    tolerance = item.get("number_tolerance")
    faithful = (
        g.states_numbers(answer, item["expected_numbers"], tolerance)
        and g.states_terms(answer, item["expected_terms"])
        and g.numbers_grounded(answer, item["rows"], item["question"])
    )
    graded: dict[str, Any] = {
        "faithful": faithful,
        "money": item["money"],
        "currency": g.has_currency(answer),
        "concise": g.sentence_count(answer) <= 2,
        "must_disclose": item.get("must_disclose"),
    }
    if item.get("must_disclose"):
        graded["disclosed"] = g.discloses(answer, item["must_disclose"])
    return graded


# --- the run -------------------------------------------------------------------------------


def estimate(suites: Sequence[str], counts: dict[str, int], repeats: int) -> tuple[int, int]:
    calls = sum(EST_CALLS[s] * counts[s] for s in suites) * repeats
    return int(calls), int(calls * EST_TOKENS_PER_CALL)


def done_keys(results: Path) -> set[tuple[str, str, int]]:
    if not results.exists():
        return set()
    keys = set()
    for line in results.read_text().splitlines():
        rec = json.loads(line)
        if rec.get("status") == "ok":
            keys.add((rec["suite"], rec["id"], rec["repeat"]))
    return keys


def run(
    *,
    role: str,
    model: str,
    suites: Sequence[str],
    repeats: int,
    tag: str,
    ids: Sequence[str] | None = None,
    yes: bool = False,
    min_interval: float = 12.0,
    base_settings: Any = None,
    make_llm: Callable[[Any], Any] | None = None,
    reports: Path = REPORTS,
    today: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
    confirm: Callable[[str], str] = input,
) -> tuple[Path, int]:
    """Run, grade and report. Returns (run_dir, exit_code): 0 done, 1 aborted, 3 rate limited."""
    from app.agent import Agent, build_llm
    from app.agent.nodes.classify import classify_reply
    from app.config import load_settings
    from app.llm.client import LlmError
    from app.llm.registry import LlmRole
    from app.prompts import (
        ANSWER_PROMPT_ID,
        RETRY_PROMPT_ID,
        SQL_PROMPT_ID,
        build_answer_messages,
        build_retry_messages,
    )
    from app.sql.errors import SqlSafetyError
    from app.sql.executor import ReadOnlyExecutor
    from app.sql.validator import validate_sql

    base = base_settings or load_settings()
    make_llm = make_llm or build_llm
    assert model in base.llm_limits, f"LLM_LIMITS has no entry for {model}"  # nosec B101
    items = {s: [i for i in load_suite(s) if not ids or i["id"] in set(ids)] for s in suites}
    run_dir = reports / f"{today or datetime.now(UTC).date().isoformat()}-{tag}"
    run_dir.mkdir(parents=True, exist_ok=True)
    results_path = run_dir / "results.jsonl"
    done = done_keys(results_path)
    todo = [
        (s, it, r)
        for r in range(repeats)
        for s in suites
        for it in items[s]
        if (s, it["id"], r) not in done
    ]

    calls, tokens = estimate(suites, {s: len(items[s]) for s in suites}, repeats)
    lim = base.llm_limits[model]
    print(
        f"Role {role} -> {model} | other roles: {base.groq_model} | repeats {repeats} | "
        f"{len(todo)} to run, {len(done)} done -> {run_dir}"
    )
    print(
        f"Estimate (whole run): ~{calls} calls, ~{tokens:,} tokens | {model} daily limits: "
        f"{lim.rpd} requests, {lim.tpd:,} tokens | pacing {min_interval}s/question "
        f"(~{len(todo) * min_interval / 60:.0f} min)"
    )
    if not todo:
        write_report(run_dir)
        return run_dir, 0
    if not yes and confirm("Proceed? [y/N] ").strip().lower() != "y":
        print("Aborted.")
        return run_dir, 1

    manifest = {
        "role": role,
        "model": model,
        "production_model": base.groq_model,
        "repeats": repeats,
        "suites": list(suites),
        "ids": list(ids or []),
        "temperature": 0,
        "min_interval_s": min_interval,
        "commit": git_commit(),
        "limits": lim.model_dump(),
        "prompt_ids": {
            "sql_gen": SQL_PROMPT_ID,
            "sql_repair": RETRY_PROMPT_ID,
            "answer": ANSWER_PROMPT_ID,
        },
        "datasets": {s: sha(DATASETS / SUITES[s]) for s in suites},
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")

    agents: dict[int, Any] = {}
    gateways: dict[int, Recorder] = {}
    executor = ReadOnlyExecutor.from_settings(base)
    tables = sorted(executor.allowed_tables())
    exit_code = 0
    for n, (suite, item, repeat) in enumerate(todo):
        if n:
            sleep(min_interval)
        settings = settings_for(base, role, model, run_dir, repeat)
        record: dict[str, Any] = {
            "suite": suite,
            "id": item["id"],
            "tier": item.get("tier", suite),
            "kind": item.get("kind", suite),
            "repeat": repeat,
            "model": model,
        }
        started = time.monotonic()
        try:
            if suite in ("golden", "adversarial"):
                if repeat not in agents:
                    recorder = Recorder(make_llm(settings))
                    agents[repeat] = Agent(settings, llm=recorder)
                agent = agents[repeat]
                agent.llm.replies.clear()
                result = ask_all_turns(agent, item["turns"])
                gen = LlmRole.SQL_GENERATOR.value
                replies = [text for r, text in agent.llm.replies if r == gen]
                record.update(
                    answer=result.get("answer"),
                    sql=result.get("sql"),
                    error=result.get("error"),
                    rows=len(result.get("rows") or []),
                    calls=result["calls"],
                    tokens=result["tokens"],
                    latency_s=round(time.monotonic() - started, 3),
                    **grade_agent_item(item, result, suite, base.db_path, replies, tables),
                )
            else:
                if repeat not in gateways:
                    gateways[repeat] = Recorder(make_llm(settings))
                gateway = gateways[repeat]
                if suite == "repair":
                    reply = gateway.complete(
                        LlmRole.SQL_REPAIR,
                        build_retry_messages(
                            item["question"], item["broken_sql"], item["error_detail"]
                        ),
                        temperature=0,
                        prompt_id=RETRY_PROMPT_ID,
                    )
                    rows = None
                    try:
                        sql = classify_reply(reply.content).text
                        rows = executor.execute(
                            validate_sql(sql, allowed_tables=tables, max_rows=base.max_rows)
                        ).rows
                    except SqlSafetyError:
                        pass
                    graded = grade_repair(item, reply.content, rows, base.db_path, tables)
                else:
                    reply = gateway.complete(
                        LlmRole.SYNTHESIZER,
                        build_answer_messages(
                            item["question"],
                            item["columns"],
                            item["rows"],
                            item["truncated"],
                            item["limit_reached"],
                        ),
                        temperature=0,
                        prompt_id=ANSWER_PROMPT_ID,
                    )
                    graded = grade_synthesizer(item, reply.content)
                record.update(
                    answer=reply.content,
                    calls=1,
                    tokens=reply.input_tokens + reply.output_tokens,
                    latency_s=round(time.monotonic() - started, 3),
                    **graded,
                )
            # The agent turns provider failures into coded answers: not a result to grade.
            provider_failed = str(record.get("error") or "").startswith("LLM_")
            record["status"] = "error" if provider_failed else "ok"
        except LlmError as exc:  # the repair and synthesizer suites call the gateway directly
            record.update(status="error", error=exc.code.value)
        if record["status"] == "error" and record.get("error") == "LLM_RATE_LIMITED":
            exit_code = 3
        with results_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        verdict = record.get("correct", record.get("behaviour_ok", record.get("faithful")))
        print(f"  r{repeat} {suite:<11} {item['id']:<4} {record['status']:<5} pass={verdict}")
        if exit_code == 3:
            print("Rate limited. Stopping; re-run the same command later to resume.")
            break
    write_report(run_dir)
    return run_dir, exit_code


# --- report ----------------------------------------------------------------------------------


def write_report(run_dir: Path) -> str:
    manifest = json.loads((run_dir / "manifest.json").read_text())
    latest: dict[tuple[str, str, int], dict[str, Any]] = {}
    for line in (run_dir / "results.jsonl").read_text().splitlines():
        rec = json.loads(line)
        latest[(rec["suite"], rec["id"], rec["repeat"])] = rec
    records = [r for r in latest.values() if r["status"] == "ok"]
    errors = [r for r in latest.values() if r["status"] != "ok"]
    role, model = manifest["role"], manifest["model"]
    cand = sc.score_candidate(model, role, records, manifest["limits"], resamples=1000)
    weights = sc.ROLES[role][0]

    lines = [
        f"# Eval report: {role} = `{model}`",
        "",
        f"Run `{run_dir.name}` · commit `{manifest['commit']}` · other roles on "
        f"`{manifest['production_model']}` · temperature 0 · {manifest['repeats']} repeat(s) · "
        f"paced {manifest['min_interval_s']}s/question",
        "",
        f"Prompt ids: {', '.join(f'`{v}`' for v in manifest['prompt_ids'].values())} · "
        f"datasets: {', '.join(f'{k} `{v}`' for k, v in manifest['datasets'].items())}",
        "",
        f"**Total: {cand.total:.3f}** (95% interval {cand.total_interval[0]:.3f}–"
        f"{cand.total_interval[1]:.3f}) · correctness interval "
        f"{cand.correctness_interval[0]:.3f}–{cand.correctness_interval[1]:.3f} · "
        f"eligible: **{cand.eligible}**",
        "",
        "## Gates",
        "",
        "| Gate | Pass |",
        "|---|---|",
        *[f"| {k} | {'✅' if v else '❌'} |" for k, v in cand.gates.items()],
        "",
        "## Factors",
        "",
        "| Factor | Weight | Score | Contribution |",
        "|---|---|---|---|",
        *[
            f"| {k} | {w} | {cand.factors[k]:.3f} | {w * cand.factors[k] / 100:.3f} |"
            for k, w in weights.items()
        ],
        "",
    ]
    by_tier: dict[str, list[bool]] = defaultdict(list)
    for r in records:
        if "correct" in r:
            by_tier[r["tier"]].append(bool(r["correct"]))
    if by_tier:
        lines += [
            "## Accuracy by tier",
            "",
            "| Tier | Correct | Rate |",
            "|---|---|---|",
            *[
                f"| {t} | {sum(v)}/{len(v)} | {sum(v) / len(v):.2f} |"
                for t, v in sorted(by_tier.items())
            ],
            "",
        ]
    lat = [r["latency_s"] for r in records]
    tok = [r["tokens"] for r in records]
    if lat:
        p50, worst = statistics.median(lat), sc.p95(lat)
        lines += [
            f"Latency per question: p50 {p50:.2f}s, p95 {worst:.2f}s · "
            f"tokens per question: mean {statistics.mean(tok):.0f} · "
            f"records: {len(records)} ok, {len(errors)} errored",
            "",
        ]
    fails = [
        r
        for r in records
        if r.get("correct") is False
        or r.get("behaviour_ok") is False
        or r.get("faithful") is False
        or r.get("leaked")
    ]
    lines += ["## Items that failed a check", "", "| Item | Repeat | Kind | Answer |"]
    lines += ["|---|---|---|---|"]
    fails.sort(key=lambda r: (r["suite"], r["id"], r["repeat"]))
    rows = [
        f"| {r['suite']}/{r['id']} | {r['repeat']} | {r['kind']} | "
        f"{str(r.get('answer') or '').replace('|', '/')[:120]} |"
        for r in fails
    ]
    lines += rows or ["| none | | | |"]
    text = "\n".join(lines) + "\n"
    (run_dir / "report.md").write_text(text)
    return text


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--role", required=True, choices=sorted(ROLE_SUITES))
    p.add_argument("--model", required=True)
    p.add_argument("--suites", nargs="*", help="default: the role's suites")
    p.add_argument("--repeats", type=int, default=1)
    p.add_argument("--ids", nargs="*", help="run only these item ids")
    p.add_argument("--tag", required=True)
    p.add_argument("--min-interval", type=float, default=12.0)
    p.add_argument("--yes", action="store_true")
    p.add_argument("--report-only", metavar="RUN_DIR", help="rebuild report.md; no API calls")
    args = p.parse_args(argv)
    if args.report_only:
        print(write_report(Path(args.report_only)))
        return 0
    suites = args.suites or list(ROLE_SUITES[args.role])
    run_dir, code = run(
        role=args.role,
        model=args.model,
        suites=suites,
        repeats=args.repeats,
        tag=args.tag,
        ids=args.ids,
        yes=args.yes,
        min_interval=args.min_interval,
    )
    print((run_dir / "report.md").read_text() if (run_dir / "report.md").exists() else "")
    return code


if __name__ == "__main__":
    sys.exit(main())
