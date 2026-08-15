"""Tests for the LangGraph agent.

No real API calls. A fake LLM is injected via agent.set_llm(), so the suite
stays free, offline and deterministic. Everything below the model is real:
the validator runs, the graph routes, and queries hit the actual SQLite file.
"""

import pytest

import app.agent as agent


# --- fake LLM -----------------------------------------------------------

class FakeResponse:
    """Mimics LangChain's response object: only .content is used."""
    def __init__(self, content):
        self.content = content


class FakeLLM:
    """Returns scripted responses in order. One ask() can trigger 1-3 calls."""

    def __init__(self, *responses):
        self.queued = list(responses)
        self.calls = []

    def invoke(self, messages):
        self.calls.append(messages)
        if not self.queued:
            raise AssertionError(
                f"FakeLLM called {len(self.calls)} times but only "
                f"{len(self.calls) - 1} responses were scripted"
            )
        return FakeResponse(self.queued.pop(0))

    @property
    def call_count(self):
        return len(self.calls)


@pytest.fixture(autouse=True)
def reset_module_globals():
    """agent._llm and agent._graph are module-level and would leak between tests."""
    agent._llm = None
    agent._graph = None
    yield
    agent._llm = None
    agent._graph = None


@pytest.fixture
def sql_seen(monkeypatch):
    """Records every SQL string that reaches the database, then calls through."""
    seen = []
    real = agent.run_query

    def spy(sql):
        seen.append(sql)
        return real(sql)

    monkeypatch.setattr(agent, "run_query", spy)
    return seen


# --- 1. happy path ------------------------------------------------------

def test_happy_path_generates_sql_and_answers():
    fake = FakeLLM(
        "SELECT name, city FROM customers LIMIT 5",
        "There are five customers listed.",
    )
    agent.set_llm(fake)

    result = agent.ask("Show all customers")

    assert result["sql"].upper().startswith("SELECT")
    assert result["error"] is None
    assert result["out_of_scope"] is False
    assert result["rows"], "expected real rows from ecommerce.db"
    assert result["columns"] == ["name", "city"]
    assert result["answer"].strip()
    assert fake.call_count == 2, "generate + format_answer, no retry"


# --- 2. out of scope ----------------------------------------------------

def test_out_of_scope_skips_the_database(sql_seen):
    fake = FakeLLM("OUT_OF_SCOPE")
    agent.set_llm(fake)

    result = agent.ask("Write me some Python code")

    assert result["out_of_scope"] is True
    assert result["sql"] is None
    assert result["rows"] == []
    assert "e-commerce database" in result["answer"]
    assert sql_seen == [], "database must not be touched for out-of-scope questions"
    assert fake.call_count == 1, "no answer-formatting call needed"


def test_read_only_request_gets_a_read_only_message(sql_seen):
    """Write requests are on-topic but forbidden, so the reply must say so."""
    fake = FakeLLM("READ_ONLY")
    agent.set_llm(fake)

    result = agent.ask("delete the most expensive order")

    assert result["out_of_scope"] is True
    assert result["sql"] is None
    assert "only read" in result["answer"]
    assert "e-commerce database" not in result["answer"], "wrong message: that is the off-topic one"
    assert sql_seen == []
    assert fake.call_count == 1


# --- 3. retry succeeds --------------------------------------------------

def test_retry_after_bad_column_succeeds():
    fake = FakeLLM(
        "SELECT revenue FROM customers",          # valid shape, column does not exist
        "SELECT name FROM customers LIMIT 3",     # corrected
        "Three customers were found.",
    )
    agent.set_llm(fake)

    result = agent.ask("Show me revenue")

    assert result["error"] is None
    assert result["sql"] == "SELECT name FROM customers LIMIT 3"
    assert len(result["rows"]) == 3
    assert fake.call_count == 3, "generate + retry + format_answer"


def test_retry_receives_the_database_error():
    fake = FakeLLM(
        "SELECT revenue FROM customers",
        "SELECT name FROM customers LIMIT 1",
        "One customer.",
    )
    agent.set_llm(fake)
    agent.ask("Show me revenue")

    retry_messages = fake.calls[1]
    assert any("no such column: revenue" in m["content"] for m in retry_messages)


# --- 4. retry exhausted -------------------------------------------------

def test_retry_exhausted_returns_error_without_a_third_call():
    fake = FakeLLM(
        "SELECT revenue FROM customers",
        "SELECT still_wrong FROM customers",
    )
    agent.set_llm(fake)

    result = agent.ask("Show me revenue")

    assert result["error"] is not None
    assert "couldn't run a query" in result["answer"]
    assert fake.call_count == 2, "one retry only; no LLM call to format an error"


# --- 5. dangerous SQL ---------------------------------------------------

def test_dangerous_sql_never_reaches_the_database(sql_seen):
    fake = FakeLLM(
        "DROP TABLE customers",
        "SELECT name FROM customers LIMIT 2",
        "Two customers.",
    )
    agent.set_llm(fake)

    result = agent.ask("Delete all customers")

    assert all("DROP" not in sql.upper() for sql in sql_seen)
    assert result["sql"] == "SELECT name FROM customers LIMIT 2"
    assert result["error"] is None


def test_stacked_statements_are_rejected_by_the_validator(sql_seen):
    fake = FakeLLM(
        "SELECT 1; DROP TABLE customers",
        "SELECT name FROM customers LIMIT 1",
        "One customer.",
    )
    agent.set_llm(fake)
    agent.ask("Show a customer")

    assert all(";" not in sql for sql in sql_seen)


# --- 6. markdown fences -------------------------------------------------

@pytest.mark.parametrize("raw", [
    "```sql\nSELECT name FROM customers LIMIT 2\n```",
    "```\nSELECT name FROM customers LIMIT 2\n```",
    "  SELECT name FROM customers LIMIT 2  ",
])
def test_markdown_fences_and_whitespace_are_stripped(raw):
    fake = FakeLLM(raw, "Two customers.")
    agent.set_llm(fake)

    result = agent.ask("Show two customers")

    assert result["sql"] == "SELECT name FROM customers LIMIT 2"
    assert result["error"] is None


# --- 7. conversation history --------------------------------------------

def test_history_is_replayed_to_the_model():
    fake = FakeLLM(
        "SELECT name FROM customers WHERE city = 'Pune' LIMIT 5",
        "Two customers in Pune.",
    )
    agent.set_llm(fake)

    history = [{"question": "customers in Mumbai",
                "sql": "SELECT name FROM customers WHERE city = 'Mumbai'"}]
    agent.ask("and Pune?", history)

    roles = [m["role"] for m in fake.calls[0]]
    assert roles == ["system", "user", "assistant", "user"]
    assert fake.calls[0][1]["content"] == "customers in Mumbai"
    assert fake.calls[0][-1]["content"] == "and Pune?"


def test_history_is_truncated_to_max_turns():
    fake = FakeLLM("SELECT name FROM customers LIMIT 1", "One customer.")
    agent.set_llm(fake)

    history = [{"question": f"q{i}", "sql": f"SELECT {i}"} for i in range(10)]
    agent.ask("latest question", history)

    replayed = [m["content"] for m in fake.calls[0] if m["role"] == "user"]
    assert "q0" not in replayed, "old turns must be dropped"
    assert "q9" in replayed, "most recent turn must be kept"
    assert len(replayed) == agent.MAX_HISTORY_TURNS + 1
