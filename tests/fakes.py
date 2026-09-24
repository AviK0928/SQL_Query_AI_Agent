"""Test doubles and errors shared by the offline suite.

One FakeLLM for every test file (previously duplicated in test_agent.py and
test_api.py). It mimics the only part of LangChain's chat model the agent
uses: `.invoke(messages)` returning an object with `.content`.
"""


class FakeResponse:
    """Mimics LangChain's AIMessage: only .content is used."""

    def __init__(self, content):
        self.content = content


class FakeLLM:
    """Returns scripted responses in order and records every call.

    Raises if called more times than responses were scripted, so a test fails
    loudly when the graph makes an unexpected extra call.
    """

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


class NetworkBlockedError(RuntimeError):
    """Raised by the conftest socket guard when a test tries to reach the network.

    Defined here rather than in conftest.py so tests can import it without
    importing conftest as a module.
    """
