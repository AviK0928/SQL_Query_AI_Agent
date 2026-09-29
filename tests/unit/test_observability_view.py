"""The call-log viewer: parsing, grouping and rendering (D56)."""

import io
import json

from app.observability.view import (
    NO_ID,
    group_by_request,
    main,
    parse_calls,
    render_call,
    render_request,
)


def call(request_id="req-1", **fields):
    record = {
        "ts": "2026-09-27T10:15:30.123+00:00",
        "event": "llm.call",
        "request_id": request_id,
        "llm.role": "sql_generator",
        "llm.prompt_id": "sql_gen@5fb4fe06",
        "gen_ai.request.model": "big",
        "gen_ai.response.model": "big",
        "gen_ai.usage.input_tokens": 120,
        "gen_ai.usage.output_tokens": 9,
        "llm.attempts": 1,
        "llm.fallback_used": False,
        "llm.cache_hit": False,
        "llm.latency_ms": 842,
    }
    record.update(fields)
    return record


def with_content(**fields):
    return call(
        **{
            "gen_ai.input.messages": [
                {"role": "system", "content": "You write SQLite.\nRules..."},
                {"role": "user", "content": "How many customers?"},
            ],
            "gen_ai.output.text": "SELECT COUNT(*)\nFROM customers",
            **fields,
        }
    )


def test_parse_keeps_only_llm_call_records_and_ignores_prefixes():
    lines = [
        "2026-09-27T10:15:30Z " + json.dumps(call()),
        json.dumps({"event": "http.request", "request_id": "req-1"}),
        "plain uvicorn text line",
        "INFO: {not json",
        "[1, 2]",
        json.dumps(call("req-2")),
    ]
    assert [c["request_id"] for c in parse_calls(lines)] == ["req-1", "req-2"]


def test_calls_are_grouped_by_request_in_first_seen_order():
    groups = group_by_request([call("b"), call("a"), call("b"), call(None)])
    assert list(groups) == ["b", "a", NO_ID]
    assert len(groups["b"]) == 2


def test_a_call_with_content_shows_messages_and_reply_but_folds_the_system_prompt():
    lines = render_call(1, with_content(), full=False)
    assert lines[0] == (
        "[1] 10:15:30.123 sql_generator big sql_gen@5fb4fe06 120->9 tokens 842 ms (attempts 1)"
    )
    assert lines[1:] == [
        "  --> system: [sql_gen@5fb4fe06, 26 chars; --full shows it]",
        "  --> user: How many customers?",
        "  <-- reply: SELECT COUNT(*)",
        "      FROM customers",
    ]


def test_full_shows_the_system_prompt():
    lines = render_call(1, with_content(), full=True)
    assert lines[1:3] == ["  --> system: You write SQLite.", "      Rules..."]


def test_cache_hits_fallbacks_and_empty_messages_are_labelled():
    lines = render_call(
        2,
        call(
            **{
                "llm.cache_hit": True,
                "llm.fallback_used": True,
                "gen_ai.input.messages": [{"role": "user", "content": ""}],
            }
        ),
        full=False,
    )
    assert lines[0].endswith("(cache hit, fallback)")
    assert lines[1:] == ["  --> user: "]


def test_without_logged_content_the_viewer_says_how_to_get_it():
    assert render_call(1, call(), full=False)[1] == (
        "  (content not logged: run with LLM_LOG_CONTENT=true)"
    )


def test_a_failed_call_shows_its_error_code_and_detail():
    failed = {
        "ts": "2026-09-27T10:15:31.000+00:00",
        "event": "llm.call",
        "request_id": "req-1",
        "llm.role": "synthesizer",
        "llm.prompt_id": "answer@3c3a3566",
        "gen_ai.request.model": "big",
        "error.type": "LLM_RATE_LIMITED",
        "error.detail": "rate limited on big",
        "llm.latency_ms": 15,
        "gen_ai.input.messages": [{"role": "user", "content": "rows..."}],
    }
    lines = render_call(3, failed, full=False)
    assert lines[0] == (
        "[3] 10:15:31.000 synthesizer big answer@3c3a3566 FAILED LLM_RATE_LIMITED "
        "after 15 ms: rate limited on big"
    )
    assert lines[1:] == ["  --> user: rows..."], "no reply line for a failed call"


def test_a_request_is_rendered_with_a_header_and_numbered_calls():
    text = render_request("req-9", [call("req-9"), call("req-9")], full=False)
    assert text.splitlines()[0] == "=== request req-9: 2 LLM call(s)"
    assert "\n[2] " in text


def test_main_reads_a_file_and_selects_one_request(tmp_path, capsys):
    path = tmp_path / "calls.jsonl"
    path.write_text("\n".join(json.dumps(call(r)) for r in ["a", "b", "c"]) + "\n")
    assert main([str(path), "--request-id", "b"]) == 0
    out = capsys.readouterr().out
    assert "=== request b:" in out and "=== request a:" not in out


def test_main_shows_the_last_requests_from_stdin(capsys):
    stdin = io.StringIO("\n".join(json.dumps(call(r)) for r in ["a", "b", "c"]))
    assert main(["--last", "2"], stdin=stdin) == 0
    out = capsys.readouterr().out
    assert [line.split(":")[0] for line in out.splitlines() if line.startswith("===")] == [
        "=== request b",
        "=== request c",
    ]


def test_main_reads_the_process_stdin_by_default(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(call("z"))))
    assert main([]) == 0
    assert "=== request z:" in capsys.readouterr().out


def test_an_unknown_request_id_is_reported(tmp_path, capsys):
    path = tmp_path / "calls.jsonl"
    path.write_text(json.dumps(call("a")) + "\n")
    assert main([str(path), "--request-id", "missing"]) == 1
    assert capsys.readouterr().out == "no LLM calls logged for request missing\n"


def test_no_calls_or_last_zero_is_reported(capsys):
    assert main(["-"], stdin=io.StringIO("not a log line\n")) == 1
    assert main(["--last", "0"], stdin=io.StringIO(json.dumps(call()))) == 1
    assert capsys.readouterr().out == "no LLM calls found\nno LLM calls found\n"
