"""Regression: the TUI SSH + Telegram badges read the real config source.

Both read the WRONG source, so they always showed "missing" even when configured —
the same source-mismatch class as the scheduler/task-queue badges (#352):

- `resolve_ssh` read a `hosts:` key from `config.yaml`, but SSH hosts are per-host
  YAML files under `config_dir()/hosts` (+ legacy `apps/`). `get_config_manager().list_hosts()`
  is the canonical lister `navig host list` uses.
- `resolve_telegram` read a flat `TELEGRAM_BOT_TOKEN` config key, but the token lives
  in the vault (vault-first) or under `telegram.bot_token`. `resolve_telegram_bot_token()`
  is the one canonical resolver.
"""

from __future__ import annotations

import navig.config as cfg_mod
import navig.messaging.secrets as secrets_mod
from navig.tui.resolvers import resolve_ssh, resolve_telegram

# ── SSH ────────────────────────────────────────────────────────────────────


class _FakeManager:
    """Stand-in for the ConfigManager — only the method resolve_ssh uses."""

    hosts: list = []

    def list_hosts(self):
        return list(_FakeManager.hosts)


def test_ssh_counts_hosts_from_host_manager(monkeypatch):
    _FakeManager.hosts = ["prod", "staging"]
    monkeypatch.setattr(cfg_mod, "get_config_manager", lambda *a, **k: _FakeManager())

    badge = resolve_ssh()

    assert badge.status == "ok"
    assert "2 hosts active" in badge.detail


def test_ssh_missing_when_no_hosts(monkeypatch):
    _FakeManager.hosts = []
    monkeypatch.setattr(cfg_mod, "get_config_manager", lambda *a, **k: _FakeManager())

    badge = resolve_ssh()

    assert badge.status == "missing"


def test_ssh_reads_real_host_files_without_patching(monkeypatch, tmp_path):
    """No stand-in: a host YAML under an isolated config dir yields an ok badge.

    `navig.core.Config` is the shared-config singleton, which has no list_hosts();
    calling it raised an AttributeError the resolver swallowed, so the badge read
    "missing" for every real install that had hosts.
    """
    cfg_dir = tmp_path / "navig-config"
    (cfg_dir / "hosts").mkdir(parents=True)
    (cfg_dir / "hosts" / "prod.yaml").write_text(
        "name: prod\nhost: 10.0.0.5\nuser: deploy\n", encoding="utf-8"
    )
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg_dir))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cfg_mod, "_config_manager_instance", None)

    badge = resolve_ssh()

    assert badge.status == "ok", badge.detail
    assert "1 host active" in badge.detail


# ── Telegram ───────────────────────────────────────────────────────────────


def test_telegram_ok_when_token_resolves(monkeypatch):
    monkeypatch.setattr(secrets_mod, "resolve_telegram_bot_token", lambda *a, **k: "123:ABC")

    badge = resolve_telegram()

    assert badge.status == "ok"
    assert "configured" in badge.detail


def test_telegram_missing_when_no_token(monkeypatch):
    monkeypatch.setattr(secrets_mod, "resolve_telegram_bot_token", lambda *a, **k: "")

    badge = resolve_telegram()

    assert badge.status == "missing"
