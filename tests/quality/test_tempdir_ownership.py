"""`mkdtemp()` hands you a DIRECTORY. Removing only the files in it is a leak.

Measured on the operator's machine, three separate subsystems, all the same shape:

* **TikTok actions** — 25 empty `navig_tiktok_*` dirs, one per action ever run.
* **Deck deploy** — 91 `navig-deck-deploy-*` dirs; the bake copies the whole
  bundle in and hands the path back, and nothing removed it.
* **navig-dedupe** — the video signature path unlinked its frames and left the
  directory, once *per video*, so a library scan leaves one per file.

Nothing errors, nothing is slow, nobody notices; the temp dir just fills up. Two
of the three shipped after the first was found and fixed, which is why this is a
guard and not three patches.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

_REPO = pathlib.Path(__file__).resolve().parents[3]

#: Files allowed to call `mkdtemp()` without cleaning up, each with a reason.
#: The rule is ownership: a function that DELIBERATELY hands the directory to its
#: caller is not leaking — the caller owns it. Anything else must clean up.
_ALLOWED = {
    "plugins/navig-download/navig_download/tiktok/engine.py": (
        "fetch_file() returns a path INTO the directory and documents that the "
        "caller owns it; every in-tree caller now passes an explicit dest_dir "
        "(navig.telegram.tiktok_actions._fetched, navig_pipeline._do_acquire)."
    ),
}


def _roots() -> list[pathlib.Path]:
    roots = [_REPO / "core" / "navig"]
    roots += sorted((_REPO / "plugins").glob("navig-*/navig_*"))
    return [r for r in roots if r.is_dir()]


def _python_files() -> list[pathlib.Path]:
    out: list[pathlib.Path] = []
    for root in _roots():
        out += [f for f in root.rglob("*.py") if "build" not in f.parts]
    return out


def _rel(path: pathlib.Path) -> str:
    return path.relative_to(_REPO).as_posix()


@pytest.fixture(scope="module")
def offenders() -> dict[str, list[int]]:
    """Files that call `mkdtemp()` and never remove a directory."""
    found: dict[str, list[int]] = {}
    scanned = 0
    for f in _python_files():
        src = f.read_text(encoding="utf-8", errors="replace")
        scanned += 1
        if "mkdtemp" not in src:
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        # Any of these means the file takes directory cleanup seriously somewhere.
        cleans = "rmtree" in src or "TemporaryDirectory" in src
        lines = [
            n.lineno
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and getattr(n.func, "attr", getattr(n.func, "id", None)) == "mkdtemp"
        ]
        if lines and not cleans:
            found[_rel(f)] = lines
    assert scanned > 500, (
        f"only scanned {scanned} files — the roots are wrong, so a pass here "
        "would mean nothing"
    )
    return found


def test_mkdtemp_callers_remove_the_directory(offenders):
    unexplained = {k: v for k, v in offenders.items() if k not in _ALLOWED}
    assert not unexplained, (
        "mkdtemp() without rmtree()/TemporaryDirectory() — removing the FILES "
        f"and leaving the directory is the leak this guards: {unexplained}"
    )


def test_the_allowlist_does_not_outlive_its_reason(offenders):
    """An entry that no longer offends is a stale claim — drop it."""
    stale = sorted(set(_ALLOWED) - set(offenders))
    assert not stale, (
        f"{stale} no longer calls mkdtemp() without cleanup — remove the "
        "_ALLOWED entry so the list keeps meaning what it says"
    )


def test_every_allowlist_entry_carries_a_reason():
    for name, reason in _ALLOWED.items():
        assert len(reason) > 40, f"{name}: an exemption needs a written rationale"
