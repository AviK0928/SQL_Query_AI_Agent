"""Runner CLI: --date lets a run stopped by a daily limit resume into its own folder."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import evals.runner as runner

ARGS = ["--role", "sql_generator", "--model", "m", "--tag", "t"]


def test_date_reaches_the_run_so_it_resumes_its_original_folder(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(**kwargs: Any) -> tuple[Path, int]:
        seen.update(kwargs)
        return tmp_path, 0

    monkeypatch.setattr(runner, "run", fake_run)
    assert runner.main([*ARGS, "--date", "2026-09-27"]) == 0
    assert seen["today"] == "2026-09-27"


def test_a_malformed_date_is_rejected() -> None:
    with pytest.raises(SystemExit):
        runner.main([*ARGS, "--date", "27-09-2026"])


def test_without_a_date_a_run_uses_today_and_the_roles_suites(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: dict[str, Any] = {}

    def fake_run(**kwargs: Any) -> tuple[Path, int]:
        seen.update(kwargs)
        return tmp_path, 0

    monkeypatch.setattr(runner, "run", fake_run)
    assert runner.main(ARGS) == 0
    assert seen["today"] is None
    assert seen["suites"] == ["golden", "adversarial"]
    assert seen["prompt_versions"] == {}


def test_a_malformed_prompt_override_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_run(**kwargs: Any) -> tuple[Path, int]:
        raise AssertionError("the run must not start")

    monkeypatch.setattr(runner, "run", fake_run)
    with pytest.raises(SystemExit):
        runner.main([*ARGS, "--prompt", "answer"])
