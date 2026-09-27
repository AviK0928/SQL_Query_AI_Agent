"""Post-deploy smoke test (Phase 10, D57).

    python -m app.smoke https://<service>.onrender.com
    python -m app.smoke https://<service>.onrender.com --question "How many customers are there?"

Without --question it checks only GET /health, which touches neither the
database nor the LLM. With --question it also asks one question through
POST /chat (about two LLM calls from the daily quota) and checks the answer:
HTTP 200, no error code, a non-empty answer, and a request id that matches the
X-Request-ID header, so the call log can be read for it with
`python -m app.observability.view`. The timeout allows for a free instance
waking up (30-60 s). Exit code 0 when every check passed, 1 otherwise.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from typing import Any

TIMEOUT_S = 90.0
Opener = Callable[..., Any]  # urllib.request.urlopen, or a fake in tests


def fetch(
    opener: Opener, url: str, body: dict[str, Any] | None, timeout: float
) -> tuple[int, dict[str, str], Any]:
    """(status, headers, parsed JSON or None) for one request; HTTP errors are returned."""
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(  # nosec B310: the scheme is checked in main()
        url, data=data, headers={"Content-Type": "application/json"} if data else {}
    )
    try:
        with opener(request, timeout=timeout) as response:  # nosec B310: scheme checked in main
            status, headers, raw = response.status, dict(response.headers), response.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
        headers = dict(exc.headers.items()) if exc.headers else {}
    try:
        parsed = json.loads(raw) if raw else None
    except ValueError:
        parsed = None
    return status, headers, parsed


def check_health(opener: Opener, base: str, timeout: float) -> str | None:
    """None if /health is fine, else what was wrong."""
    status, _, body = fetch(opener, f"{base}/health", None, timeout)
    if status != 200 or body != {"status": "ok"}:
        return f"GET /health returned {status} {body!r}"
    return None


def check_chat(opener: Opener, base: str, question: str, timeout: float) -> tuple[str | None, str]:
    """(None or what was wrong, a one-line summary of the answer)."""
    status, headers, body = fetch(opener, f"{base}/chat", {"question": question}, timeout)
    header_id = {k.lower(): v for k, v in headers.items()}.get("x-request-id")
    if status != 200 or not isinstance(body, dict):
        return f"POST /chat returned {status} {body!r}", ""
    summary = f"request_id={body.get('request_id')} answer: {body.get('answer')!r}"
    if body.get("error") is not None:
        return f"POST /chat answered with error {body['error']}", summary
    if not body.get("answer"):
        return "POST /chat returned an empty answer", summary
    if header_id is None or header_id != body.get("request_id"):
        return "X-Request-ID header does not match the body's request_id", summary
    return None, summary


def main(argv: Sequence[str] | None = None, opener: Opener = urllib.request.urlopen) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.smoke", description=__doc__)
    parser.add_argument("base_url", help="e.g. https://<service>.onrender.com")
    parser.add_argument("--question", help="also ask this question (spends LLM quota)")
    parser.add_argument("--timeout", type=float, default=TIMEOUT_S)
    args = parser.parse_args(argv)
    base = args.base_url.rstrip("/")
    if not base.startswith(("https://", "http://")):
        print(f"FAIL base URL must start with https:// or http://: {base}")
        return 1

    failures = 0
    started = time.monotonic()
    try:
        problem = check_health(opener, base, args.timeout)
    except OSError as exc:  # unreachable, DNS, timeout
        problem = f"GET /health failed: {type(exc).__name__}"
    print(f"{'FAIL' if problem else 'ok  '} GET /health ({time.monotonic() - started:.1f} s)")
    if problem:
        print(f"     {problem}")
        failures += 1

    if args.question and not failures:
        started = time.monotonic()
        try:
            problem, summary = check_chat(opener, base, args.question, args.timeout)
        except OSError as exc:
            problem, summary = f"POST /chat failed: {type(exc).__name__}", ""
        print(f"{'FAIL' if problem else 'ok  '} POST /chat ({time.monotonic() - started:.1f} s)")
        if summary:
            print(f"     {summary}")
        if problem:
            print(f"     {problem}")
            failures += 1
    elif not args.question:
        print("     (no --question: /chat not checked, no LLM quota spent)")

    print("SMOKE TEST PASSED" if not failures else "SMOKE TEST FAILED")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
