"""Guard: every JSON/YAML data file that ships must actually parse.

The bug this exists for
-----------------------
Two shipped files did not, and nothing in this repo could tell:

* ``navig/builtin/tools/app.tool.json`` was UTF-16LE-with-BOM plus one stray trailing
  byte, so it decoded as neither UTF-8 nor UTF-16 -- unreadable in any encoding.
* ``navig/builtin/tools/iperf3/schema.json`` carried shell escaping that leaked into the
  file: ``\\"windows\\": \\"https://...\\"`` where plain quotes belong. Four escaped
  quotes on one line, and the document was not JSON.

Both shipped in the wheel. `verify_install.py` asserts the builtin store *has* a
``tools`` directory; nothing has ever asserted its contents are readable.

Why nothing else sees it
------------------------
A data file is invisible to every other check here. ruff and mypy never parse it (not
code). No test imports it. `tsc` does not know it exists. The packaging guard checks that
it is PRESENT, which a corrupt file also is. The first one was found only by a NUL-byte
scan written for an unrelated reason, and the second only by running this check for the
first time -- one grep of the class, two hits.

Scope, and why it is the whole package
--------------------------------------
Measured when written: 222 files under ``navig/`` and 37 under ``plugins/`` -- **all 259
parse** once the two above were repaired. There was no reason to scope this to
``builtin/``: no data file here is a template with placeholder syntax, so there are no
exemptions to carve and none to grandfather.

Decoded as strict UTF-8, deliberately not ``utf-8-sig``. A consumer reads these with
``json.loads(path.read_text())``, which a BOM breaks -- tolerating one here would pass a
file that fails at runtime. Measured: zero shipped data files carry a BOM today, so
strictness costs nothing and catches the next one.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]

# Both roots ship: `navig/` is the wheel, `plugins/` are the first-party packages
# published beside it.
_ROOTS = ("core/navig", "plugins")

_SKIP_DIRS = {
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "target",
    ".dev",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
}

_SUFFIXES = {".json", ".yaml", ".yml"}

# Measured at 259. The floor sits well below that so ordinary churn cannot trip it, and
# far above the zero a mis-rooted scan would produce.
_MIN_DATA_FILES = 150


@lru_cache(maxsize=4)
def _scan(repo: Path = REPO) -> tuple[tuple[tuple[str, str], ...], int]:
    """((path, error), ...), data files seen. One pass, cached.

    ``repo`` is a parameter only so the rooting itself is testable — the floor
    below cannot catch a mis-rooted scan from a checkout that happens to sit in a
    normally-named directory.
    """
    failures: list[tuple[str, str]] = []
    seen = 0
    for root in _ROOTS:
        base = repo / root
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(repo)
            # Match _SKIP_DIRS against the path RELATIVE to the repo, never the
            # absolute one. Every worktree this repo's own workflow creates lives
            # at `<repo>/.dev/worktrees/<slug>`, so `.dev` is a part of every
            # absolute path inside it — the whole scan was skipped, `seen` stayed
            # 0, and the anti-vacuity floor failed the push. Measured in a
            # worktree: 0 files by absolute parts, 253 by relative.
            # The same held for a checkout under any dir named build/dist/venv/target.
            if _SKIP_DIRS & set(relative.parts):
                continue
            suffix = path.suffix.lower()
            if suffix not in _SUFFIXES:
                continue
            seen += 1
            rel = relative.as_posix()
            try:
                text = path.read_bytes().decode("utf-8")
            except UnicodeDecodeError as exc:
                failures.append((rel, f"not UTF-8: {exc}"))
                continue
            try:
                if suffix == ".json":
                    json.loads(text)
                else:
                    yaml.safe_load(text)
            except (json.JSONDecodeError, yaml.YAMLError) as exc:
                failures.append((rel, f"{type(exc).__name__}: {str(exc).splitlines()[0]}"))
    return tuple(failures), seen


def test_the_scan_actually_reads_the_tree() -> None:
    """Stated as a presence: a scan that found nothing reports no failures."""
    _, seen = _scan()
    assert (REPO / "core" / "navig").is_dir() and (REPO / "plugins").is_dir(), (
        f"{REPO} does not look like the repo root; every count below it is meaningless"
    )
    assert seen >= _MIN_DATA_FILES, (
        f"only {seen} data files found (expected >= {_MIN_DATA_FILES}) -- this scan is "
        f"mis-rooted at {REPO} and read almost nothing."
    )


def test_every_shipped_data_file_parses() -> None:
    failures, _ = _scan()
    assert not failures, (
        "these shipped data files do not parse:\n"
        + "\n".join(f"    {path}\n        {err}" for path, err in failures)
        + "\n\nThey ship in the wheel. Nothing else here can see the problem: a linter "
        "never parses a data file, no test imports it, and the packaging guard only "
        "checks that it is PRESENT -- which a corrupt file also is."
    )


def test_the_scan_is_not_defeated_by_a_skip_named_ancestor(tmp_path) -> None:
    """A directory in _SKIP_DIRS ABOVE the repo root must not skip the whole tree.

    This repo's own parallel-session workflow puts every worktree at
    `<repo>/.dev/worktrees/<slug>`, so `.dev` is a component of every absolute
    path inside one. Matching _SKIP_DIRS against absolute parts therefore skipped
    everything: measured 0 files scanned in a worktree versus 253 relative, which
    tripped the anti-vacuity floor and blocked the push of an unrelated change.

    The floor above cannot catch this — it only fires where the mis-rooting
    actually happens, and the main checkout is not under a skip-named directory.
    """
    repo = tmp_path / ".dev" / "worktrees" / "wt"
    (repo / "core" / "navig").mkdir(parents=True)
    (repo / "plugins").mkdir()
    (repo / "core" / "navig" / "ok.json").write_text('{"a": 1}', encoding="utf-8")

    # A skip-named dir INSIDE the repo must still be skipped.
    cache = repo / "core" / "navig" / "__pycache__"
    cache.mkdir()
    (cache / "junk.json").write_text("definitely not json", encoding="utf-8")

    failures, seen = _scan(repo)

    assert seen == 1, f"expected the one real data file, scanned {seen}"
    assert not failures, f"the __pycache__ file was not skipped: {failures}"
