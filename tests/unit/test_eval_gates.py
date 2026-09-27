"""The availability and context-window gates are computed, and unverified means fail."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from evals.runner import catalog_entry, context_fits, main


def snapshot(reports: Path, date: str, models: list[dict[str, object]]) -> None:
    folder = reports / f"{date}-catalog"
    folder.mkdir(parents=True)
    (folder / "models.json").write_text(json.dumps({"data": models}))


def calls(run_dir: Path, *usage: tuple[str, int, int]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    lines = [
        json.dumps(
            {
                "gen_ai.request.model": m,
                "gen_ai.usage.input_tokens": i,
                "gen_ai.usage.output_tokens": o,
            }
        )
        for m, i, o in usage
    ]
    (run_dir / "calls.jsonl").write_text("\n".join(lines) + "\n")


def test_catalog_entry_uses_the_newest_snapshot_on_or_before_the_run_date(tmp_path: Path) -> None:
    for date, window in (("2026-09-20", 100), ("2026-09-26", 200), ("2026-09-30", 300)):
        snapshot(tmp_path, date, [{"id": "m", "context_window": window, "active": True}])
    entry = catalog_entry(tmp_path, "m", "2026-09-27")
    assert entry is not None and entry["context_window"] == 200
    assert catalog_entry(tmp_path, "m", "2026-09-19") is None  # no snapshot yet
    assert catalog_entry(tmp_path, "other", "2026-09-27") is None  # not listed


def test_context_fits_checks_the_largest_call_of_that_model_only(tmp_path: Path) -> None:
    run = tmp_path / "run"
    calls(run, ("m", 900, 100), ("m", 50, 10), ("big", 5000, 5000))
    assert context_fits(run, "m", 1000) is True
    assert context_fits(run, "m", 999) is False


def test_context_gate_is_not_a_pass_when_unverified(tmp_path: Path) -> None:
    run = tmp_path / "run"
    calls(run, ("other", 10, 10))
    assert context_fits(run, "m", 1000) is False  # no logged call by this model
    assert context_fits(run, "other", None) is False  # window unknown
    assert context_fits(tmp_path / "missing", "m", 1000) is False  # no call log


REPORTS = Path(__file__).resolve().parents[2] / "evals" / "reports"
RUN = "2026-09-26-synth-qwen3.8-27b"


def test_report_only_needs_no_run_arguments_and_computes_the_gates(tmp_path: Path) -> None:
    shutil.copytree(REPORTS / RUN, tmp_path / RUN)
    shutil.copytree(REPORTS / "2026-09-26-catalog", tmp_path / "2026-09-26-catalog")
    assert main(["--report-only", str(tmp_path / RUN)]) == 0
    text = (tmp_path / RUN / "report.md").read_text()
    assert "| available | ✅ |" in text and "| context_window | ✅ |" in text


def test_a_report_without_a_catalog_snapshot_fails_the_gates(tmp_path: Path) -> None:
    shutil.copytree(REPORTS / RUN, tmp_path / RUN)  # no snapshot: nothing can vouch for it
    assert main(["--report-only", str(tmp_path / RUN)]) == 0
    text = (tmp_path / RUN / "report.md").read_text()
    assert "| available | ✅ |" not in text and "eligible: **False**" in text


def test_a_real_run_still_requires_role_model_and_tag() -> None:
    with pytest.raises(SystemExit):
        main([])
