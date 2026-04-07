"""Tests for resolve_telegram_uid() and ensure_telegram_uid() in navig.messaging.secrets.

Coverage:
    - UID in vault labels only
    - UID in legacy provider credentials only
  - UID in env var only
  - UID in config only (deprecated path, emits warning)
  - Vault wins over config when both present
  - No UID anywhere → None
  - Headless + no env var → RuntimeError
  - ensure_telegram_uid happy path (vault write succeeds)
  - ensure_telegram_uid vault write fails → RuntimeError
"""

from __future__ import annotations

import importlib
import io
import sys

# ── helpers ───────────────────────────────────────────────────────────────────


def _mod():
    return importlib.import_module("navig.messaging.secrets")


def _stub_vault(uid: str | None):
    """Return a minimal vault stub whose get_secret returns ``uid``."""

    class _Vault:
        def get_secret(self, label: str) -> str:  # noqa: ARG002
            if uid is not None:
                return uid
            raise KeyError(label)

        def put(self, label: str, data: bytes) -> str:  # noqa: ARG002
            return "fake-id"

    return _Vault()


def _stub_legacy_no_uid():
    """Legacy provider-credential stub that returns None for every get_secret call."""

    class _Secret:
        def reveal(self) -> str:
            return ""

    class _Legacy:
        def get_secret(self, provider, key, caller=None):  # noqa: ARG002
            return None

    return _Legacy()


# ── 1. UID in vault labels ────────────────────────────────────────────────────


def test_uid_from_vault_labels(monkeypatch):
    mod = _mod()
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_vault", lambda: "111")
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_legacy_store", lambda: None)
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_env_file", lambda: None)
    monkeypatch.delenv("NAVIG_TELEGRAM_UID", raising=False)

    assert mod.resolve_telegram_uid({}) == "111"


# ── 2. UID in legacy provider credentials ─────────────────────────────────────


def test_uid_from_legacy_provider_credentials(monkeypatch):
    mod = _mod()
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_vault", lambda: None)
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_legacy_store", lambda: "222")
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_env_file", lambda: None)
    monkeypatch.delenv("NAVIG_TELEGRAM_UID", raising=False)

    assert mod.resolve_telegram_uid({}) == "222"


# ── 3. UID in env var ────────────────────────────────────────────────────────


def test_uid_from_env_var(monkeypatch):
    mod = _mod()
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_vault", lambda: None)
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_legacy_store", lambda: None)
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_env_file", lambda: None)
    monkeypatch.setenv("NAVIG_TELEGRAM_UID", "333")

    assert mod.resolve_telegram_uid({}) == "333"


# ── 4. UID in config.yaml (deprecated path) ──────────────────────────────────


def test_uid_from_config_deprecated(monkeypatch):
    mod = _mod()
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_vault", lambda: None)
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_legacy_store", lambda: None)
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_env_file", lambda: None)
    monkeypatch.delenv("NAVIG_TELEGRAM_UID", raising=False)

    warnings_emitted: list[str] = []
    monkeypatch.setattr(mod.logger, "warning", lambda msg, *a, **kw: warnings_emitted.append(msg))

    result = mod.resolve_telegram_uid({"telegram": {"user_id": "444"}})

    assert result == "444"
    assert any("deprecated" in w.lower() for w in warnings_emitted)


# ── 5. Vault wins over config ─────────────────────────────────────────────────


def test_vault_wins_over_config(monkeypatch):
    mod = _mod()
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_vault", lambda: "vault-uid")
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_legacy_store", lambda: None)
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_env_file", lambda: None)
    monkeypatch.delenv("NAVIG_TELEGRAM_UID", raising=False)

    result = mod.resolve_telegram_uid({"telegram": {"user_id": "config-uid"}})
    assert result == "vault-uid"


# ── 6. No UID anywhere → None ────────────────────────────────────────────────


def test_no_uid_returns_none(monkeypatch):
    mod = _mod()
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_vault", lambda: None)
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_legacy_store", lambda: None)
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_env_file", lambda: None)
    monkeypatch.delenv("NAVIG_TELEGRAM_UID", raising=False)
    # Stub config manager so no disk read / real config bleeds in
    from types import SimpleNamespace
    monkeypatch.setattr(
        "navig.config.get_config_manager",
        lambda: SimpleNamespace(global_config={}),
        raising=False,
    )

    result = mod.resolve_telegram_uid({})
    assert result is None


# ── 7. Headless + no env → RuntimeError ─────────────────────────────────────


def test_ensure_uid_headless_no_env_raises(monkeypatch):
    mod = _mod()
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_vault", lambda: None)
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_legacy_store", lambda: None)
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_env_file", lambda: None)
    monkeypatch.delenv("NAVIG_TELEGRAM_UID", raising=False)
    monkeypatch.setenv("CI", "true")

    import pytest
    with pytest.raises(RuntimeError, match="NAVIG_TELEGRAM_UID"):
        mod.ensure_telegram_uid(raw_config={})


# ── 8. ensure_telegram_uid happy path ───────────────────────────────────────


def test_ensure_uid_interactive_saves_to_vault(monkeypatch):
    mod = _mod()
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_vault", lambda: None)
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_legacy_store", lambda: None)
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_env_file", lambda: None)
    monkeypatch.delenv("NAVIG_TELEGRAM_UID", raising=False)
    monkeypatch.delenv("CI", raising=False)

    stored: dict = {}

    class _FakeV2:
        def put(self, label: str, data: bytes) -> str:
            stored["label"] = label
            stored["data"] = data
            return "ok"

        def get_secret(self, label: str) -> str:
            raise KeyError(label)

    fake_v2 = _FakeV2()

    # Simulate an interactive TTY by making stdin.isatty() return True
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    # Fake the input() call
    monkeypatch.setattr("builtins.input", lambda _prompt="": "987654321")
    # Suppress print
    monkeypatch.setattr("builtins.print", lambda *a, **kw: None)
    # Stub .env write
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_env_file", lambda: None)

    uid = mod.ensure_telegram_uid(vault=fake_v2, raw_config={})
    assert uid == "987654321"
    assert stored.get("label") == "telegram/user_id"
    import json
    assert json.loads(stored["data"])["value"] == "987654321"


# ── 9. ensure_telegram_uid vault write fails → RuntimeError ──────────────────


def test_ensure_uid_vault_failure_raises(monkeypatch):
    mod = _mod()
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_vault", lambda: None)
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_legacy_store", lambda: None)
    monkeypatch.setattr(mod, "_resolve_telegram_uid_from_env_file", lambda: None)
    monkeypatch.delenv("NAVIG_TELEGRAM_UID", raising=False)
    monkeypatch.delenv("CI", raising=False)

    class _BrokenV2:
        def put(self, label: str, data: bytes) -> str:
            raise OSError("disk full")

        def get_secret(self, label: str) -> str:
            raise KeyError(label)

    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt="": "123456789")
    monkeypatch.setattr("builtins.print", lambda *a, **kw: None)

    import pytest
    with pytest.raises(RuntimeError, match="vault"):
        mod.ensure_telegram_uid(vault=_BrokenV2(), raw_config={})


# ── 10. _telegram_config includes owner_uid ──────────────────────────────────


def test_telegram_config_daemon_includes_owner_uid(monkeypatch):
    registry = importlib.import_module("navig.messaging.registry")
    monkeypatch.setattr(registry, "resolve_telegram_bot_token", lambda cfg=None: "tok")
    monkeypatch.setattr(registry, "resolve_telegram_uid", lambda cfg=None: "555")

    result = registry._telegram_config({})
    assert result.get("owner_uid") == "555"
    assert result.get("bot_token") == "tok"
