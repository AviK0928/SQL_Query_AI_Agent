"""Tests for the LangGraph agent.

No real API calls. Each test builds an Agent with the shared FakeLLM
(tests/fakes.py) injected through its constructor, and conftest.py blocks the
network, so the suite stays free, offline and deterministic. Everything below the model is real:
the validator runs, the graph routes, and queries hit the actual SQLite file.
"""

import pytest

from app.agent import (
    LLM_ERROR_REPLIES,
    READ_ONLY_REPLY,
    SUMMARY_UNAVAILABLE_REPLY,
    Agent,
    build_llm,
)
from app.config import load_settings
from app.llm.client import LlmError, LlmErrorCode
from app.llm.gateway import LlmGateway
from app.llm.registry import LlmRole
from app.prompts import ANSWER_PROMPT_ID, RETRY_PROMPT_ID, SQL_PROMPT_ID
from app.sql.errors import USER_MESSAGES, SqlErrorCode
from app.sql.executor import ReadOnlyExecutor
from tests.fakes import TEST_LLM_LIMITS, FakeLLM


@pytest.fixture
def make_agent(test_settings):
    """Builds an Agent around a scripted FakeLLM. No module globals to reset."""

    def build(fake, **overrides):
        settings = test_settings
        if overrides:
            settings = load_settings(
                env_file=None,
                groq_api_key="test-key-not-real",
                groq_model="fake/test-model",
                llm_limits=TEST_LLM_LIMITS,
                **overrides,
            )
        return Agent(settings, llm=fake)

    return build


@pytest.fixture
def sql_seen(monkeypatch):
    """Records every SQL string that reaches the database, then calls through."""
    seen = []
    real = ReadOnlyExecutor.execute

    def spy(self, query):
        seen.append(query.sql)
        return real(self, query)

    monkeypatch.setattr(ReadOnlyExecutor, "execute", spy)
    return seen


# --- 1. happy path ------------------------------------------------------


def test_happy_path_generates_sql_and_answers(make_agent):
    fake = FakeLLM(
        "SELECT name, city FROM customers LIMIT 5",
        "There are five customers listed.",
    )
    bot = make_agent(fake)

    result = bot.ask("Show all customers")

    assert result["sql"].upper().startswith("SELECT")
    assert result["error"] is None
    assert result["out_of_scope"] is False
    assert result["rows"], "expected real rows from ecommerce.db"
    assert result["columns"] == ["name", "city"]
    assert result["answer"].strip()
    assert fake.call_count == 2, "generate + format_answer, no retry"


def test_sql_returned_is_the_sql_that_ran(make_agent, sql_seen):
    """The row cap is code-owned: a query without LIMIT runs with cap + 1."""
    fake = FakeLLM("SELECT name FROM customers", "All customers.")
    bot = make_agent(fake)

    result = bot.ask("List customers")

    assert result["sql"] == "SELECT name FROM customers LIMIT 201"
    assert sql_seen == [result["sql"]]


# --- 2. out of scope ----------------------------------------------------


def test_out_of_scope_skips_the_database(make_agent, sql_seen):
    fake = FakeLLM("OUT_OF_SCOPE")
    bot = make_agent(fake)

    result = bot.ask("Write me some Python code")

    assert result["out_of_scope"] is True
    assert result["sql"] is None
    assert result["rows"] == []
    assert "e-commerce database" in result["answer"]
    assert sql_seen == [], "database must not be touched for out-of-scope questions"
    assert fake.call_count == 1, "no answer-formatting call needed"


def test_read_only_request_gets_a_read_only_message(make_agent, sql_seen):
    """Write requests are on-topic but forbidden, so the reply must say so."""
    fake = FakeLLM("READ_ONLY")
    bot = make_agent(fake)

    result = bot.ask("delete the most expensive order")

    assert result["out_of_scope"] is True
    assert result["sql"] is None
    assert "only read" in result["answer"]
    assert "e-commerce database" not in result["answer"], "wrong message: that is the off-topic one"
    assert sql_seen == []
    assert fake.call_count == 1


# --- 3. retry succeeds --------------------------------------------------


def test_retry_after_bad_column_succeeds(make_agent):
    fake = FakeLLM(
        "SELECT revenue FROM customers",  # valid shape, column does not exist
        "SELECT name FROM customers LIMIT 3",  # corrected
        "Three customers were found.",
    )
    bot = make_agent(fake)

    result = bot.ask("Show me revenue")

    assert result["error"] is None
    assert result["sql"] == "SELECT name FROM customers LIMIT 3"
    assert len(result["rows"]) == 3
    assert fake.call_count == 3, "generate + retry + format_answer"


def test_retry_receives_the_database_error(make_agent):
    fake = FakeLLM(
        "SELECT revenue FROM customers",
        "SELECT name FROM customers LIMIT 1",
        "One customer.",
    )
    bot = make_agent(fake)
    bot.ask("Show me revenue")

    retry_messages = fake.calls[1]
    assert any("no such column: revenue" in m["content"] for m in retry_messages)


def test_unknown_table_is_repaired(make_agent):
    fake = FakeLLM(
        "SELECT name FROM buyers",  # schema-name mismatch: users say buyers
        "SELECT name FROM customers LIMIT 2",
        "Two customers.",
    )
    bot = make_agent(fake)

    result = bot.ask("Show two buyers")

    assert result["error"] is None
    assert any("Available tables" in m["content"] for m in fake.calls[1])
    assert fake.call_count == 3


def test_retry_can_decline_as_out_of_scope(make_agent):
    fake = FakeLLM(
        "SELECT revenue FROM customers",  # fails: no such column
        "OUT_OF_SCOPE",  # the retry concludes the schema cannot answer it
    )
    bot = make_agent(fake)

    result = bot.ask("Show me revenue")

    assert result["out_of_scope"] is True
    assert result["error"] is None
    assert result["sql"] is None
    assert "e-commerce database" in result["answer"]
    assert fake.call_count == 2, "generate + retry; no answer-formatting call"


# --- 4. retry exhausted -------------------------------------------------


def test_retry_exhausted_returns_error_without_a_third_call(make_agent):
    fake = FakeLLM(
        "SELECT revenue FROM customers",
        "SELECT still_wrong FROM customers",
    )
    bot = make_agent(fake)

    result = bot.ask("Show me revenue")

    assert result["error"] == SqlErrorCode.EXECUTION_ERROR
    assert "couldn't run a query" in result["answer"]
    assert "no such column" not in result["answer"], "database text must not reach users"
    assert fake.call_count == 2, "one retry only; no LLM call to format an error"


# --- 5. dangerous SQL: final, never retried ------------------------------


def test_dangerous_sql_never_reaches_the_database(make_agent, sql_seen):
    """Worst case: the model complies. The write is rejected and not retried."""
    fake = FakeLLM(
        "DROP TABLE customers",
        "SELECT name FROM customers LIMIT 2",  # must never be requested
    )
    bot = make_agent(fake)

    result = bot.ask("Delete all customers")

    assert sql_seen == []
    assert result["error"] == SqlErrorCode.FORBIDDEN_WRITE
    assert result["answer"] == READ_ONLY_REPLY
    assert result["sql"] is None, "rejected SQL is never presented as the query that ran"
    assert fake.call_count == 1, "a forbidden write earns no retry"


def test_stacked_statements_are_rejected_without_retry(make_agent, sql_seen):
    fake = FakeLLM(
        "SELECT 1; DROP TABLE customers",
        "SELECT name FROM customers LIMIT 1",  # must never be requested
    )
    bot = make_agent(fake)

    result = bot.ask("Show a customer")

    assert sql_seen == []
    assert result["error"] == SqlErrorCode.MULTIPLE_STATEMENTS
    assert fake.call_count == 1


def test_timeout_is_final_and_not_retried(make_agent):
    fake = FakeLLM(
        "WITH RECURSIVE c(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM c) SELECT COUNT(*) FROM c",
        "SELECT 1",  # must never be requested
    )
    bot = make_agent(fake, query_timeout_s=0.2)

    result = bot.ask("Count forever")

    assert result["error"] == SqlErrorCode.TIMEOUT
    assert result["answer"] == USER_MESSAGES[SqlErrorCode.TIMEOUT]
    assert fake.call_count == 1


# --- 6. honest partial results ------------------------------------------


def test_truncation_flows_through(make_agent):
    fake = FakeLLM("SELECT id FROM customers ORDER BY id", "Two customers shown.")
    bot = make_agent(fake, max_rows=2)

    result = bot.ask("List customer ids")

    assert result["truncated"] is True
    assert len(result["rows"]) == 2
    assert "truncated" in fake.calls[1][-1]["content"]


def test_limit_reached_flows_through_to_the_answer_prompt(make_agent):
    fake = FakeLLM("SELECT name FROM customers ORDER BY id LIMIT 3", "Three customers.")
    bot = make_agent(fake)

    result = bot.ask("Some customers")

    assert result["limit_reached"] is True
    assert result["truncated"] is False
    assert "LIMIT was reached" in fake.calls[1][-1]["content"]


# --- 7. markdown fences -------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "```sql\nSELECT name FROM customers LIMIT 2\n```",
        "```\nSELECT name FROM customers LIMIT 2\n```",
        "  SELECT name FROM customers LIMIT 2  ",
    ],
)
def test_markdown_fences_and_whitespace_are_stripped(make_agent, raw):
    fake = FakeLLM(raw, "Two customers.")
    bot = make_agent(fake)

    result = bot.ask("Show two customers")

    assert result["sql"] == "SELECT name FROM customers LIMIT 2"
    assert result["error"] is None


# --- 8. conversation history --------------------------------------------


def test_history_is_replayed_to_the_model(make_agent):
    fake = FakeLLM(
        "SELECT name FROM customers WHERE city = 'Pune' LIMIT 5",
        "Two customers in Pune.",
    )
    bot = make_agent(fake)

    history = [
        {
            "question": "customers in Mumbai",
            "sql": "SELECT name FROM customers WHERE city = 'Mumbai'",
        }
    ]
    bot.ask("and Pune?", history)

    roles = [m["role"] for m in fake.calls[0]]
    assert roles == ["system", "user", "assistant", "user"]
    assert fake.calls[0][1]["content"] == "customers in Mumbai"
    assert fake.calls[0][-1]["content"] == "and Pune?"


def test_history_is_truncated_to_max_turns(make_agent):
    fake = FakeLLM("SELECT name FROM customers LIMIT 1", "One customer.")
    bot = make_agent(fake)

    history = [{"question": f"q{i}", "sql": f"SELECT {i}"} for i in range(10)]
    bot.ask("latest question", history)

    replayed = [m["content"] for m in fake.calls[0] if m["role"] == "user"]
    assert "q0" not in replayed, "old turns must be dropped"
    assert "q9" in replayed, "most recent turn must be kept"
    assert len(replayed) == bot.settings.max_history_turns + 1


# --- 9. LLM gateway wiring (Phase 4) ------------------------------------


def test_each_node_calls_the_model_for_its_role(make_agent):
    fake = FakeLLM("SELECT revenue FROM customers", "SELECT name FROM customers LIMIT 1", "One.")
    make_agent(fake).ask("Show me revenue")
    assert fake.roles == [LlmRole.SQL_GENERATOR, LlmRole.SQL_REPAIR, LlmRole.SYNTHESIZER]
    assert [k["prompt_id"] for k in fake.kwargs] == [
        SQL_PROMPT_ID,
        RETRY_PROMPT_ID,
        ANSWER_PROMPT_ID,
    ]


def test_every_call_carries_the_schema_hash_and_one_request_id(make_agent):
    fake = FakeLLM("SELECT name FROM customers LIMIT 1", "One.", "SELECT 1", "One.")
    bot = make_agent(fake)
    first = bot.ask("q1")
    second = bot.ask("q2")
    hashes = {k["schema_hash"] for k in fake.kwargs}
    assert hashes == {bot.schema_hash} and len(bot.schema_hash) == 12
    assert [k["request_id"] for k in fake.kwargs] == [first["request_id"]] * 2 + [
        second["request_id"]
    ] * 2
    assert first["request_id"] != second["request_id"]
    assert all(k["temperature"] == 0 for k in fake.kwargs)


def test_usage_totals_this_question_only(make_agent):
    fake = FakeLLM("SELECT name FROM customers LIMIT 1", "One.")
    result = make_agent(fake).ask("One customer")
    assert result["usage"] == {
        "calls": 2,
        "cache_hits": 0,
        "input_tokens": 200,
        "output_tokens": 20,
    }


@pytest.mark.parametrize("code", list(LlmErrorCode))
def test_llm_failure_during_generation_becomes_a_coded_answer(make_agent, sql_seen, code):
    fake = FakeLLM(LlmError(code, "provider trouble"))
    result = make_agent(fake).ask("How many customers?")
    assert result["error"] == code.value
    assert result["answer"] == LLM_ERROR_REPLIES[code]
    assert (result["sql"], result["rows"]) == (None, [])
    assert "provider trouble" not in result["answer"]
    assert sql_seen == []


def test_llm_failure_during_repair_becomes_a_coded_answer(make_agent):
    fake = FakeLLM("SELECT revenue FROM customers", LlmError(LlmErrorCode.TIMEOUT, "slow"))
    result = make_agent(fake).ask("Show me revenue")
    assert result["error"] == "LLM_TIMEOUT"
    assert result["sql"] is None


def test_llm_failure_during_the_summary_keeps_the_rows(make_agent):
    fake = FakeLLM(
        "SELECT name FROM customers ORDER BY id LIMIT 2",
        LlmError(LlmErrorCode.RATE_LIMITED, "busy"),
    )
    result = make_agent(fake).ask("Two customers")
    assert result["answer"] == SUMMARY_UNAVAILABLE_REPLY
    assert result["error"] == "LLM_RATE_LIMITED"
    assert len(result["rows"]) == 2
    assert result["sql"] == "SELECT name FROM customers ORDER BY id LIMIT 2"


def test_build_llm_assembles_the_stack_without_a_network_call(test_settings, tmp_path):
    gateway = build_llm(test_settings)
    assert isinstance(gateway, LlmGateway)
    assert gateway.cache is None, "no cache unless LLM_CACHE_PATH is set"
    assert gateway.call_log is not None and gateway.call_log.log_content is False
    assert gateway.client.limiter is not None
    assert gateway.client.max_attempts == test_settings.llm_max_attempts

    cached = build_llm(test_settings.model_copy(update={"llm_cache_path": tmp_path / "c.sqlite"}))
    assert cached.cache is not None


# --- 10. guard_input and classify_intent (Phase 5) ----------------------


@pytest.mark.parametrize(
    ("question", "code"),
    [("   ", "INPUT_EMPTY"), ("???", "INPUT_NO_TEXT"), ("x" * 501, "INPUT_TOO_LONG")],
)
def test_guard_rejects_without_a_model_call(make_agent, sql_seen, question, code):
    fake = FakeLLM()  # nothing scripted: any model call fails the test
    result = make_agent(fake).ask(question)
    assert result["error"] == code
    assert result["answer"].strip()
    assert fake.call_count == 0
    assert sql_seen == []


def test_a_clarifying_question_ends_the_turn(make_agent, sql_seen):
    fake = FakeLLM("CLARIFY: Best by total spend or by number of orders?")
    result = make_agent(fake).ask("Who are the best customers?")
    assert result["needs_clarification"] is True
    assert result["answer"] == "Best by total spend or by number of orders?"
    assert (result["sql"], result["error"], result["out_of_scope"]) == (None, None, False)
    assert fake.call_count == 1
    assert sql_seen == []


def test_ordinary_answers_do_not_need_clarification(make_agent):
    result = make_agent(FakeLLM("SELECT name FROM customers LIMIT 1", "One.")).ask("One customer")
    assert result["needs_clarification"] is False


# --- 11. configurable repair attempts (Phase 5) ---------------------------


@pytest.mark.parametrize(("attempts", "calls"), [(0, 1), (2, 3)])
def test_repair_attempts_are_configurable_and_bounded(make_agent, attempts, calls):
    fake = FakeLLM(
        "SELECT revenue FROM customers",
        "SELECT nope FROM customers",
        "SELECT still_nope FROM customers",
    )
    result = make_agent(fake, max_repair_attempts=attempts).ask("Show me revenue")
    assert result["error"] == SqlErrorCode.EXECUTION_ERROR
    assert fake.call_count == calls, "generation plus exactly `attempts` repairs, no answer call"


def test_a_second_repair_can_succeed(make_agent):
    fake = FakeLLM(
        "SELECT revenue FROM customers",
        "SELECT nope FROM customers",
        "SELECT name FROM customers LIMIT 2",
        "Two customers.",
    )
    result = make_agent(fake, max_repair_attempts=2).ask("Show me revenue")
    assert result["error"] is None
    assert len(result["rows"]) == 2
    assert fake.roles.count(LlmRole.SQL_REPAIR) == 2


# --- 12. check_answer (Phase 5) -------------------------------------------


def test_check_answer_appends_a_missing_truncation_disclosure(make_agent):
    fake = FakeLLM("SELECT id FROM customers ORDER BY id", "Two customers shown.")
    result = make_agent(fake, max_rows=2).ask("List customer ids")
    assert result["answer"].startswith("Two customers shown. Only the first 2 rows")
    assert result["answer_checks"] == ["TRUNCATION_UNDISCLOSED"]


def test_check_answer_leaves_an_honest_answer_alone(make_agent):
    fake = FakeLLM("SELECT name FROM customers WHERE city = 'Goa'", "No customers live in Goa.")
    result = make_agent(fake).ask("Customers in Goa?")
    assert result["answer"] == "No customers live in Goa."
    assert result["answer_checks"] == []


def test_check_answer_flags_numbers_not_in_the_rows(make_agent):
    fake = FakeLLM("SELECT COUNT(*) FROM customers", "There are 999 customers.")
    result = make_agent(fake).ask("How many customers?")
    assert result["answer"] == "There are 999 customers."
    assert "UNSUPPORTED_NUMBERS" in result["answer_checks"]


def test_check_answer_skips_refusals(make_agent):
    assert make_agent(FakeLLM("OUT_OF_SCOPE")).ask("Write Python")["answer_checks"] == []
