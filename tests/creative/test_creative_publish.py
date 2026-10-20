"""Authorized publishing: allow-list, visibility, scoped commit, retry (scenarios 7, 8, 10).

Every test runs against a LOCAL bare repository — no real GitHub, no network.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from navig.creative import build as build_mod
from navig.creative import policy
from navig.creative import proposals as prop
from navig.creative.decide import ArtifactSpec, Decision

_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"}


def _git(dest: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", "-C", str(dest), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        env=_ENV, timeout=60,
    )
    if check and proc.returncode != 0:
        raise AssertionError(f"git {args} failed: {proc.stdout}\n{proc.stderr}")
    return (proc.stdout or "").strip()


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    from navig.agent.proactive import user_state
    from navig.config import reset_config_manager
    from navig.core.shared_config import ConfigSingleton

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    reset_config_manager()
    ConfigSingleton._instance = None
    monkeypatch.setattr(user_state, "_tracker_instance", None)
    yield
    reset_config_manager()
    ConfigSingleton._instance = None


@pytest.fixture()
def repo(tmp_path):
    """A local bare origin + a checked-out worktree on branch main."""
    bare = tmp_path / "origin.git"
    work = tmp_path / "checkout"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, env=_ENV, capture_output=True)
    subprocess.run(["git", "init", str(work)], check=True, env=_ENV, capture_output=True)
    _git(work, "symbolic-ref", "HEAD", "refs/heads/main")
    _git(work, "config", "user.email", "creative@test")
    _git(work, "config", "user.name", "creative-test")
    _git(work, "config", "commit.gpgsign", "false")
    (work / "README.md").write_text("destination\n", encoding="utf-8")
    _git(work, "add", "README.md")
    _git(work, "commit", "-m", "init")
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-u", "origin", "main")
    return SimpleNamespace(bare=bare, work=work, tmp=tmp_path)


def _mode(mode: str) -> None:
    from navig.agent.proactive.user_state import get_user_state_tracker

    assert get_user_state_tracker().set_preference("chat_mode", mode) is True


def _set_policy(**kw) -> None:
    from navig.config import get_config_manager

    cm = get_config_manager()
    for key, value in kw.items():
        cm.set_global(f"creative.publish.{key}", value)


def _built_proposal(slug: str) -> dict:
    _mode("creative")
    d = Decision(
        verdict="create",
        reason="repeated",
        genuine_gap="none",
        plan=["build"],
        artifact=ArtifactSpec(
            kind="skill", slug=slug, title=slug.title(),
            description=f"run {slug} workflow", commands=["navig echo hi"],
        ),
        benefit="x", complexity="low", verification="lint",
    )
    p = prop.from_decision(d, source="detector:test", problem="R", evidence=[], goal=f"go {slug}")
    created, reason = prop.propose(p)
    assert created, reason
    result = build_mod.build_proposal(p["id"])
    assert result["ok"], result
    return prop.get_proposal(p["id"])


# ── 7. authorized private push, unrelated work preserved ─────────────────────


def test_authorized_private_push_scoped(repo, monkeypatch):
    _set_policy(mode="repo", repo=str(repo.work), repos=[str(repo.work)], branch="main")
    p = _built_proposal("publish-me")

    # Unrelated dirty work in the destination — must survive byte-for-byte.
    (repo.work / "README.md").write_text("destination — DIRTY\n", encoding="utf-8")
    (repo.work / "unrelated.tmp").write_text("scratch\n", encoding="utf-8")
    head_before = _git(repo.work, "rev-parse", "HEAD")

    argv: list[tuple] = []
    real_git = policy._git

    def _spy(dest, *args):
        argv.append(args)
        return real_git(dest, *args)

    monkeypatch.setattr(policy, "_git", _spy)
    result = policy.publish_proposal(p["id"])
    monkeypatch.setattr(policy, "_git", real_git)

    assert result["ok"] is True, result
    # Remote received the commit.
    assert _git(repo.bare, "rev-parse", "main") == _git(repo.work, "rev-parse", "HEAD")
    assert _git(repo.work, "rev-parse", "HEAD") != head_before
    # The commit tree contains ONLY the intended path.
    committed = _git(repo.work, "show", "--name-only", "--format=", "HEAD").splitlines()
    assert committed, "commit must contain the skill"
    assert all(c.startswith("skills/publish-me/") for c in committed), committed
    # Unrelated work preserved: still dirty, not committed.
    status = _git(repo.work, "status", "--porcelain")
    assert "README.md" in status and "unrelated.tmp" in status
    assert "README.md" not in committed
    # No force anywhere in the argv we ran.
    assert not any("--force" in a or a == "-f" for call in argv for a in call), argv
    # Version recorded on the proposal.
    row = prop.get_proposal(p["id"])
    assert row["version_ref"]["commit"]
    assert row["version_ref"]["branch"] == "main"
    assert [c["key"] for c in result["checks"] if not c["ok"]] == []


# ── 8. unauthorized / public are blocked ─────────────────────────────────────


def test_destination_outside_allow_list_is_blocked(repo):
    other = repo.tmp / "elsewhere"
    other.mkdir()
    _set_policy(mode="repo", repo=str(repo.work), repos=[str(other)], branch="main")
    p = _built_proposal("not-allowed")
    head_before = _git(repo.work, "rev-parse", "HEAD")

    result = policy.publish_proposal(p["id"])
    assert result["ok"] is False and result["blocked"] is True
    assert "allow_list" in result["reason"]
    # Nothing was written anywhere.
    assert _git(repo.work, "rev-parse", "HEAD") == head_before
    assert not (repo.work / "skills").exists()
    bare_before = subprocess.run(
        ["git", "-C", str(repo.bare), "rev-parse", "main"], capture_output=True, text=True
    ).stdout.strip()
    assert bare_before == head_before


def test_public_visibility_is_always_blocked(repo):
    _set_policy(
        mode="repo", repo=str(repo.work), repos=[str(repo.work)],
        branch="main", allow_public=True, auto_push=True,
    )
    p = _built_proposal("public-blocked")
    head_before = _git(repo.work, "rev-parse", "HEAD")

    result = policy.publish_proposal(p["id"], probe=lambda dest: "public")
    assert result["ok"] is False and result["blocked"] is True
    assert "visibility" in result["reason"]
    # allow_public=True does NOT unblock — public is never publishable.
    assert _git(repo.work, "rev-parse", "HEAD") == head_before
    assert not (repo.work / "skills").exists()


def test_default_local_mode_never_publishes():
    p = _built_proposal("local-only")
    result = policy.publish_proposal(p["id"])
    assert result["ok"] is False and result["blocked"] is True
    assert "local-only" in result["reason"]


def test_automatic_publish_needs_auto_push(repo):
    _set_policy(mode="repo", repo=str(repo.work), repos=[str(repo.work)], branch="main")
    p = _built_proposal("auto-gated")
    result = policy.publish_proposal(p["id"], automatic=True)
    assert result["ok"] is False and result["blocked"] is True
    assert "authorization" in result["reason"]

    _set_policy(auto_push=True)
    result2 = policy.publish_proposal(p["id"], automatic=True)
    assert result2["ok"] is True, result2


def test_fresh_foreign_lock_blocks_publish(repo):
    _set_policy(mode="repo", repo=str(repo.work), repos=[str(repo.work)], branch="main")
    p = _built_proposal("locked-dest")
    lock_dir = repo.work / ".dev"
    lock_dir.mkdir()
    (lock_dir / "agent.lock").write_text(
        json.dumps(
            {
                "session_id": "foreign",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "branch": "x",
            }
        ),
        encoding="utf-8",
    )
    result = policy.publish_proposal(p["id"])
    assert result["ok"] is False and result["blocked"] is True
    assert "lock" in result["reason"]


def test_unknown_branch_blocks_publish(repo):
    _set_policy(mode="repo", repo=str(repo.work), repos=[str(repo.work)], branch="nope")
    p = _built_proposal("bad-branch")
    result = policy.publish_proposal(p["id"])
    assert result["ok"] is False and result["blocked"] is True
    assert "branch" in result["reason"]


def test_stale_plan_fails_head_check(repo):
    """A plan made, then invalidated by other work, must not publish."""
    _set_policy(mode="repo", repo=str(repo.work), repos=[str(repo.work)], branch="main")
    p = _built_proposal("stale-head")
    plan = policy.make_plan(prop.get_proposal(p["id"]))
    assert plan is not None
    # Someone else commits after the plan was captured.
    (repo.work / "other.txt").write_text("moved on\n", encoding="utf-8")
    _git(repo.work, "add", "other.txt")
    _git(repo.work, "commit", "-m", "someone else")
    checks = policy.verify_publish_plan(plan)
    by_key = {c.key: c for c in checks}
    assert by_key["head"].ok is False


# ── secret scan ──────────────────────────────────────────────────────────────


def test_secret_in_capability_blocks_publish(repo, monkeypatch):
    _set_policy(mode="repo", repo=str(repo.work), repos=[str(repo.work)], branch="main")
    p = _built_proposal("leaky-skill")
    # Rewrite the built skill with a secret-shaped line (simulating history content).
    skill_file = build_mod.skill_target_dir("leaky-skill") / "SKILL.md"
    skill_file.write_text(
        skill_file.read_text(encoding="utf-8") + "\n```bash\npassword=hunter2\n```\n",
        encoding="utf-8",
    )
    head_before = _git(repo.work, "rev-parse", "HEAD")

    result = policy.publish_proposal(p["id"])
    assert result["ok"] is False and result["blocked"] is True
    assert "secret scan" in result["reason"]
    assert _git(repo.work, "rev-parse", "HEAD") == head_before  # nothing committed
    # Staged work was rolled back.
    assert "skills/" not in _git(repo.work, "diff", "--cached", "--name-only")


# ── 10. push failure is retryable ────────────────────────────────────────────


def test_push_failure_is_retryable(repo, monkeypatch):
    _set_policy(mode="repo", repo=str(repo.work), repos=[str(repo.work)], branch="main")
    p = _built_proposal("retry-me")

    real_git = policy._git

    def _fail_push(dest, *args):
        if args and args[0] == "push":
            return 1, "remote: rejected (simulated outage)"
        return real_git(dest, *args)

    monkeypatch.setattr(policy, "_git", _fail_push)
    result = policy.publish_proposal(p["id"])
    assert result["ok"] is False
    assert "retryable" in result["reason"]
    # The proposal survives as built with no version yet.
    row = prop.get_proposal(p["id"])
    assert row["status"] == "built"
    assert row.get("version_ref") is None

    # Retry with the outage gone: the pending commit is pushed, not silently skipped.
    monkeypatch.setattr(policy, "_git", real_git)
    retry = policy.publish_proposal(p["id"])
    assert retry["ok"] is True, retry
    assert retry["reason"] in ("published", "pushed existing commit")
    assert _git(repo.bare, "rev-parse", "main") == _git(repo.work, "rev-parse", "HEAD")
    assert prop.get_proposal(p["id"])["version_ref"]["commit"]


def test_publish_requires_built_status():
    _mode("creative")
    d = Decision(verdict="reuse", reason="x", benefit="x", verification="x")
    p = prop.from_decision(d, source="detector:test", problem="R", evidence=[], goal="not-built")
    prop.propose(p)
    result = policy.publish_proposal(p["id"])
    assert result["ok"] is False and result["blocked"] is True
    assert "only a built" in result["reason"]


def test_publish_refused_in_standard_mode(repo):
    p = _built_proposal("standard-no-publish")
    _mode("standard")
    result = policy.publish_proposal(p["id"])
    assert result["ok"] is False and result["blocked"] is True
    assert "Standard" in result["reason"]
