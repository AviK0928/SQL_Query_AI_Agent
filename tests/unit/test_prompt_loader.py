"""app/prompts/loader.py: file format rules and placeholder checks.

Each test points the loader at a temporary directory, so the rules are tested
independently of the real prompt files.
"""

import pytest

from app.prompts import loader


@pytest.fixture
def prompt_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "PROMPT_DIR", tmp_path)
    return tmp_path


def test_read_removes_exactly_one_final_newline(prompt_dir):
    (prompt_dir / "p.v1.md").write_text("line one\nline two\n\n")
    assert loader.read_prompt("p", 1) == "line one\nline two\n"


def test_read_rejects_a_file_without_a_final_newline(prompt_dir):
    (prompt_dir / "p.v1.md").write_text("no newline")
    with pytest.raises(ValueError, match="exactly one newline"):
        loader.read_prompt("p", 1)


def test_read_selects_the_requested_version(prompt_dir):
    (prompt_dir / "p.v1.md").write_text("one\n")
    (prompt_dir / "p.v2.md").write_text("two\n")
    assert loader.read_prompt("p", 2) == "two"


@pytest.mark.parametrize("version", [0, -1])
def test_versions_start_at_one(prompt_dir, version):
    with pytest.raises(ValueError, match="start at 1"):
        loader.read_prompt("p", version)


def test_missing_version_raises(prompt_dir):
    with pytest.raises(FileNotFoundError):
        loader.read_prompt("p", 9)


def test_render_fills_placeholders_and_leaves_other_symbols(prompt_dir):
    (prompt_dir / "p.v1.md").write_text("Use strftime('%Y', d) {x} on $table.\n")
    assert loader.render_prompt("p", 1, table="orders") == "Use strftime('%Y', d) {x} on orders."


def test_render_rejects_a_missing_value(prompt_dir):
    (prompt_dir / "p.v1.md").write_text("$a and $b\n")
    with pytest.raises(ValueError, match="needs placeholders"):
        loader.render_prompt("p", 1, a="x")


def test_render_rejects_an_unused_value(prompt_dir):
    (prompt_dir / "p.v1.md").write_text("$a\n")
    with pytest.raises(ValueError, match="needs placeholders"):
        loader.render_prompt("p", 1, a="x", b="y")


def test_render_rejects_a_malformed_placeholder(prompt_dir):
    (prompt_dir / "p.v1.md").write_text("cost $5\n")
    with pytest.raises(ValueError, match="malformed"):
        loader.render_prompt("p", 1)


def test_fragments_return_strings(prompt_dir):
    (prompt_dir / "n.v1.toml").write_text('a = "one"\nb = "two {shown}"\n')
    assert loader.read_fragments("n", 1) == {"a": "one", "b": "two {shown}"}


def test_fragments_reject_non_string_values(prompt_dir):
    (prompt_dir / "n.v1.toml").write_text("a = 1\n")
    with pytest.raises(ValueError, match="only hold strings"):
        loader.read_fragments("n", 1)
