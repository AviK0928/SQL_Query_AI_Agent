"""JSON log lines, the request-id context, secret redaction and the H2 fix (Phase 9)."""

import json
import logging

import pytest

from app.observability.logs import (
    QUIET_LOGGERS,
    JsonFormatter,
    StdoutHandler,
    configure_logging,
    request_id_var,
)

SECRET = "gsk_TEST_SECRET_do_not_log_456"
# Built at runtime, so the text never appears in a source line of a stack frame.
EXC_MESSAGE = "customer " + "alice@example.com"


class ListHandler(logging.Handler):
    """Keeps formatted lines, parsed back into dicts."""

    def __init__(self, formatter):
        super().__init__()
        self.setFormatter(formatter)
        self.lines = []

    def emit(self, record):
        self.lines.append(self.format(record))

    @property
    def records(self):
        return [json.loads(line) for line in self.lines]


@pytest.fixture
def capture():
    """A private logger whose lines go only to a ListHandler with a JsonFormatter."""
    logger = logging.getLogger("tests.observability")
    handler = ListHandler(JsonFormatter(secrets=[SECRET, ""]))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield logger, handler
    logger.removeHandler(handler)


@pytest.fixture
def clean_root():
    """Run with no StdoutHandler on the root logger; put back what was there after.

    Only StdoutHandlers are touched: pytest adds and removes its own capture
    handlers per test phase, so restoring a saved handler list would leak them.
    """
    root = logging.getLogger()
    saved_level = root.level
    saved_quiet = {name: logging.getLogger(name).level for name in QUIET_LOGGERS}
    removed = [h for h in root.handlers if isinstance(h, StdoutHandler)]
    for handler in removed:
        root.removeHandler(handler)
    yield root
    for handler in [h for h in root.handlers if isinstance(h, StdoutHandler)]:
        root.removeHandler(handler)
    for handler in removed:
        root.addHandler(handler)
    root.setLevel(saved_level)
    for name, level in saved_quiet.items():
        logging.getLogger(name).setLevel(level)


def test_a_line_is_one_json_object_with_the_core_fields(capture):
    logger, handler = capture
    logger.warning("cache.miss for %s", "sql_generator")
    (line,) = handler.lines
    assert "\n" not in line
    record = json.loads(line)
    assert record["level"] == "WARNING"
    assert record["logger"] == "tests.observability"
    assert record["event"] == "cache.miss for sql_generator"
    assert record["request_id"] is None
    assert record["ts"].endswith("+00:00") and len(record["ts"]) == 29  # ms precision, UTC


def test_request_id_comes_from_the_request_context(capture):
    logger, handler = capture
    token = request_id_var.set("req-ctx")
    try:
        logger.info("inside")
    finally:
        request_id_var.reset(token)
    logger.info("outside")
    assert [r["request_id"] for r in handler.records] == ["req-ctx", None]


def test_an_explicit_request_id_wins_over_the_context(capture):
    logger, handler = capture
    token = request_id_var.set("req-ctx")
    try:
        logger.info("explicit", extra={"request_id": "req-given"})
    finally:
        request_id_var.reset(token)
    assert handler.records[0]["request_id"] == "req-given"


def test_extra_fields_are_kept_but_cannot_overwrite_core_fields(capture):
    logger, handler = capture
    logger.info("http.request", extra={"url.path": "/chat", "level": "FAKE", "event": "x"})
    record = handler.records[0]
    assert record["url.path"] == "/chat"
    assert record["level"] == "INFO" and record["event"] == "http.request"


def test_an_exception_logs_its_type_and_stack_but_never_its_message(capture):
    logger, handler = capture
    try:
        raise ValueError(EXC_MESSAGE)
    except ValueError:
        logger.exception("chat.unhandled_error")
    record = handler.records[0]
    assert record["error.type"] == "ValueError"
    assert any("test_an_exception_logs" in frame for frame in record["error.stack"])
    assert "alice@example.com" not in handler.lines[0]


def test_exception_logging_outside_a_handler_adds_no_error_fields(capture):
    logger, handler = capture
    logger.exception("no active exception")  # exc_info is (None, None, None)
    assert "error.type" not in handler.records[0]


def test_secret_values_are_redacted_in_every_field(capture):
    logger, handler = capture
    logger.info("key %s", SECRET, extra={"detail": {"nested": f"Bearer {SECRET}"}})
    assert SECRET not in handler.lines[0]
    assert handler.records[0]["event"] == "key ***"
    assert handler.records[0]["detail"] == {"nested": "Bearer ***"}


def test_the_handler_writes_to_the_current_stdout(capsys):
    handler = StdoutHandler()
    handler.setFormatter(JsonFormatter())
    handler.handle(logging.makeLogRecord({"msg": "hello", "levelname": "INFO"}))
    out = capsys.readouterr().out
    assert out.endswith("\n")
    assert json.loads(out)["event"] == "hello"


def test_a_line_that_cannot_be_formatted_is_reported_not_raised(capsys, monkeypatch):
    monkeypatch.setattr(logging, "raiseExceptions", True)
    handler = StdoutHandler()
    handler.setFormatter(JsonFormatter())
    bad = logging.makeLogRecord({"msg": "%s and %s", "args": ("only one",)})
    handler.handle(bad)  # must not raise into the caller
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "Logging error" in captured.err


def test_configure_installs_exactly_one_json_handler(clean_root):
    before = list(clean_root.handlers)
    first = configure_logging(secrets=[SECRET])
    second = configure_logging(secrets=[SECRET])
    json_handlers = [h for h in clean_root.handlers if isinstance(h, StdoutHandler)]
    assert json_handlers == [second] and first is not second
    assert [h for h in clean_root.handlers if h not in json_handlers] == before
    assert clean_root.level == logging.INFO
    assert second.formatter.secrets == (SECRET,)


def test_sqlglot_warnings_never_reach_the_log(clean_root, capsys):
    """H2: sqlglot's raw-command fallback warning contains SQL text."""
    configure_logging()
    logging.getLogger("sqlglot").warning(
        "'SELECT secret_column FROM t' contains unsupported syntax"
    )
    logging.getLogger("sqlglot").error("an actual sqlglot error")
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["event"] for line in lines] == ["an actual sqlglot error"]
    assert logging.getLogger("sqlglot").level == logging.ERROR
