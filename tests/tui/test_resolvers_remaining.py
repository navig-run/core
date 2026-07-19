"""Source-guarding tests for the resolvers that were audited-correct but had no
coverage: daemon, vault, provider, agent.

The 7 fixed silent-dead badges (#319/#346/#352/#359/#364) each got a regression
test locking the source they read. These four read the RIGHT source today — but a
badge with no test is exactly how the silent-dead class recurs, so these lock their
sources too (most importantly the daemon pid path).
"""

from __future__ import annotations

import navig.agent_config_loader as agent_loader_mod
import navig.platform.windows_utils as winutils_mod
import navig.settings.resolver as settings_mod
import navig.tui.resolvers as res_mod
import navig.vault.manager as vault_mod
from navig.tui.resolvers import (
    resolve_agent,
    resolve_daemon,
    resolve_provider,
    resolve_vault,
)

# ── Daemon — locks the pid path config_dir()/daemon/supervisor.pid ───────────


def test_daemon_ok_reads_supervisor_pid(monkeypatch, tmp_path):
    # The supervisor writes _daemon_dir()/supervisor.pid == config_dir()/daemon/…
    (tmp_path / "daemon").mkdir()
    (tmp_path / "daemon" / "supervisor.pid").write_text("4242", encoding="utf-8")
    monkeypatch.setattr(res_mod, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(winutils_mod, "check_pid_exists", lambda pid: True)

    badge = resolve_daemon()

    assert badge.status == "ok"
    assert "4242" in badge.detail


def test_daemon_missing_without_pid_file(monkeypatch, tmp_path):
    monkeypatch.setattr(res_mod, "config_dir", lambda: tmp_path)  # no daemon/supervisor.pid
    assert resolve_daemon().status == "missing"


def test_daemon_missing_when_pid_dead(monkeypatch, tmp_path):
    (tmp_path / "daemon").mkdir()
    (tmp_path / "daemon" / "supervisor.pid").write_text("4242", encoding="utf-8")
    monkeypatch.setattr(res_mod, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(winutils_mod, "check_pid_exists", lambda pid: False)
    assert resolve_daemon().status == "missing"


# ── Vault ────────────────────────────────────────────────────────────────────


def test_vault_ok_when_list_succeeds(monkeypatch):
    class _VM:
        def list(self):
            return []

    monkeypatch.setattr(vault_mod, "VaultManager", _VM)
    assert resolve_vault().status == "ok"


def test_vault_warn_when_unavailable(monkeypatch):
    class _VM:
        def list(self):
            raise RuntimeError("locked")

    monkeypatch.setattr(vault_mod, "VaultManager", _VM)
    assert resolve_vault().status == "warn"


# ── Provider ─────────────────────────────────────────────────────────────────


def test_provider_ok_from_settings(monkeypatch):
    monkeypatch.setattr(res_mod, "_load_navig_json", lambda: {})
    monkeypatch.setattr(
        settings_mod, "get", lambda key, default="": "claude-opus" if key == "navig.ai.provider" else default
    )
    badge = resolve_provider()
    assert badge.status == "ok"
    assert "claude-opus" in badge.detail


def test_provider_ok_from_navig_json_model(monkeypatch):
    monkeypatch.setattr(res_mod, "_load_navig_json", lambda: {"agents": {"defaults": {"model": "gpt-x"}}})
    monkeypatch.setattr(settings_mod, "get", lambda key, default="": default)
    badge = resolve_provider()
    assert badge.status == "ok"
    assert "gpt-x" in badge.detail


def test_provider_missing_when_unset(monkeypatch):
    monkeypatch.setattr(res_mod, "_load_navig_json", lambda: {})
    monkeypatch.setattr(settings_mod, "get", lambda key, default="": default)
    assert resolve_provider().status == "missing"


# ── Agent ────────────────────────────────────────────────────────────────────


def test_agent_ok_from_agent_json(monkeypatch, tmp_path):
    class _Cfg:
        llm_mode = "auto"
        name = "The Navigator"
        id = "navig"

    monkeypatch.setattr(agent_loader_mod, "load_agent_json", lambda name: _Cfg())
    # No soul.json in either location → indicator absent, still ok.
    monkeypatch.setattr(res_mod, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(res_mod, "builtin_store_dir", lambda: tmp_path)
    badge = resolve_agent()
    assert badge.status == "ok"
    assert "The Navigator" in badge.detail


def test_agent_missing_when_no_config(monkeypatch):
    monkeypatch.setattr(agent_loader_mod, "load_agent_json", lambda name: None)
    assert resolve_agent().status == "missing"
