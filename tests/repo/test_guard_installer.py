"""Tests for ``navig repo guard`` — installer, status, uninstall, template sync.

Uses throwaway git repos under tmp_path; never touches the real checkout.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from navig.commands.repo import (
    guard_install_cmd,
    guard_status_cmd,
    guard_uninstall_cmd,
)
from navig.guard import HOOK_FILES, template_text

_REPO_ROOT = Path(__file__).resolve().parents[3]


def _git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True)


@pytest.fixture()
def target(tmp_path: Path) -> Path:
    root = tmp_path / "user-repo"
    root.mkdir()
    _git("init", "-b", "main", cwd=root)
    _git("config", "user.email", "test@navig.local", cwd=root)
    _git("config", "user.name", "navig-test", cwd=root)
    (root / "README.md").write_text("hello\n", encoding="utf-8")
    _git("add", "README.md", cwd=root)
    _git("commit", "-m", "base", cwd=root)
    return root


def test_status_resolves_the_portable_placeholder(target: Path, capsys) -> None:
    """`guard status` must expand ${CLAUDE_PROJECT_DIR} exactly as Claude Code does.

    Without that, a perfectly healthy portable (committed) wiring resolves to a literal
    "${CLAUDE_PROJECT_DIR}/..." path and status reports the scripts as **missing** —
    a false alarm that would push people straight back to absolute paths.
    """
    guard_install_cmd(repo=str(target))
    capsys.readouterr()  # drain the installer's output — only status' JSON must remain
    guard_status_cmd(repo=str(target), json_out=True)
    data = json.loads(capsys.readouterr().out)

    assert data["scripts"] == dict.fromkeys(HOOK_FILES, "current")
    assert all(data["wired"].values())
    for path in data["script_paths"].values():
        assert path and "CLAUDE_PROJECT_DIR" not in path  # resolved, not literal


def test_navig_repo_ships_its_own_portable_guard_wiring() -> None:
    """This repo's OWN .claude/settings.json must be committed AND portable.

    The guard protected nobody on a fresh clone: `.claude/` was gitignored, so the wiring
    never travelled with the repo, and the absolute paths inside meant it could not have
    been committed anyway. It points at scripts/agent-hooks/ (tracked), so a clone has the
    scripts already — only the wiring was missing.
    """
    settings_path = _REPO_ROOT / ".claude" / "settings.json"
    assert settings_path.is_file(), "the repo-guard wiring must ship with the repo"

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    for event in ("PreToolUse", "SessionEnd", "SessionStart"):
        entries = settings["hooks"][event]
        assert entries, f"{event} is not wired"
        for entry in entries:
            for h in entry["hooks"]:
                cmd = h["command"]
                assert "${CLAUDE_PROJECT_DIR}" in cmd, f"{event} wiring is not portable: {cmd}"
                assert ":" not in cmd.split("${CLAUDE_PROJECT_DIR}")[0].replace("python ", ""), (
                    f"{event} wiring looks machine-absolute: {cmd}"
                )
                # Must point at the TRACKED hook copies — .claude/hooks/ is not committed,
                # so wiring a fresh clone to it would reference files that do not exist.
                assert "scripts/agent-hooks/" in cmd, f"{event} must use the tracked hooks: {cmd}"


def test_templates_stay_in_sync_with_scripts_copies() -> None:
    """scripts/agent-hooks/ are this repo's live copies of navig.guard — no drift."""
    for name in HOOK_FILES:
        live = (_REPO_ROOT / "scripts" / "agent-hooks" / name).read_text(encoding="utf-8")
        assert live == template_text(name), (
            f"scripts/agent-hooks/{name} drifted from navig/guard/{name} — "
            "edit navig/guard/ (canonical) and copy over the scripts/ twin."
        )


def test_install_writes_hooks_wiring_and_gitignore(target: Path) -> None:
    guard_install_cmd(repo=str(target))

    for name in HOOK_FILES:
        assert (target / ".claude" / "hooks" / name).read_text(encoding="utf-8") == template_text(
            name
        )

    settings = json.loads((target / ".claude" / "settings.json").read_text(encoding="utf-8"))
    pre = settings["hooks"]["PreToolUse"]
    assert len(pre) == 1
    assert "PowerShell" in pre[0]["matcher"]
    assert "agent_lock.py" in pre[0]["hooks"][0]["command"]
    assert settings["hooks"]["SessionEnd"] and settings["hooks"]["SessionStart"]

    # PORTABLE wiring: ${CLAUDE_PROJECT_DIR} (a Claude Code path placeholder, expanded to
    # the project root) instead of a machine-absolute path. This is the whole point — an
    # absolute path could not be committed, so every fresh clone of a guarded repo
    # silently had NO guard until someone remembered `navig repo guard install`. Nobody
    # remembers. Still cwd-proof: it expands to an absolute path at run time, and python's
    # file-not-found exit code is 2, which Claude Code treats as BLOCK (fails closed).
    for event in ("PreToolUse", "SessionEnd", "SessionStart"):
        for entry in settings["hooks"][event]:
            for h in entry["hooks"]:
                cmd = h["command"]
                assert "${CLAUDE_PROJECT_DIR}" in cmd, f"{event} wiring is not portable: {cmd}"
                # The regression that matters: no machine-absolute path may leak back in.
                assert target.as_posix() not in cmd, f"{event} wiring leaked an absolute path: {cmd}"

    gitignore = (target / ".gitignore").read_text(encoding="utf-8")
    assert ".dev/" in gitignore


def test_install_is_idempotent_and_preserves_user_hooks(target: Path) -> None:
    claude_dir = target / ".claude"
    claude_dir.mkdir()
    user_settings = {
        "permissions": {"allow": ["Bash(npm run *)"]},
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user-hook"}]}]},
    }
    (claude_dir / "settings.json").write_text(json.dumps(user_settings), encoding="utf-8")

    guard_install_cmd(repo=str(target))
    guard_install_cmd(repo=str(target))  # second run must not duplicate

    settings = json.loads((claude_dir / "settings.json").read_text(encoding="utf-8"))
    assert settings["permissions"] == {"allow": ["Bash(npm run *)"]}  # untouched
    pre = settings["hooks"]["PreToolUse"]
    assert len(pre) == 2  # user's + ours, exactly once
    commands = [h["command"] for e in pre for h in e["hooks"]]
    assert commands.count("echo user-hook") == 1
    assert sum("agent_lock.py" in c for c in commands) == 1
    assert len(settings["hooks"]["SessionEnd"]) == 1
    assert len(settings["hooks"]["SessionStart"]) == 1


def test_install_refuses_invalid_settings_json(target: Path) -> None:
    claude_dir = target / ".claude"
    claude_dir.mkdir()
    (claude_dir / "settings.json").write_text("{broken", encoding="utf-8")
    import typer

    with pytest.raises(typer.Exit):
        guard_install_cmd(repo=str(target))
    # the broken file was not clobbered
    assert (claude_dir / "settings.json").read_text(encoding="utf-8") == "{broken"


def test_status_json_reports_installed_state(target: Path, capsys) -> None:
    guard_status_cmd(repo=str(target), json_out=True)
    before = json.loads(capsys.readouterr().out)
    assert before["scripts"] == dict.fromkeys(HOOK_FILES, "missing")
    assert not any(before["wired"].values())

    guard_install_cmd(repo=str(target))
    capsys.readouterr()
    guard_status_cmd(repo=str(target), json_out=True)
    after = json.loads(capsys.readouterr().out)
    assert after["scripts"] == dict.fromkeys(HOOK_FILES, "current")
    assert all(after["wired"].values())
    assert after["dev_ignored"] is True
    assert after["lock"]["state"] == "free"


def test_status_recognizes_custom_path_wiring(target: Path, capsys) -> None:
    """Wiring that points at non-standard script locations (like the navig repo's
    scripts/agent-hooks/) must read as current, not missing."""
    custom_dir = target / "scripts" / "agent-hooks"
    custom_dir.mkdir(parents=True)
    for name in HOOK_FILES:
        (custom_dir / name).write_text(template_text(name), encoding="utf-8")
    claude_dir = target / ".claude"
    claude_dir.mkdir()
    lock = (custom_dir / "agent_lock.py").as_posix()
    start = (custom_dir / "session_start.py").as_posix()
    settings = {
        "hooks": {
            "PreToolUse": [
                {"matcher": "Bash", "hooks": [{"type": "command", "command": f'python "{lock}"'}]}
            ],
            "SessionEnd": [{"hooks": [{"type": "command", "command": f"python {lock}"}]}],
            "SessionStart": [{"hooks": [{"type": "command", "command": f"python {start}"}]}],
        }
    }
    (claude_dir / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    guard_status_cmd(repo=str(target), json_out=True)
    data = json.loads(capsys.readouterr().out)
    assert data["scripts"] == dict.fromkeys(HOOK_FILES, "current")
    assert all(data["wired"].values())
    assert "agent-hooks" in data["script_paths"]["agent_lock.py"]


def test_uninstall_removes_ours_keeps_users_and_modified(target: Path) -> None:
    guard_install_cmd(repo=str(target))
    settings_path = target / ".claude" / "settings.json"
    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    settings["hooks"]["PreToolUse"].append(
        {"matcher": "Bash", "hooks": [{"type": "command", "command": "echo user-hook"}]}
    )
    settings_path.write_text(json.dumps(settings), encoding="utf-8")
    # locally modify one script — uninstall must keep it
    lock_script = target / ".claude" / "hooks" / "agent_lock.py"
    lock_script.write_text(lock_script.read_text(encoding="utf-8") + "\n# local tweak\n", encoding="utf-8")

    guard_uninstall_cmd(repo=str(target))

    settings = json.loads(settings_path.read_text(encoding="utf-8"))
    commands = [
        h["command"] for e in settings["hooks"]["PreToolUse"] for h in e["hooks"]
    ]
    assert commands == ["echo user-hook"]
    assert "SessionEnd" not in settings["hooks"]
    assert "SessionStart" not in settings["hooks"]
    assert lock_script.exists()  # modified — kept
    assert not (target / ".claude" / "hooks" / "session_start.py").exists()  # pristine — removed
