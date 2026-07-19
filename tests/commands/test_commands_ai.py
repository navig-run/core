from __future__ import annotations

from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.integration


def test_ask_ai_windows_tasklist_decode_fallback(monkeypatch):
    # Targets the tasklist CSV decode path only. platform.* is stubbed so the
    # global subprocess.run replacement below can't leak into OS detection —
    # otherwise platform._syscmd_ver makes its OWN subprocess.run call, skewing
    # subprocess_calls[0]. Platform-failure resilience is covered separately by
    # the _client_platform_context tests.
    import navig.commands.ai as ai_mod

    captured: dict[str, object] = {}

    class FakeAIAssistant:
        def __init__(self, config_manager):
            self.config_manager = config_manager

        def ask(self, question, context, model_override=None, **kwargs):
            captured["question"] = question
            captured["context"] = context
            captured["model"] = model_override
            return "ok"

    class FakeConfigManager:
        def get_active_server(self):
            return "local"

        def host_exists(self, name):
            return True

        def load_server_config(self, name):
            return {"host": "localhost", "is_local": True, "type": "local"}

    class FakeRemoteOps:
        def __init__(self, _cfg):
            pass

        def execute_command(self, _cmd, _server_config):
            return SimpleNamespace(returncode=0, stdout="")

    subprocess_calls: list[dict] = []

    def fake_run(*args, **kwargs):
        subprocess_calls.append({"args": args, "kwargs": kwargs})
        # Invalid UTF-8 byte sequence to simulate Windows decode edge-cases
        raw = b'"nginx.exe","1234","Console","1","12,000 K"\n"weird\\xff","9","Console","1","1 K"\n'
        return SimpleNamespace(returncode=0, stdout=raw)

    monkeypatch.setattr(ai_mod, "os", SimpleNamespace(name="nt"))
    monkeypatch.setattr(ai_mod.subprocess, "run", fake_run)
    # Keep OS detection off the (globally-faked) subprocess path so the only
    # recorded call is the tasklist probe we're actually asserting on.
    monkeypatch.setattr(ai_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ai_mod.platform, "release", lambda: "11")
    monkeypatch.setattr(ai_mod.platform, "machine", lambda: "AMD64")

    import navig.ai as ai_module
    import navig.config as cfg_module
    import navig.remote as remote_module

    monkeypatch.setattr(ai_module, "AIAssistant", FakeAIAssistant)
    monkeypatch.setattr(cfg_module, "get_config_manager", lambda: FakeConfigManager())
    monkeypatch.setattr(remote_module, "RemoteOperations", FakeRemoteOps)
    monkeypatch.setattr(ai_mod.ch, "print_markdown", lambda _text: None)

    ai_mod.ask_ai("status", None, {})

    assert captured["question"] == "status"
    assert "processes" in captured["context"]
    assert any("nginx" in line.lower() for line in captured["context"]["processes"])
    assert subprocess_calls
    assert subprocess_calls[0]["kwargs"].get("text") is False


def test_humanize_fallback_reason_prose():
    from navig.commands.ai import _humanize_fallback_reason

    # Known categories become readable prose (no machine tokens with underscores).
    assert _humanize_fallback_reason("rate_limited") == "rate-limited"
    assert _humanize_fallback_reason("auth") == "failing to authenticate"
    assert _humanize_fallback_reason("payment") == "having a billing issue"
    # Empty / None → a safe generic word.
    assert _humanize_fallback_reason(None) == "unavailable"
    assert _humanize_fallback_reason("") == "unavailable"
    # Unknown category degrades gracefully (de-underscored), never a KeyError.
    assert _humanize_fallback_reason("some_new_category") == "some new category"


def test_humanize_covers_every_rotatable_category():
    # Guard: every category the rotation loop can surface has a friendly phrase,
    # so no rotation notice ever shows a raw underscore token. (from/to reasons
    # come from categorize_error via complete_via_connection's on_fallback.)
    from navig.llm.fallback_policy import _CATEGORY_PHRASE, _ROTATE_WORTHY

    missing = {c for c in _ROTATE_WORTHY if c not in _CATEGORY_PHRASE}
    assert not missing, f"unmapped rotatable categories: {missing}"


def test_cli_humanizer_delegates_to_canonical():
    # The CLI helper is a thin wrapper over the single source of truth, so the two
    # never drift.
    from navig.commands.ai import _humanize_fallback_reason
    from navig.llm.fallback_policy import describe_category

    for cat in ("rate_limited", "auth", "payment", "cooldown", "dead_model", "", None):
        assert _humanize_fallback_reason(cat) == describe_category(cat)


def test_client_platform_context_normal(monkeypatch):
    import navig.commands.ai as ai_mod

    monkeypatch.setattr(ai_mod.platform, "system", lambda: "Windows")
    monkeypatch.setattr(ai_mod.platform, "release", lambda: "11")
    monkeypatch.setattr(ai_mod.platform, "machine", lambda: "AMD64")
    assert ai_mod._client_platform_context() == ("Windows 11", "AMD64")


def test_client_platform_context_degrades_when_release_raises(monkeypatch):
    # Regression: platform.release() can shell out (platform._syscmd_ver) and, on
    # odd locales/code pages, raise. That must NOT crash `navig ask` — OS detection
    # is optional context. The OS label degrades; the architecture is preserved.
    import navig.commands.ai as ai_mod

    def _boom():
        raise RuntimeError("cannot use a string pattern on a bytes-like object")

    monkeypatch.setattr(ai_mod.platform, "release", _boom)
    monkeypatch.setattr(ai_mod.platform, "machine", lambda: "AMD64")
    os_label, arch = ai_mod._client_platform_context()
    assert os_label == "unknown"   # degraded, did not raise
    assert arch == "AMD64"         # independent field survives


def test_client_platform_context_arch_failure_isolated(monkeypatch):
    import navig.commands.ai as ai_mod

    monkeypatch.setattr(ai_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(ai_mod.platform, "release", lambda: "6.1")
    monkeypatch.setattr(
        ai_mod.platform, "machine", lambda: (_ for _ in ()).throw(RuntimeError("x"))
    )
    os_label, arch = ai_mod._client_platform_context()
    assert os_label == "Linux 6.1"
    assert arch == "unknown"
