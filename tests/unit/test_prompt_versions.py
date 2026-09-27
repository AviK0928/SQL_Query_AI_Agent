"""Released prompt files are immutable; a change is a new version file.

RELEASED holds the SHA-256 (first 16 hex chars) of every prompt file's bytes.
Editing a released file fails test_released_files_are_unchanged. Adding a new
version fails test_every_prompt_file_is_registered until its hash is added
here, which makes each new version an explicit, reviewed step.
"""

import hashlib
import re

import pytest

from app.prompts import ACTIVE_VERSIONS
from app.prompts.loader import PROMPT_DIR

RELEASED = {
    "schema.v1.md": "5d12418fc0fb125e",
    "sql_gen.v1.md": "202fee36aef990b9",
    "sql_gen.v2.md": "ed935453b4ba4933",
    "sql_gen.v3.md": "2416cc5af042d9ed",
    "sql_gen.v4.md": "5d75ac4e0addb06e",
    "sql_repair.v1.md": "d379da6f79a9da91",
    "sql_repair.v2.md": "44f9fc7a337d3470",
    "answer.v1.md": "d519351408d8139e",
    "answer.v2.md": "59f88dbf4532058a",
    "answer_notes.v1.toml": "004ae34058b803ad",
}

VERSIONED = re.compile(r"^(?P<name>[a-z_]+)\.v(?P<version>[1-9][0-9]*)\.(md|toml)$")


def _sha16(name):
    return hashlib.sha256((PROMPT_DIR / name).read_bytes()).hexdigest()[:16]


@pytest.mark.parametrize("name", sorted(RELEASED))
def test_released_files_are_unchanged(name):
    assert _sha16(name) == RELEASED[name], f"{name} was edited; copy it to the next version instead"


def test_every_prompt_file_is_registered():
    on_disk = {p.name for p in PROMPT_DIR.iterdir() if p.suffix in {".md", ".toml"}}
    assert on_disk == set(RELEASED)
    assert all(VERSIONED.match(n) for n in on_disk), "file names must be <name>.v<N>.<md|toml>"


def test_every_active_version_has_a_file():
    released = {(m["name"], int(m["version"])) for m in map(VERSIONED.match, RELEASED)}
    assert set(ACTIVE_VERSIONS.items()) <= released
