"""Read versioned prompt files and compute prompt ids.

Prompt text lives in files next to this module, one file per prompt version:
``<name>.v<N>.md`` for prose prompts and ``<name>.v<N>.toml`` for small keyed
fragments. A released version is never edited; a change is a new file with the
next version number (tests/unit/test_prompt_versions.py enforces this).

File format rules, chosen so that files survive the repo's pre-commit hooks
without changing the text the model sees:

* Every file ends with exactly one newline (the end-of-file-fixer hook). The
  loader removes that one newline, so the prompt text itself controls whether
  it ends in a newline.
* Placeholders use ``string.Template`` syntax (``$schema``). Prompts contain
  SQL and ``strftime('%Y', ...)``, so ``$`` is the one delimiter that never
  appears in the text, unlike ``{}`` or ``%``. Rendering fails if a placeholder
  is missing or an unused value is passed, so a renamed placeholder cannot
  silently leave ``$name`` in a prompt.
"""

from __future__ import annotations

import hashlib
import tomllib
from pathlib import Path
from string import Template

PROMPT_DIR = Path(__file__).parent


def _path(name: str, version: int, suffix: str) -> Path:
    if version < 1:
        raise ValueError(f"prompt versions start at 1, got {version}")
    return PROMPT_DIR / f"{name}.v{version}{suffix}"


def read_prompt(name: str, version: int) -> str:
    """The raw text of ``<name>.v<version>.md`` without its final newline."""
    path = _path(name, version, ".md")
    text = path.read_text(encoding="utf-8")
    if not text.endswith("\n"):
        raise ValueError(f"{path.name} must end with exactly one newline")
    return text[:-1]


def render_prompt(name: str, version: int, **values: str) -> str:
    """Fill a prompt's ``$placeholders``; every placeholder and no extra value."""
    template = Template(read_prompt(name, version))
    if not template.is_valid():
        raise ValueError(f"{name}.v{version}.md has a malformed placeholder")
    expected = set(template.get_identifiers())
    if set(values) != expected:
        raise ValueError(
            f"{name}.v{version}.md needs placeholders {sorted(expected)}, got {sorted(values)}"
        )
    return template.substitute(values)


def read_fragments(name: str, version: int) -> dict[str, str]:
    """String values from ``<name>.v<version>.toml`` (for short keyed texts)."""
    data = tomllib.loads(_path(name, version, ".toml").read_text(encoding="utf-8"))
    if not all(isinstance(v, str) for v in data.values()):
        raise ValueError(f"{name}.v{version}.toml may only hold strings")
    return {k: str(v) for k, v in data.items()}


def prompt_id(name: str, *texts: str) -> str:
    """name@<first 8 hex chars of the SHA-256 of the prompt text>.

    The id hashes the rendered text, not the file name, so it also changes when
    a shared part (the schema file, a token) changes under an unchanged version.
    """
    digest = hashlib.sha256("\n".join(texts).encode()).hexdigest()[:8]
    return f"{name}@{digest}"
