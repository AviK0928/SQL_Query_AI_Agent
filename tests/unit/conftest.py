"""Hypothesis profiles for the property tests.

ci (default): derandomized, so a failure in CI reproduces identically on any
machine. No per-example deadline: sqlglot parse time varies with input and a
timing-based failure would be flaky, not a finding.
dev: many more examples, for a manual deep run:
    HYPOTHESIS_PROFILE=dev pytest tests/unit/test_sql_properties.py
"""

import os

from hypothesis import settings

settings.register_profile("ci", derandomize=True, deadline=None, max_examples=200)
settings.register_profile("dev", deadline=None, max_examples=5000)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))
