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
