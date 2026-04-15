from __future__ import annotations

from types import SimpleNamespace
import pytest

pytestmark = pytest.mark.integration


def test_ask_ai_windows_tasklist_decode_fallback(monkeypatch):
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
