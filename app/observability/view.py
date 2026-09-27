"""Print what the app asked the LLM and what it got back, one request at a time (D56).

Reads LLM call-log lines (app/llm/calllog.py) from a file or stdin: a
`LLM_LOG_PATH` file, the uvicorn log from notebook Cell 10, or log text copied
from Render's log viewer. Anything before the first `{` on a line (a platform
timestamp) is ignored, and so is every line that is not an `llm.call` record.
Calls are grouped by `request_id`, the id in the `X-Request-ID` header.

    python -m app.observability.view calls.jsonl                   # last 5 requests
    python -m app.observability.view calls.jsonl --request-id ID   # one request
    python -m app.observability.view calls.jsonl --last 1 --full   # with system prompts

System prompts are long and identified by their prompt id, so they are shown
as one summary line unless --full is given. Messages and replies appear only if
the app ran with LLM_LOG_CONTENT=true.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Iterable, Sequence
from typing import Any, TextIO

NO_ID = "(no request id)"
INDENT = "      "


def parse_calls(lines: Iterable[str]) -> list[dict[str, Any]]:
    """The llm.call records among the lines, in order."""
    calls: list[dict[str, Any]] = []
    for line in lines:
        start = line.find("{")
        if start < 0:
            continue
        try:
            record = json.loads(line[start:])
        except json.JSONDecodeError:
            continue
        if record.get("event") == "llm.call":  # text from a "{" parses to an object or fails
            calls.append(record)
    return calls


def group_by_request(calls: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """request_id -> its calls, requests in the order they first appear."""
    groups: dict[str, list[dict[str, Any]]] = {}
    for call in calls:
        groups.setdefault(call.get("request_id") or NO_ID, []).append(call)
    return groups


def _block(arrow: str, label: str, text: str) -> list[str]:
    lines = text.splitlines() or [""]
    return [f"  {arrow} {label}: {lines[0]}"] + [INDENT + line for line in lines[1:]]


def render_call(number: int, call: dict[str, Any], *, full: bool) -> list[str]:
    time = str(call.get("ts", ""))[11:23]
    role = call.get("llm.role", "?")
    prompt = call.get("llm.prompt_id", "?")
    latency = call.get("llm.latency_ms", "?")
    if "error.type" in call:
        model = call.get("gen_ai.request.model", "?")
        head = f"[{number}] {time} {role} {model} {prompt} FAILED {call['error.type']}"
        out = [f"{head} after {latency} ms: {call.get('error.detail', '')}"]
    else:
        model = call.get("gen_ai.response.model", "?")
        tokens_in, tokens_out = (
            call.get("gen_ai.usage.input_tokens"),
            call.get("gen_ai.usage.output_tokens"),
        )
        tokens = f"{tokens_in}->{tokens_out}"
        attempts = call.get("llm.attempts")
        source = "cache hit" if call.get("llm.cache_hit") else f"attempts {attempts}"
        if call.get("llm.fallback_used"):
            source += ", fallback"
        out = [f"[{number}] {time} {role} {model} {prompt} {tokens} tokens {latency} ms ({source})"]
    messages = call.get("gen_ai.input.messages")
    if messages is None:
        return out + ["  (content not logged: run with LLM_LOG_CONTENT=true)"]
    for message in messages:
        kind, text = message.get("role", "?"), str(message.get("content", ""))
        if kind == "system" and not full:
            out.append(f"  --> system: [{prompt}, {len(text)} chars; --full shows it]")
        else:
            out += _block("-->", kind, text)
    if "gen_ai.output.text" in call:
        out += _block("<--", "reply", str(call["gen_ai.output.text"]))
    return out


def render_request(request_id: str, calls: list[dict[str, Any]], *, full: bool) -> str:
    lines = [f"=== request {request_id}: {len(calls)} LLM call(s)"]
    for number, call in enumerate(calls, start=1):
        lines += render_call(number, call, full=full)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, stdin: TextIO | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.observability.view", description=__doc__)
    parser.add_argument("path", nargs="?", default="-", help="call-log file, or - for stdin")
    parser.add_argument("--request-id", help="show only this request")
    parser.add_argument("--last", type=int, default=5, help="show the last N requests (5)")
    parser.add_argument("--full", action="store_true", help="also print system prompts")
    args = parser.parse_args(argv)

    if args.path == "-":
        calls = parse_calls(stdin if stdin is not None else sys.stdin)
    else:
        with open(args.path, encoding="utf-8") as fh:
            calls = parse_calls(fh)
    groups = group_by_request(calls)

    if args.request_id is not None:
        if args.request_id not in groups:
            print(f"no LLM calls logged for request {args.request_id}")
            return 1
        selected = [args.request_id]
    else:
        selected = list(groups)[-args.last :] if args.last > 0 else []
    if not selected:
        print("no LLM calls found")
        return 1
    print("\n\n".join(render_request(rid, groups[rid], full=args.full) for rid in selected))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
