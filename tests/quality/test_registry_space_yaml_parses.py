"""Every YAML file a registry space ships must parse.

`navig space install` clones these spaces onto users' machines, where their agents,
skills and workflows are read by YAML loaders. 30 of 583 did not parse (an unquoted
`@bridge` list item, `(default: 50)` inside a plain scalar, Python f-strings in a
`script: |` block dropping to column 0) — invisible because no test read them.
Nothing in the tree loads them from the registry itself, so this is the only check.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
SPACES = REPO / "registry" / "spaces"

# Measured when added: 583 tracked files. A floor, so a moved root cannot pass
# having read nothing.
_SCAN_FLOOR = 400


def _space_yaml_files() -> list[Path]:
    return sorted(
        p
        for p in SPACES.rglob("*")
        if p.suffix in {".yaml", ".yml"}
        and p.is_file()
        and "node_modules" not in p.parts
    )


def test_scan_reaches_the_registry():
    assert (REPO / "core" / "navig").is_dir() and SPACES.is_dir()
    assert len(_space_yaml_files()) >= _SCAN_FLOOR


def test_every_registry_space_yaml_parses():
    bad = []
    for p in _space_yaml_files():
        try:
            list(yaml.safe_load_all(p.read_text(encoding="utf-8")))
        except yaml.YAMLError as e:
            mark = getattr(e, "problem_mark", None)
            where = f":{mark.line + 1}" if mark else ""
            bad.append(f"{p.relative_to(REPO).as_posix()}{where} — {getattr(e, 'problem', e)}")
    if bad:
        pytest.fail(
            "registry space YAML that does not parse (quote plain scalars starting with "
            "@ or ` or containing ': '; keep every line of a `|` block at or beyond its "
            "indent):\n  " + "\n  ".join(bad)
        )
