"""Tests for the ``navig doctor`` Repo Guard check (check_repo_guard).

The check is repo-relative: silent outside git, informational when the guard
is absent, warning on partial wiring, green with lock detail when active.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from navig.commands.doctor import check_repo_guard
from navig.commands.repo import guard_install_cmd


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git("init", "-b", "main", cwd=root)
    _git("config", "user.email", "test@navig.local", cwd=root)
    _git("config", "user.name", "navig-test", cwd=root)
    (root / "a.txt").write_text("x\n", encoding="utf-8")
    _git("add", "a.txt", cwd=root)
    _git("commit", "-m", "base", cwd=root)
    return root


def test_outside_git_repo_is_silent(monkeypatch) -> None:
    # NOT tmp_path: core/conftest.py points PYTEST_DEBUG_TEMPROOT at core/.dev/tmp, which
    # lives INSIDE this repo — "outside a git repo" needs the system temp dir instead.
    import tempfile

    with tempfile.TemporaryDirectory() as outside:
        monkeypatch.chdir(outside)
        result = check_repo_guard()
        monkeypatch.chdir(Path(__file__).parent)  # step out so Windows can delete the dir
    assert result == []


def test_uninstalled_guard_is_informational_not_failure(repo: Path, monkeypatch) -> None:
    monkeypatch.chdir(repo)
    results = check_repo_guard()
    assert len(results) == 1
    _icon, ok, line = results[0]
    assert ok is True  # absence must never fail doctor
    assert "guard install" in line


def test_installed_guard_reports_active_and_lock_free(repo: Path, monkeypatch) -> None:
    guard_install_cmd(repo=str(repo))
    monkeypatch.chdir(repo)
    results = check_repo_guard()
    _icon, ok, line = results[0]
    assert ok is True
    assert "active" in line and "lock free" in line


def test_an_outdated_hook_script_turns_the_row_into_a_warning(repo: Path, monkeypatch) -> None:
    """Wired is not current. The row used to check the events and the lock and render ✓
    over hook scripts of any age — a repo fully wired to the pre-#1481 agent_lock.py (the
    one that blocked `navig repo new` from a worktree) read "active". A green light that
    does not consult the script state is the "green over unknown" trap."""
    guard_install_cmd(repo=str(repo))
    hook = repo / ".claude" / "hooks" / "agent_lock.py"
    hook.write_text(hook.read_text(encoding="utf-8") + "\n# an older build\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    _icon, ok, line = check_repo_guard()[0]
    assert ok is False, "an outdated hook script must not render as a green tick"
    assert "agent_lock.py outdated" in line
    assert "guard install" in line
    assert "active" in line, "the wiring IS active — say so alongside the warning, not instead of it"


def test_a_missing_hook_script_behind_live_wiring_warns(repo: Path, monkeypatch) -> None:
    guard_install_cmd(repo=str(repo))
    (repo / ".claude" / "hooks" / "session_start.py").unlink()
    monkeypatch.chdir(repo)
    _icon, ok, line = check_repo_guard()[0]
    assert ok is False
    assert "session_start.py missing" in line


def test_partial_wiring_warns(repo: Path, monkeypatch) -> None:
    guard_install_cmd(repo=str(repo))
    settings_path = repo / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    del settings["hooks"]["SessionStart"]  # half-wired guard = false safety
    settings_path.write_text(json.dumps(settings), encoding="utf-8")

    monkeypatch.chdir(repo)
    results = check_repo_guard()
    _icon, ok, line = results[0]
    assert ok is False
    assert "partially wired" in line and "SessionStart" in line


def test_orphaned_worktree_dirs_add_a_warn_row(repo: Path, monkeypatch) -> None:
    guard_install_cmd(repo=str(repo))
    (repo / ".dev" / "worktrees" / "dead").mkdir(parents=True)  # git-untracked leftover
    monkeypatch.chdir(repo)
    results = check_repo_guard()

    assert any("active" in line for _i, _o, line in results)  # guard row still present
    orphan_rows = [(ok, line) for _i, ok, line in results if "orphaned dir" in line]
    assert len(orphan_rows) == 1
    ok, line = orphan_rows[0]
    assert ok is False and "navig repo prune" in line


def test_no_orphan_row_when_clean(repo: Path, monkeypatch) -> None:
    guard_install_cmd(repo=str(repo))
    monkeypatch.chdir(repo)
    results = check_repo_guard()
    assert not any("orphaned dir" in line for _i, _o, line in results)  # silent when clean


def _driver_rows(results):
    return [(ok, line) for _i, ok, line in results if "registered" in line or "merge" in line.lower()]


def test_no_merge_driver_row_when_none_is_declared(repo: Path, monkeypatch) -> None:
    (repo / ".gitattributes").write_text("*.sh text eol=lf\nCHANGELOG.md merge=union\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    assert _driver_rows(check_repo_guard()) == []  # builtin strategies need no config


def test_an_unregistered_merge_driver_warns(repo: Path, monkeypatch) -> None:
    """A driver lives in per-clone git config, never committed; without it git silently
    line-merges, so the generated command list conflicts as if the driver did not exist."""
    (repo / ".gitattributes").write_text("gen/commands.json merge=navig-manifest\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    rows = _driver_rows(check_repo_guard())
    assert len(rows) == 1
    ok, line = rows[0]
    assert ok is False, "an unregistered driver must not render as a green tick"
    assert "navig-manifest" in line and "not registered" in line
    assert "git config merge.navig-manifest.driver" in line  # a repo that is not navig's


def test_navig_repo_is_told_npm_install(repo: Path, monkeypatch) -> None:
    (repo / ".gitattributes").write_text("gen/commands.json merge=navig-manifest\n", encoding="utf-8")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "ci-local.mjs").write_text("// registers drivers\n", encoding="utf-8")
    monkeypatch.chdir(repo)
    _ok, line = _driver_rows(check_repo_guard())[0]
    assert "npm install" in line


def test_a_registered_merge_driver_is_green(repo: Path, monkeypatch) -> None:
    (repo / ".gitattributes").write_text(
        "# routed\ngen/a.json merge=navig-manifest\ngen/b.json  merge=navig-manifest\n", encoding="utf-8"
    )
    _git("config", "merge.navig-manifest.driver", "py -3 x.py %O %A %B %P", cwd=repo)
    monkeypatch.chdir(repo)
    rows = _driver_rows(check_repo_guard())
    assert rows == [(True, rows[0][1])]
    assert "registered: navig-manifest" in rows[0][1]


# ── the wiring decides what runs, so it decides the report ────────────────────


def _wired(settings_dir: Path, *, root: Path, hooked: str, command: str) -> dict:
    """A settings file pointing at *hooked*, plus the two files that could disagree."""
    from navig.commands.repo import _guard_script_state
    from navig.guard import template_text

    settings_dir.mkdir(parents=True, exist_ok=True)
    (settings_dir / "settings.json").write_text(
        json.dumps({"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": command}]}]}}),
        encoding="utf-8",
    )
    settings = json.loads((settings_dir / "settings.json").read_text(encoding="utf-8"))
    template = template_text("agent_lock.py")
    standard = root / ".claude" / "hooks" / "agent_lock.py"
    standard.parent.mkdir(parents=True, exist_ok=True)
    standard.write_text(template, encoding="utf-8")  # current unless a test overwrites it
    wired = root / hooked
    wired.parent.mkdir(parents=True, exist_ok=True)
    wired.write_text(template, encoding="utf-8")
    assert callable(_guard_script_state)
    return settings


def test_a_stale_unwired_copy_cannot_make_a_current_guard_read_outdated(tmp_path: Path) -> None:
    """The failure this repo showed on 2026-10-20: settings wire `scripts/agent-hooks/`,
    whose file matched the template byte for byte, but an older copy sat in `.claude/hooks/`
    and was checked first — so `guard status` called a healthy guard outdated and told the
    operator to re-install. The unwired copy is not what runs."""
    from navig.commands.repo import _guard_script_state
    from navig.guard import template_text

    root = tmp_path
    settings = _wired(
        root / ".claude", root=root, hooked="scripts/agent-hooks/agent_lock.py",
        command='python "${CLAUDE_PROJECT_DIR}/scripts/agent-hooks/agent_lock.py"',
    )
    (root / ".claude" / "hooks" / "agent_lock.py").write_text(
        template_text("agent_lock.py") + "# an older, unwired build\n", encoding="utf-8"
    )

    state, path = _guard_script_state(root, settings, "agent_lock.py")
    assert state == "current", f"a stale unwired copy reported the running guard as {state}"
    assert path is not None and "agent-hooks" in path.replace("\\", "/"), path


def test_a_stale_WIRED_hook_is_reported_even_when_the_standard_copy_is_current(tmp_path: Path) -> None:
    """The other direction is the dangerous one: a current `.claude/hooks/` copy must not
    hide a drifted hook that Claude Code actually executes."""
    from navig.commands.repo import _guard_script_state
    from navig.guard import template_text

    root = tmp_path
    settings = _wired(
        root / ".claude", root=root, hooked="scripts/agent-hooks/agent_lock.py",
        command='python "${CLAUDE_PROJECT_DIR}/scripts/agent-hooks/agent_lock.py"',
    )
    (root / "scripts" / "agent-hooks" / "agent_lock.py").write_text(
        template_text("agent_lock.py") + "# drifted from the template\n", encoding="utf-8"
    )

    state, path = _guard_script_state(root, settings, "agent_lock.py")
    assert state == "outdated", "the hook that runs drifted and must be reported"
    assert path is not None and "agent-hooks" in path.replace("\\", "/"), path
