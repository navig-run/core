"""The generated manifest merges BY COMMAND — and the result is exactly what a regen would write.

core/generated/{commands.json, commands.md, completions/commands.txt, deprecations.json} are
regenerated on nearly every branch and three of them stamp the clock, so two concurrent
branches collided on every rebase even when they touched different commands (a full pre-push
gate per collision). `scripts/merge_generated_manifest.py` merges them as records keyed by
command path. The property that makes that safe is checked here against navig's OWN
renderers: merging two sides' artifacts must be byte-identical to rendering the merged
command list — otherwise the driver would trade a conflict for silent drift.
"""

from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DRIVER = REPO / "scripts" / "merge_generated_manifest.py"
GENERATED = ("commands.json", "commands.md", "completions/commands.txt", "deprecations.json")


@pytest.fixture(scope="module")
def driver():
    spec = importlib.util.spec_from_file_location("_manifest_merge_driver", DRIVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _export_registry():
    """``core/tools/export_registry.py`` loaded BY PATH.

    ``import tools.export_registry`` resolves ``tools`` by name, and ``core/tests/tools/``
    is a package too. When a test from that folder ran earlier on the same xdist worker,
    ``tools`` already meant the TEST package and this file failed with ModuleNotFoundError.
    The failure depended only on test order, so it passed alone and in most selections.
    """
    spec = importlib.util.spec_from_file_location(
        "_export_registry_for_merge_test", REPO / "core" / "tools" / "export_registry.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _render(manifest: dict) -> dict[str, str]:
    """Every artifact exactly as tools/export_registry.py writes it."""
    from navig.registry.manifest import deprecations_report, render_markdown

    _completions_text = _export_registry()._completions_text

    return {
        "commands.json": json.dumps(manifest, indent=2, sort_keys=True),
        "commands.md": render_markdown(manifest),
        "completions/commands.txt": _completions_text(manifest),
        "deprecations.json": json.dumps(deprecations_report(manifest), indent=2, sort_keys=True),
    }


def _entry(path: str, summary: str = "A command.") -> dict:
    return {
        "path": path,
        "summary": summary,
        "module": "navig.commands.zz",
        "handler": "h",
        "status": "stable",
        "since": "",
        "aliases": [],
        "tags": [],
        "examples": [path],
        "arguments": [],
        "options": [{"flags": ["--json"], "takes_value": False}],
    }


def _manifest(commands: list[dict], stamp: str) -> dict:
    commands = sorted(commands, key=lambda c: c["path"])
    return {
        "schema_version": "1.0.0",
        "generated_at": stamp,
        "total": len(commands),
        "commands": commands,
        "groups": {"zz": "The zz group."},
    }


@pytest.fixture
def sides():
    """base + two branches that each change DIFFERENT commands, as a real rebase sees them."""
    base_cmds = [_entry("navig zz alpha"), _entry("navig zz beta"), _entry("navig zz gamma")]
    base = _manifest(base_cmds, "2026-09-30T01:00:00+00:00")

    ours_cmds = copy.deepcopy(base_cmds) + [_entry("navig zz added-by-ours")]
    ours_cmds[0]["summary"] = "Alpha, reworded by ours."
    ours = _manifest(ours_cmds, "2026-09-30T02:00:00+00:00")

    theirs_cmds = [c for c in copy.deepcopy(base_cmds) if c["path"] != "navig zz gamma"]
    theirs_cmds.append(_entry("navig zz added-by-theirs"))
    theirs_cmds[1]["status"] = "deprecated"
    theirs_cmds[1]["deprecated"] = {
        "since": "3.30.0",
        "remove_after": "3.40.0",
        "replaced_by": "navig zz alpha",
        "note": "use alpha",
    }
    theirs = _manifest(theirs_cmds, "2026-09-30T03:00:00+00:00")

    want_cmds = copy.deepcopy(theirs_cmds) + [copy.deepcopy(ours_cmds[-1])]
    want_cmds = [c for c in want_cmds if c["path"] != "navig zz alpha"] + [
        copy.deepcopy(ours_cmds[0])
    ]
    want = _manifest(want_cmds, "2026-09-30T03:00:00+00:00")  # the later stamp
    return base, ours, theirs, want


def _run(driver, tmp_path: Path, name: str, base: str, ours: str, theirs: str) -> tuple[int, str]:
    o, a, b = tmp_path / "O", tmp_path / "A", tmp_path / "B"
    o.write_text(base, encoding="utf-8")
    a.write_text(ours, encoding="utf-8")
    b.write_text(theirs, encoding="utf-8")
    rc = driver.main(["driver", str(o), str(a), str(b), f"core/generated/{name}"])
    return rc, a.read_text(encoding="utf-8")


def test_the_four_files_are_wired_to_the_driver():
    if shutil.which("git") is None:
        pytest.skip("git is not on PATH")
    out = subprocess.run(
        [
            "git",
            "-C",
            str(REPO),
            "check-attr",
            "merge",
            *[f"core/generated/{n}" for n in GENERATED],
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert out.count(": merge: navig-manifest") == 4, out


@pytest.mark.parametrize("name", GENERATED)
def test_merging_concurrent_changes_equals_rendering_the_merged_commands(
    driver, sides, tmp_path, name
):
    base, ours, theirs, want = sides
    rb, ro, rt, rw = _render(base), _render(ours), _render(theirs), _render(want)
    rc, merged = _run(driver, tmp_path, name, rb[name], ro[name], rt[name])
    assert rc == 0
    assert merged == rw[name]  # byte-identical to a regen — no silent drift


@pytest.mark.parametrize("name", GENERATED)
def test_a_file_merged_with_itself_is_unchanged(driver, sides, tmp_path, name):
    text = _render(sides[3])[name]
    assert _run(driver, tmp_path, name, text, text, text) == (0, text)


def test_the_committed_files_round_trip(driver, tmp_path):
    """The live files, not just fixtures: nothing in their real shape is lost."""
    for name in GENERATED:
        text = (REPO / "core" / "generated" / name).read_text(encoding="utf-8")
        assert _run(driver, tmp_path, name, text, text, text) == (0, text), name


def test_the_same_command_changed_differently_is_a_real_conflict(driver, sides, tmp_path):
    base, ours, _, _ = sides
    theirs = copy.deepcopy(base)
    theirs["commands"][0]["summary"] = "Alpha, reworded by theirs."
    rc, merged = _run(
        driver,
        tmp_path,
        "commands.json",
        _render(base)["commands.json"],
        _render(ours)["commands.json"],
        _render(theirs)["commands.json"],
    )
    assert rc == 1  # git leaves the file conflicted…
    assert "<<<<<<< ours" in merged and ">>>>>>> theirs" in merged  # …with its usual markers


def test_a_rebase_resolves_by_itself(driver, sides, tmp_path):
    """End to end through git: two branches regenerate the manifest; the rebase is clean."""
    if shutil.which("git") is None:
        pytest.skip("git is not on PATH")
    base, ours, theirs, want = sides
    repo = tmp_path / "repo"
    gen = repo / "core" / "generated"
    (gen / "completions").mkdir(parents=True)

    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)

    def write(manifest: dict) -> None:
        for name, text in _render(manifest).items():
            (gen / name).write_text(text, encoding="utf-8", newline="\n")

    git("init", "-q", "-b", "main")
    for key, value in (("user.email", "t@t"), ("user.name", "t"), ("core.autocrlf", "false")):
        git("config", key, value)
    git("config", "merge.navig-manifest.driver", f'"{sys.executable}" "{DRIVER}" %O %A %B %P')
    (repo / ".gitattributes").write_text(
        "".join(f"core/generated/{n} merge=navig-manifest\n" for n in GENERATED), encoding="utf-8"
    )
    write(base)
    git("add", "-A")
    git("commit", "-q", "-m", "base")
    git("checkout", "-q", "-b", "feature")
    write(theirs)
    git("commit", "-qam", "feature regenerates")
    git("checkout", "-q", "main")
    write(ours)
    git("commit", "-qam", "main regenerates")
    git("checkout", "-q", "feature")

    rebase = git("rebase", "main")
    assert rebase.returncode == 0, rebase.stdout + rebase.stderr
    for name, text in _render(want).items():
        assert (gen / name).read_text(encoding="utf-8") == text, name


def test_orphaned_merge_temps_are_swept_and_live_ones_kept(driver, tmp_path):
    """git deletes the `.merge_file_*` copies it hands a driver when the merge ends; an
    interrupted git does not, and they piled up in the main checkout (301 in a day)."""
    import os
    import time

    now = time.time()
    stale = tmp_path / ".merge_file_ab12CD"
    live = tmp_path / ".merge_file_Zz9yX8"  # a merge running right now
    lookalike = tmp_path / ".merge_file_notatemp.txt"  # not git's name shape
    for p in (stale, live, lookalike):
        p.write_text("{}", encoding="utf-8")
    os.utime(stale, (now - 3600, now - 3600))
    os.utime(lookalike, (now - 3600, now - 3600))

    assert driver._sweep_stale_temps(tmp_path, now=now) == 1
    assert not stale.exists()
    assert live.exists(), "a temp file a live merge may still be using must survive"
    assert lookalike.exists(), "only git's exact temp-name shape is swept"


def test_leftover_merge_temps_are_gitignored():
    if shutil.which("git") is None:
        pytest.skip("git is not on PATH")
    res = subprocess.run(
        ["git", "-C", str(REPO), "check-ignore", "-q", ".merge_file_ab12CD"],
        capture_output=True,
    )
    assert res.returncode == 0, ".merge_file_* must be gitignored so a leftover is never committed"


def test_every_driver_run_sweeps_its_worktree_top(driver, sides, tmp_path, monkeypatch):
    """git runs the driver from the worktree's top directory — the one place the orphans
    land — so each merge cleans up after the ones that were interrupted."""
    import os
    import time

    orphan = tmp_path / ".merge_file_Q1w2E3"
    orphan.write_text("{}", encoding="utf-8")
    old = time.time() - 3600
    os.utime(orphan, (old, old))
    monkeypatch.chdir(tmp_path)
    text = _render(sides[0])["commands.json"]
    assert _run(driver, tmp_path, "commands.json", text, text, text)[0] == 0
    assert not orphan.exists()
