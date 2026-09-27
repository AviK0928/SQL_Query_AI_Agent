"""Live tests: real Groq calls, run only with `pytest -m live`.

The default run deselects them (addopts `-m 'not live'` in pyproject.toml), so
`pytest` and CI stay fully offline (T1). They run from Colab, or from the
"Run evals" workflow with its `live_tests` box ticked.

This file overrides the suite-wide `offline_guard` from tests/conftest.py for
this folder only, because live tests need the network and the real
configuration. Two rules replace it:

1. Every test here must carry the `live` marker. An unmarked test would run in
   the default offline run with the guard switched off, so it fails instead.
2. Missing configuration fails loudly; it never skips. A skipped live test
   shows green while checking nothing.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from app.config import ConfigError, Settings, load_settings


@pytest.fixture(autouse=True)
def offline_guard(request: pytest.FixtureRequest) -> Iterator[list[str]]:
    """Replaces the offline guard for this folder; see the module docstring."""
    if request.node.get_closest_marker("live") is None:
        pytest.fail(
            f"{request.node.nodeid}: tests under tests/live must set "
            "`pytestmark = pytest.mark.live`",
            pytrace=False,
        )
    yield []


@pytest.fixture(scope="session")
def live_settings() -> Settings:
    """Real settings from the environment (and .env). ConfigError names the
    missing variables and never echoes a value."""
    try:
        return load_settings()
    except ConfigError as exc:
        pytest.fail(f"live tests need real configuration: {exc}", pytrace=False)
