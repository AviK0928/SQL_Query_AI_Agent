"""Judge runs: build the calibration set, judge cases live, measure agreement.

    python -m evals.judges.run build --sheet /content/calibration_v1_labels.csv
    python -m evals.judges.run judge --model qwen/qwen3.8-27b --tag judge-calib-qwen [--yes]
    python -m evals.judges.run agree --run evals/reports/<date>-judge-calib-qwen \
        --labels evals/judges/calibration_v1_labels.csv

`judge` follows the eval runner's rules (D36, Section 8): a pre-run estimate
and confirmation, pacing between calls, the judge model set for this run only
with no fallback, a response cache and a call log in the run folder, one
checkpointed line per case in judgments.jsonl, resume by re-running the same
command, and a stop on a rate limit (exit 3).
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from evals.judges import calibration as cal
from evals.judges import response as jr

HERE = Path(__file__).resolve().parent
EVALS = HERE.parent
CASES = HERE / "calibration_v1.jsonl"
REPORTS = EVALS / "reports"
EST_OUTPUT_TOKENS = 400
CHARS_PER_TOKEN = 3.5


def build(sheet: Path, cases_path: Path = CASES, reports: Path = REPORTS) -> list[dict[str, Any]]:
    """Select the items from the baseline run, re-run their SQL, write the cases
    file and the blank label sheet."""
    from app.config import load_settings
    from app.sql.executor import ReadOnlyExecutor
    from app.sql.validator import validate_sql
    from evals.runner import load_suite, reference

    settings = load_settings()
    executor = ReadOnlyExecutor.from_settings(settings)
    tables = sorted(executor.allowed_tables())
    items = {i["id"]: i for s in ("golden", "adversarial") for i in load_suite(s)}

    def run_query(sql: str) -> Any:
        return executor.execute(
            validate_sql(sql, allowed_tables=tables, max_rows=settings.max_rows)
        )

    def ref_rows(sql: str) -> int:
        return len(reference(sql, settings.db_path)[1])

    records = cal.load_jsonl(reports / cal.BASELINE_RUN / "results.jsonl")
    cases = [build_one(r, items, run_query, ref_rows) for r in cal.select_items(records)]
    cases_path.write_text(
        "".join(json.dumps(c, ensure_ascii=False, default=str) + "\n" for c in cases)
    )
    cal.write_label_sheet(cases, sheet)
    return cases


def build_one(record: Any, items: Any, run_query: Any, ref_rows: Any) -> dict[str, Any]:
    return cal.build_case(record, items[record["id"]], run_query, ref_rows)


def estimate(cases: Sequence[dict[str, Any]]) -> int:
    chars = sum(
        sum(len(m["content"]) for m in jr.build_judge_messages(cal.to_judge_case(c))) for c in cases
    )
    return int(chars / CHARS_PER_TOKEN) + EST_OUTPUT_TOKENS * len(cases)


def done_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {r["id"] for r in cal.load_jsonl(path) if r.get("status") == "ok"}


def judge(
    *,
    model: str,
    tag: str,
    cases_path: Path = CASES,
    yes: bool = False,
    min_interval: float = 25.0,
    base_settings: Any = None,
    make_llm: Callable[[Any], Any] | None = None,
    reports: Path = REPORTS,
    today: str | None = None,
    sleep: Callable[[float], None] = time.sleep,
    confirm: Callable[[str], str] = input,
) -> tuple[Path, int]:
    """Judge every case. Returns (run_dir, exit_code): 0 done, 1 aborted, 3 rate limited."""
    from app.config import load_settings
    from app.llm.client import LlmError, LlmErrorCode
    from app.llm.registry import LlmRole
    from evals.runner import git_commit, sha

    base = base_settings or load_settings()
    if model not in base.llm_limits:
        raise SystemExit(f"LLM_LIMITS has no entry for {model}; add it from the Groq console")
    cases = cal.load_jsonl(cases_path)
    run_dir = reports / f"{today or datetime.now(UTC).date().isoformat()}-{tag}"
    run_dir.mkdir(parents=True, exist_ok=True)
    out = run_dir / "judgments.jsonl"
    done = done_ids(out)
    todo = [c for c in cases if c["id"] not in done]

    lim = base.llm_limits[model]
    print(f"Judge {model} | {len(todo)} to judge, {len(done)} done -> {run_dir}")
    print(
        f"Estimate: {len(todo)} calls, ~{estimate(todo):,} tokens | daily limits "
        f"{lim.rpd} requests, {lim.tpd:,} tokens | pacing {min_interval}s "
        f"(~{len(todo) * min_interval / 60:.0f} min)"
    )
    if not todo:
        return run_dir, 0
    if not yes and confirm("Proceed? [y/N] ").strip().lower() != "y":
        print("Aborted.")
        return run_dir, 1

    manifest = {
        "judge_model": model,
        "rubric": f"{jr.RUBRIC_NAME}.v{jr.RUBRIC_VERSION}",
        "judge_prompt_id": jr.JUDGE_PROMPT_ID,
        "temperature": 0,
        "cases": cases_path.name,
        "cases_sha": sha(cases_path),
        "commit": git_commit(),
        "limits": lim.model_dump(),
        "min_interval_s": min_interval,
        "started_at": datetime.now(UTC).isoformat(timespec="seconds"),
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n")
    settings = base.model_copy(
        update={
            "llm_role_models": {**base.llm_role_models, LlmRole.JUDGE: model},
            "groq_fallback_model": None,
            "llm_cache_path": run_dir / "cache.sqlite",
            "llm_log_content": True,
            "llm_log_path": run_dir / "calls.jsonl",
        }
    )
    if make_llm is None:
        from app.agent import build_llm

        make_llm = build_llm
    llm = make_llm(settings)
    code = 0
    for n, case in enumerate(todo):
        if n:
            sleep(min_interval)
        record: dict[str, Any] = {"id": case["id"], "model": model}
        try:
            res = jr.judge_response(llm, cal.to_judge_case(case), request_id=f"judge-{case['id']}")
            record.update(
                status="ok",
                verdict=res.verdict.model_dump() if res.verdict else None,
                parse_error=res.error,
                raw=res.raw,
                tokens=res.tokens,
            )
        except LlmError as exc:
            record.update(status="error", error=exc.code.value)
            if exc.code == LlmErrorCode.RATE_LIMITED:
                code = 3
        with out.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        # Blind until labels are in: progress only, never scores.
        note = " (parse failure)" if record.get("parse_error") else ""
        print(f"  {case['id']:<4} {record['status']}{note}")
        if code == 3:
            print("Rate limited. Stopping; re-run the same command later to resume.")
            break
    return run_dir, code


def agree(run_dir: Path, labels_path: Path, cases_path: Path = CASES) -> dict[str, Any]:
    ids = [c["id"] for c in cal.load_jsonl(cases_path)]
    labels = cal.read_labels(labels_path, ids)
    verdicts: dict[str, jr.Verdict | None] = {}
    parse_failures = 0
    for rec in cal.load_jsonl(run_dir / "judgments.jsonl"):
        if rec.get("status") != "ok":
            continue
        verdicts[rec["id"]] = jr.Verdict.model_validate(rec["verdict"]) if rec["verdict"] else None
        parse_failures += rec["verdict"] is None
    missing = [i for i in ids if i not in verdicts]
    result = cal.agreement(labels, verdicts)
    lines = [
        f"# Judge calibration: {run_dir.name}",
        "",
        f"{len(ids)} items; parse failures {parse_failures}; not judged {len(missing)}. "
        f"Trusted when within one point >= {cal.WITHIN_ONE_MIN:.0%} and pass/fail match "
        f">= {cal.PASS_MATCH_MIN:.0%} (D44).",
        "",
        "| Criterion | Within 1 | Pass/fail match | Trusted |",
        "|---|---|---|---|",
    ]
    for c, r in result.items():
        trusted = "yes" if r["trusted"] else "no"
        lines.append(f"| {c} | {r['within_one']:.0%} | {r['pass_match']:.0%} | {trusted} |")
    (run_dir / "agreement.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return {"criteria": result, "parse_failures": parse_failures, "missing": missing}


def main(argv: Sequence[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--sheet", type=Path, required=True)
    j = sub.add_parser("judge")
    j.add_argument("--model", required=True)
    j.add_argument("--tag", required=True)
    j.add_argument("--min-interval", type=float, default=25.0)
    j.add_argument("--date", help="resume a run folder from an earlier UTC day")
    j.add_argument("--yes", action="store_true")
    a = sub.add_parser("agree")
    a.add_argument("--run", type=Path, required=True)
    a.add_argument("--labels", type=Path, required=True)
    args = p.parse_args(argv)
    if args.cmd == "build":
        cases = build(args.sheet)
        print(
            f"{len(cases)} cases -> {CASES.relative_to(EVALS.parent)}; label sheet -> {args.sheet}"
        )
        return 0
    if args.cmd == "judge":
        _, code = judge(
            model=args.model,
            tag=args.tag,
            min_interval=args.min_interval,
            yes=args.yes,
            today=args.date,
        )
        return code
    agree(args.run, args.labels)
    return 0


if __name__ == "__main__":
    sys.exit(main())
