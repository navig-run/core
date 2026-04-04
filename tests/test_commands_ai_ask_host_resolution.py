from __future__ import annotations

from types import SimpleNamespace

import pytest
import typer


def test_ask_ai_uses_global_active_host_when_runtime_empty(monkeypatch):
    import navig.commands.ai as ai_cmd

    captured: dict[str, str] = {}

    class _Cfg:
        def __init__(self):
            self.global_config = {"active_host": "myhost", "ai": {}}

        def get_active_server(self):
            return None

        def host_exists(self, host_name: str) -> bool:
            return host_name == "myhost"

        def load_server_config(self, host_name: str):
            captured["host"] = host_name
            return {
                "name": host_name,
                "host": "127.0.0.1",
                "user": "tester",
                "is_local": True,
                "type": "local",
            }

    class _AI:
        def __init__(self, _cfg):
            pass

        def ask(self, question, context, model_override=None):
            captured["question"] = question
            return "ok"

    class _Remote:
        def __init__(self, _cfg):
            pass

        def execute_command(self, *_args, **_kwargs):
            return SimpleNamespace(returncode=1, stdout="")

    cfg = _Cfg()
    monkeypatch.setattr("navig.config.get_config_manager", lambda: cfg)
    monkeypatch.setattr("navig.ai.AIAssistant", _AI)
    monkeypatch.setattr("navig.remote.RemoteOperations", _Remote)
    monkeypatch.setattr(
        "navig.cli.recovery.require_active_server",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("recovery not expected")),
    )

    monkeypatch.setattr(ai_cmd.ch, "dim", lambda *a, **k: None)
    monkeypatch.setattr(ai_cmd.ch, "print_markdown", lambda *_a, **_k: None)
    monkeypatch.setattr(ai_cmd.ch, "error", lambda *_a, **_k: None)
    monkeypatch.setattr(
        ai_cmd.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=1, stdout=""),
    )

    ai_cmd.ask_ai("hello", None, {})

    assert captured["host"] == "myhost"
    assert captured["question"] == "hello"


def test_ask_ai_missing_key_error_is_host_aware(monkeypatch):
    import navig.commands.ai as ai_cmd

    errors: list[str] = []

    class _Cfg:
        def __init__(self):
            self.global_config = {"ai": {}}

        def get_active_server(self):
            return "alpha"

        def host_exists(self, host_name: str) -> bool:
            return host_name == "alpha"

        def load_server_config(self, host_name: str):
            return {
                "name": host_name,
                "host": "127.0.0.1",
                "user": "tester",
                "is_local": True,
                "type": "local",
            }

    class _AI:
        def __init__(self, _cfg):
            pass

        def ask(self, question, context, model_override=None):
            raise ValueError("OpenRouter API key not configured. Checked source chain.")

    class _Remote:
        def __init__(self, _cfg):
            pass

        def execute_command(self, *_args, **_kwargs):
            return SimpleNamespace(returncode=1, stdout="")

    cfg = _Cfg()
    monkeypatch.setattr("navig.config.get_config_manager", lambda: cfg)
    monkeypatch.setattr("navig.ai.AIAssistant", _AI)
    monkeypatch.setattr("navig.remote.RemoteOperations", _Remote)

    monkeypatch.setattr(ai_cmd.ch, "dim", lambda *a, **k: None)
    monkeypatch.setattr(ai_cmd.ch, "print_markdown", lambda *_a, **_k: None)
    monkeypatch.setattr(ai_cmd.ch, "error", lambda message, *_rest: errors.append(str(message)))
    monkeypatch.setattr(
        ai_cmd.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=1, stdout=""),
    )

    with pytest.raises(typer.Exit):
        ai_cmd.ask_ai("hello", None, {})

    assert errors
    assert "active host 'alpha'" in errors[0]
    assert "OPENROUTER_API_KEY" in errors[0]


def test_ask_ai_accepts_none_options(monkeypatch):
    import navig.commands.ai as ai_cmd

    class _Cfg:
        def __init__(self):
            self.global_config = {"active_host": "myhost", "ai": {}}

        def get_active_server(self):
            return None

        def host_exists(self, host_name: str) -> bool:
            return host_name == "myhost"

        def load_server_config(self, host_name: str):
            return {
                "name": host_name,
                "host": "127.0.0.1",
                "user": "tester",
                "is_local": True,
                "type": "local",
            }

    class _AI:
        def __init__(self, _cfg):
            pass

        def ask(self, question, context, model_override=None):
            return "ok"

    class _Remote:
        def __init__(self, _cfg):
            pass

        def execute_command(self, *_args, **_kwargs):
            return SimpleNamespace(returncode=1, stdout="")

    cfg = _Cfg()
    monkeypatch.setattr("navig.config.get_config_manager", lambda: cfg)
    monkeypatch.setattr("navig.ai.AIAssistant", _AI)
    monkeypatch.setattr("navig.remote.RemoteOperations", _Remote)
    monkeypatch.setattr(ai_cmd.ch, "dim", lambda *a, **k: None)
    monkeypatch.setattr(ai_cmd.ch, "print_markdown", lambda *_a, **_k: None)
    monkeypatch.setattr(ai_cmd.ch, "error", lambda *_a, **_k: None)
    monkeypatch.setattr(
        ai_cmd.subprocess,
        "run",
        lambda *_a, **_k: SimpleNamespace(returncode=1, stdout=""),
    )

    ai_cmd.ask_ai("hello", None, None)


def test_ask_ai_invalid_resolved_host_exits_2(monkeypatch):
    import navig.commands.ai as ai_cmd

    errors: list[str] = []

    class _Cfg:
        def __init__(self):
            self.global_config = {"ai": {}}

        def get_active_server(self):
            return None

        def host_exists(self, host_name: str) -> bool:
            return False

        def load_server_config(self, host_name: str):
            raise FileNotFoundError(host_name)

    cfg = _Cfg()
    monkeypatch.setattr("navig.config.get_config_manager", lambda: cfg)
    monkeypatch.setattr(ai_cmd.ch, "error", lambda message, *_rest: errors.append(str(message)))

    with pytest.raises(typer.Exit) as exc_info:
        ai_cmd.ask_ai("hello", None, {"host": "ghost"})

    assert exc_info.value.exit_code == 2
    assert errors
    assert "Active host 'ghost' not found" in errors[0]
