from __future__ import annotations

from pathlib import Path

import pytest


def _invoke_cli(args: list[str], capsys) -> tuple[int, str, str]:
    from navig.cli import app

    exit_code = 0
    try:
        result = app(args, standalone_mode=False)
        if isinstance(result, int) and result != 0:
            exit_code = result
    except SystemExit as exc:
        exit_code = int(exc.code or 0)

    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


@pytest.fixture(autouse=True)
def _register_commands(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.chdir(tmp_path)

    from navig.config import reset_config_manager

    reset_config_manager()

    import navig.cli as cli_mod

    cli_mod._config_manager = None
    cli_mod._NO_CACHE = False
    cli_mod._register_external_commands(register_all=True)

    yield


def test_ask_alias_forwards_to_ai_ask(monkeypatch, capsys):
    captured: dict[str, object] = {}

    def fake_ask_ai(question: str, model: str | None, options: dict):
        captured["question"] = question
        captured["model"] = model
        captured["options_type"] = type(options).__name__
        captured["ask_detected_mode"] = options.get("ask_detected_mode")
        captured["ask_detected_confidence"] = options.get("ask_detected_confidence")

    import navig.commands.ai as ai_mod

    monkeypatch.setattr(ai_mod, "ask_ai", fake_ask_ai)

    code, out, err = _invoke_cli(["ask", "hello from alias", "--model", "demo-model"], capsys)

    combined = (out + err).lower()
    assert code == 0
    assert captured["question"] == "hello from alias"
    assert captured["model"] == "demo-model"
    assert captured["options_type"] == "dict"
    assert captured.get("ask_detected_mode")
    assert isinstance(captured.get("ask_detected_confidence"), float)
    assert "deprecated" not in combined


def test_ask_plan_mode_does_not_call_ask_ai(monkeypatch, capsys):
    """navig ask --plan should preview route and not execute ask_ai."""
    called = {"count": 0}

    def fake_ask_ai(*_a, **_kw):
        called["count"] += 1

    import navig.commands.ai as ai_mod

    monkeypatch.setattr(ai_mod, "ask_ai", fake_ask_ai)

    code, out, err = _invoke_cli(["ask", "restart nginx on prod", "--plan"], capsys)

    combined = out + err
    assert code == 0
    assert called["count"] == 0
    assert "intent route" in combined.lower()
    assert "plan preview only" in combined.lower()


def test_ask_plan_execute_calls_ask_ai(monkeypatch, capsys):
    """navig ask --plan --execute should preview route and execute ask_ai."""
    called = {"count": 0, "mode": None}

    def fake_ask_ai(_question: str, _model: str | None, options: dict):
        called["count"] += 1
        called["mode"] = options.get("ask_detected_mode")

    import navig.commands.ai as ai_mod

    monkeypatch.setattr(ai_mod, "ask_ai", fake_ask_ai)

    code, out, err = _invoke_cli(
        ["ask", "restart nginx on prod", "--plan", "--execute"],
        capsys,
    )

    combined = out + err
    assert code == 0
    assert called["count"] == 1
    assert called["mode"]
    assert "intent route" in combined.lower()


def test_chat_command_emits_deprecation_warning(monkeypatch, capsys):
    """navig chat must print a deprecation warning pointing to navig ask."""
    import navig.commands.chat as chat_mod

    monkeypatch.setattr(chat_mod, "run_ai_chat", lambda *a, **kw: None)

    _code, out, err = _invoke_cli(["chat"], capsys)
    assert "deprecated" in (out + err).lower()
    assert "navig ask" in (out + err)


def test_ai_ask_subcommand_emits_deprecation_warning(monkeypatch, capsys):
    """navig ai ask must print a deprecation warning pointing to navig ask."""
    import navig.commands.ai as ai_mod

    monkeypatch.setattr(ai_mod, "ask_ai", lambda *a, **kw: None)

    _code, out, err = _invoke_cli(["ai", "ask", "test question"], capsys)
    assert "deprecated" in (out + err).lower()
    assert "navig ask" in (out + err)


def test_ai_explain_subcommand_emits_deprecation_warning(monkeypatch, capsys):
    """navig ai explain must print a deprecation warning and still delegate."""
    import navig.commands.ai as ai_mod

    monkeypatch.setattr(ai_mod, "ask_ai", lambda *a, **kw: None)

    _code, out, err = _invoke_cli(["ai", "explain", "/var/log/syslog"], capsys)
    assert "deprecated" in (out + err).lower()
    assert "navig ask" in (out + err)


def test_ai_suggest_subcommand_emits_deprecation_warning(monkeypatch, capsys):
    """navig ai suggest must print a deprecation warning and still delegate."""
    import navig.commands.ai as ai_mod

    monkeypatch.setattr(ai_mod, "ask_ai", lambda *a, **kw: None)

    _code, out, err = _invoke_cli(["ai", "suggest"], capsys)
    assert "deprecated" in (out + err).lower()
    assert "navig ask" in (out + err)


def test_ai_diagnose_subcommand_emits_deprecation_warning(monkeypatch, capsys):
    """navig ai diagnose must print a deprecation warning and still delegate."""
    import types
    import sys

    from navig.commands import ai as ai_mod

    assistant_stub = types.SimpleNamespace(analyze_cmd=lambda *_a, **_kw: None)
    monkeypatch.setitem(sys.modules, "navig.commands.assistant", assistant_stub)

    _code, out, err = _invoke_cli(["ai", "diagnose"], capsys)
    assert "deprecated" in (out + err).lower()
    assert "navig ask" in (out + err)


def test_brain_subapp_is_reachable(capsys):
    """navig brain must be reachable (not 'No such command') after registration."""
    code, out, err = _invoke_cli(["brain", "prompts", "list"], capsys)
    combined = out + err
    # Command should be found — "No such command" indicates missing registration
    assert "no such command" not in combined.lower()
    assert "error: no such command 'brain'" not in combined.lower()


def test_explain_command_is_removed(capsys):
    """navig explain must no longer be registered (all sub-commands were stubs)."""
    import click

    # In standalone_mode=False, unknown commands raise UsageError rather than
    # printing to stderr and calling sys.exit().
    try:
        code, out, err = _invoke_cli(["explain", "command", "test"], capsys)
        combined = (out + err).lower()
        assert "no such command" in combined
    except click.exceptions.UsageError as exc:
        assert "no such command" in str(exc).lower()
