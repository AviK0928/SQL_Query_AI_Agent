"""Test doubles, errors and shared test configuration for the offline suite.

One FakeLLM for every test file. It mimics the only part of the LLM gateway
the agent uses: `.complete(role, messages, **kwargs)` returning an object with
`.content` and token counts.
"""

# The model and limits every offline test is configured with. Values mirror the
# shape of the Groq console table; they are never sent anywhere.
TEST_MODEL = "fake/test-model"
TEST_LLM_LIMITS = {TEST_MODEL: {"rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200000}}


class FakeResponse:
    """Mimics app.llm.client.LlmResult: the fields the agent reads."""

    def __init__(self, content, input_tokens=100, output_tokens=10, cache_hit=False):
        self.content = content
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_hit = cache_hit


class FakeLLM:
    """Stands in for LlmGateway: returns scripted responses in order and records
    every call. A scripted Exception is raised instead of returned.

    Raises if called more times than responses were scripted, so a test fails
    loudly when the graph makes an unexpected extra call.
    """

    def __init__(self, *responses):
        self.queued = list(responses)
        self.calls = []  # the messages of each call, in order
        self.roles = []  # the LlmRole of each call
        self.kwargs = []  # prompt_id, schema_hash, request_id, temperature

    def complete(self, role, messages, **kwargs):
        self.calls.append(messages)
        self.roles.append(role)
        self.kwargs.append(kwargs)
        if not self.queued:
            raise AssertionError(
                f"FakeLLM called {len(self.calls)} times but only "
                f"{len(self.calls) - 1} responses were scripted"
            )
        outcome = self.queued.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return FakeResponse(outcome)

    @property
    def call_count(self):
        return len(self.calls)


class NetworkBlockedError(RuntimeError):
    """Raised by the conftest socket guard when a test tries to reach the network.

    Defined here rather than in conftest.py so tests can import it without
    importing conftest as a module.
    """
