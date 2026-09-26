"""`navig repo sync` — return the MAIN checkout to the default branch, or refuse.

The installed navig runs from the main checkout, so a checkout parked on someone's
feature branch quietly means every `navig` command is older than `main`. Sync fixes that
— but never at the cost of work in flight, which is what these tests pin: a live lock, a
dirty tree and unpushed commits each block it, with a message that says why.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from navig.commands import repo as R

pytestmark = pytest.mark.unit


def _plan(
    monkeypatch,
    *,
    branch: str,
    dirty: str = "",
    unpushed: str = "",
    unpushed_rc: int = 0,
    lock: dict | None = None,
    default: str = "main",
) -> dict:
    """Drive ``sync_plan`` over faked git output — no repository required."""

    class _Res:
        def __init__(self, stdout: str = "", returncode: int = 0, stderr: str = ""):
            self.stdout = stdout
            self.returncode = returncode
            self.stderr = stderr

    def fake_git(args, cwd, timeout=None):
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return _Res(branch + "\n")
        if args[0] == "status":
            return _Res(dirty)
        if args[0] == "log":
            return _Res(unpushed, unpushed_rc)
        return _Res()

    monkeypatch.setattr(R, "_git", fake_git)
    monkeypatch.setattr(R, "default_branch", lambda root: default)
    monkeypatch.setattr(R, "read_lock", lambda root: lock)
    return R.sync_plan(Path("C:/repo"))


def _held_by_other(minutes: int = 2) -> dict:
    return {
        "session_id": "someone-else",
        "branch": "feat/their-work",
        "claimed_at": (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(),
        "updated_at": (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat(),
    }


def test_already_on_main_only_fast_forwards(monkeypatch):
    plan = _plan(monkeypatch, branch="main")
    assert plan["blocked"] is None
    assert plan["actions"] == ["pull --ff-only"]


def test_a_clean_pushed_branch_is_switched_then_pulled(monkeypatch):
    plan = _plan(monkeypatch, branch="feat/done", unpushed="")
    assert plan["blocked"] is None
    assert plan["actions"] == ["checkout main", "pull --ff-only"]


def test_a_live_lock_blocks_and_names_the_holder(monkeypatch):
    plan = _plan(monkeypatch, branch="feat/theirs", lock=_held_by_other())
    # `lock_state` shortens the session id, so match the branch it names (and the id's stem).
    assert (
        plan["blocked"] and "feat/their-work" in plan["blocked"] and "someone-" in plan["blocked"]
    )
    assert plan["actions"] == []


def test_a_stale_lock_does_not_block(monkeypatch):
    stale = _held_by_other(minutes=24 * 60)
    plan = _plan(monkeypatch, branch="main", lock=stale)
    assert plan["blocked"] is None


def test_a_dirty_tree_blocks(monkeypatch):
    plan = _plan(monkeypatch, branch="main", dirty=" M core/navig/x.py\n")
    assert plan["blocked"] and "uncommitted" in plan["blocked"]


def test_unpushed_commits_block_the_switch(monkeypatch):
    plan = _plan(monkeypatch, branch="feat/wip", unpushed="abc123 wip\ndef456 more\n")
    assert plan["blocked"] and "not on origin" in plan["blocked"]
    # A branch with no upstream (git exits non-zero) is treated the same way.
    plan = _plan(monkeypatch, branch="feat/new", unpushed="", unpushed_rc=128)
    assert plan["blocked"] and "not on origin" in plan["blocked"]


def test_cli_dry_run_reports_without_touching_git(monkeypatch, capsys):
    import typer

    monkeypatch.setattr(R, "_require_root", lambda repo=None: Path("C:/repo"))
    monkeypatch.setattr(
        R,
        "sync_plan",
        lambda root: {
            "root": str(root),
            "branch": "feat/x",
            "default_branch": "main",
            "dirty": False,
            "lock": {"state": "free"},
            "blocked": None,
            "actions": ["checkout main", "pull --ff-only"],
        },
    )
    ran: list = []
    monkeypatch.setattr(R, "_git", lambda *a, **k: ran.append(a) or None)

    R.sync_cmd(repo=None, dry_run=True, json_out=True)
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["dry_run"] is True and payload["synced"] is False
    assert ran == []

    monkeypatch.setattr(
        R,
        "sync_plan",
        lambda root: {
            "root": str(root),
            "branch": "feat/x",
            "default_branch": "main",
            "dirty": True,
            "lock": {"state": "free"},
            "blocked": "feat/x has uncommitted changes — commit or stash them first",
            "actions": [],
        },
    )
    with pytest.raises(typer.Exit) as exc:
        R.sync_cmd(repo=None, dry_run=False, json_out=True)
    assert exc.value.exit_code == 1
    assert json.loads(capsys.readouterr().out.strip())["synced"] is False
