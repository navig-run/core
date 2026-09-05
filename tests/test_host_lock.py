"""Tests for the per-host advisory lock (navig.core.host_lock)."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from navig.core import host_lock


@pytest.fixture()
def lockdir(tmp_path, monkeypatch):
    """Isolate lock storage and identity so tests never touch the real ~/.navig."""
    monkeypatch.setenv("NAVIG_HOST_LOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setenv("NAVIG_SESSION_ID", "session-A")
    monkeypatch.delenv("CLAUDE_SESSION_ID", raising=False)
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    monkeypatch.delenv("NAVIG_HOST_LOCK", raising=False)
    return tmp_path / "locks"


# ── identity ─────────────────────────────────────────────────────────────────


def test_explicit_session_id_wins(lockdir, monkeypatch):
    monkeypatch.setenv("NAVIG_SESSION_ID", "explicit-1")
    assert host_lock.session_id() == "explicit-1"
    assert host_lock.identity_quality() == "explicit"


def test_claude_session_id_is_accepted(lockdir, monkeypatch):
    monkeypatch.delenv("NAVIG_SESSION_ID", raising=False)
    monkeypatch.setenv("CLAUDE_SESSION_ID", "claude-9")
    assert host_lock.session_id() == "claude-9"
    assert host_lock.identity_quality() == "explicit"


def test_identity_degrades_honestly(lockdir, monkeypatch):
    """With no session env and no tty, identity is weak — and says so."""
    monkeypatch.delenv("NAVIG_SESSION_ID", raising=False)
    monkeypatch.delenv("WT_SESSION", raising=False)
    monkeypatch.setattr(host_lock, "_tty_token", lambda: None)
    assert host_lock.identity_quality() == "weak"
    assert "@" in host_lock.session_id()


def test_blank_session_id_is_ignored(lockdir, monkeypatch):
    monkeypatch.setenv("NAVIG_SESSION_ID", "   ")
    monkeypatch.setattr(host_lock, "_tty_token", lambda: None)
    assert host_lock.identity_quality() == "weak"


# ── mode ─────────────────────────────────────────────────────────────────────


def test_mode_defaults_to_block(lockdir):
    assert host_lock.mode() == "block"


@pytest.mark.parametrize(
    "value,expected", [("warn", "warn"), ("OFF", "off"), ("nonsense", "block")]
)
def test_mode_parsing(lockdir, monkeypatch, value, expected):
    monkeypatch.setenv("NAVIG_HOST_LOCK", value)
    assert host_lock.mode() == expected


# ── claim / state ────────────────────────────────────────────────────────────


def test_claim_then_state_is_mine(lockdir):
    host_lock.claim("vps-1", "doing a thing")
    st = host_lock.lock_state(host_lock.read_lock("vps-1"))
    assert st.state == "mine"
    assert st.blocking is False
    assert st.command == "doing a thing"


def test_free_when_no_lock(lockdir):
    assert host_lock.lock_state(host_lock.read_lock("never-touched")).state == "free"


def test_other_live_session_blocks(lockdir, monkeypatch):
    host_lock.claim("vps-1", "session A work")
    monkeypatch.setenv("NAVIG_SESSION_ID", "session-B")
    st = host_lock.lock_state(host_lock.read_lock("vps-1"))
    assert st.state == "held"
    assert st.blocking is True
    assert st.session == "session-A"


def test_expired_lock_is_stale_not_blocking(lockdir, monkeypatch):
    host_lock.claim("vps-1")
    monkeypatch.setenv("NAVIG_SESSION_ID", "session-B")
    future = datetime.now(timezone.utc) + timedelta(minutes=host_lock.LOCK_TTL_MINUTES + 5)
    st = host_lock.lock_state(host_lock.read_lock("vps-1"), now=future)
    assert st.state == "stale"
    assert st.blocking is False


def test_corrupt_lock_never_blocks(lockdir):
    """A truncated/garbage lock file must not jam a host forever."""
    path = host_lock.lock_path("vps-1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert host_lock.read_lock("vps-1") is None
    assert host_lock.lock_state(host_lock.read_lock("vps-1")).state == "free"


def test_unparseable_timestamp_is_stale(lockdir):
    path = host_lock.lock_path("vps-1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"session_id": "other", "updated_at": "nope"}), encoding="utf-8")
    st = host_lock.lock_state(host_lock.read_lock("vps-1"))
    assert st.state == "stale"
    assert st.blocking is False


def test_refresh_preserves_claimed_at(lockdir):
    """Re-claiming in the same session keeps 'held for N minutes' truthful."""
    first = host_lock.claim("vps-1", "cmd one")
    second = host_lock.claim("vps-1", "cmd two")
    assert second["claimed_at"] == first["claimed_at"]
    assert second["updated_at"] >= first["updated_at"]
    assert second["command"] == "cmd two"


def test_host_names_are_filesystem_safe(lockdir):
    host_lock.claim("evil/../name:1")
    files = list(lockdir.iterdir())
    assert len(files) == 1
    assert "/" not in files[0].name and "\\" not in files[0].name


def test_separate_hosts_have_separate_locks(lockdir, monkeypatch):
    host_lock.claim("vps-1")
    monkeypatch.setenv("NAVIG_SESSION_ID", "session-B")
    assert host_lock.lock_state(host_lock.read_lock("vps-1")).blocking is True
    assert host_lock.lock_state(host_lock.read_lock("vps-2")).state == "free"


# ── release ──────────────────────────────────────────────────────────────────


def test_release_own_lock(lockdir):
    host_lock.claim("vps-1")
    ok, _ = host_lock.release("vps-1")
    assert ok
    assert host_lock.read_lock("vps-1") is None


def test_release_refuses_to_steal_live_lock(lockdir, monkeypatch):
    host_lock.claim("vps-1")
    monkeypatch.setenv("NAVIG_SESSION_ID", "session-B")
    ok, reason = host_lock.release("vps-1")
    assert not ok
    assert "session-A" in reason
    assert host_lock.read_lock("vps-1") is not None, "lock must survive a refused release"


def test_force_release_steals(lockdir, monkeypatch):
    host_lock.claim("vps-1")
    monkeypatch.setenv("NAVIG_SESSION_ID", "session-B")
    ok, _ = host_lock.release("vps-1", force=True)
    assert ok
    assert host_lock.read_lock("vps-1") is None


def test_release_missing_lock_is_success(lockdir):
    ok, reason = host_lock.release("never-touched")
    assert ok
    assert "no lock" in reason


# ── guard (the enforcement path) ─────────────────────────────────────────────


def test_guard_claims_when_free(lockdir):
    st = host_lock.guard("vps-1", "navig run: uptime")
    assert st.state == "free"
    assert host_lock.lock_state(host_lock.read_lock("vps-1")).state == "mine"


def test_guard_blocks_on_conflict(lockdir, monkeypatch):
    import typer

    host_lock.claim("vps-1", "session A work")
    monkeypatch.setenv("NAVIG_SESSION_ID", "session-B")
    with pytest.raises(typer.Exit) as exc:
        host_lock.guard("vps-1", "navig run: rm -rf")
    assert exc.value.exit_code == 2
    # The original holder must still own it after a blocked attempt.
    monkeypatch.setenv("NAVIG_SESSION_ID", "session-A")
    assert host_lock.lock_state(host_lock.read_lock("vps-1")).state == "mine"


def test_guard_warn_mode_proceeds(lockdir, monkeypatch):
    host_lock.claim("vps-1", "session A work")
    monkeypatch.setenv("NAVIG_SESSION_ID", "session-B")
    monkeypatch.setenv("NAVIG_HOST_LOCK", "warn")
    st = host_lock.guard("vps-1", "navig run: uptime")
    assert st.state == "held"  # reported, but no exception


def test_guard_off_mode_is_inert(lockdir, monkeypatch):
    host_lock.claim("vps-1", "session A work")
    monkeypatch.setenv("NAVIG_SESSION_ID", "session-B")
    monkeypatch.setenv("NAVIG_HOST_LOCK", "off")
    st = host_lock.guard("vps-1")
    assert st.state == "free"
    # off must not steal the other session's lock
    assert host_lock.read_lock("vps-1")["session_id"] == "session-A"


def test_guard_takes_over_stale_lock(lockdir, monkeypatch):
    """A dead session must not hold a host hostage past the TTL."""
    stale_at = (
        datetime.now(timezone.utc) - timedelta(minutes=host_lock.LOCK_TTL_MINUTES + 10)
    ).isoformat()
    path = host_lock.lock_path("vps-1")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"session_id": "dead-session", "claimed_at": stale_at, "updated_at": stale_at}),
        encoding="utf-8",
    )
    st = host_lock.guard("vps-1", "navig run: uptime")
    assert st.state == "stale"
    assert host_lock.read_lock("vps-1")["session_id"] == "session-A"


# ── guard_remote (the one-liner every mutating command calls) ─────────────────


class _FakeConfig:
    def __init__(self, local_hosts=()):
        self._local = set(local_hosts)

    def is_local_host(self, host):
        return host in self._local


def test_guard_remote_skips_local_host(lockdir):
    """Locking the local machine protects nothing and would be pure friction."""
    cfg = _FakeConfig(local_hosts={"localhost"})
    assert host_lock.guard_remote(cfg, "localhost", "navig run: ls") is None
    assert host_lock.read_lock("localhost") is None


def test_guard_remote_claims_remote_host(lockdir):
    cfg = _FakeConfig(local_hosts={"localhost"})
    st = host_lock.guard_remote(cfg, "vps-1", "navig file add: /etc/x")
    assert st is not None and st.state == "free"
    assert host_lock.lock_state(host_lock.read_lock("vps-1")).state == "mine"


def test_guard_remote_ignores_missing_host(lockdir):
    cfg = _FakeConfig()
    assert host_lock.guard_remote(cfg, None, "navig run: ls") is None


def test_guard_remote_treats_unknown_host_as_remote(lockdir):
    """If is_local_host explodes, fail toward locking — the safer direction."""

    class Exploding:
        def is_local_host(self, host):
            raise RuntimeError("no such host")

    st = host_lock.guard_remote(Exploding(), "mystery", "navig run: ls")
    assert st is not None
    assert host_lock.lock_state(host_lock.read_lock("mystery")).state == "mine"


def test_guard_remote_blocks_when_another_session_holds(lockdir, monkeypatch):
    import typer

    host_lock.claim("vps-1", "session A work")
    monkeypatch.setenv("NAVIG_SESSION_ID", "session-B")
    with pytest.raises(typer.Exit) as exc:
        host_lock.guard_remote(_FakeConfig(), "vps-1", "navig file remove: /var/www")
    assert exc.value.exit_code == 2
