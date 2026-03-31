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

    import navig.commands.ai as ai_mod

    monkeypatch.setattr(ai_mod, "ask_ai", fake_ask_ai)

    code, out, err = _invoke_cli(["ask", "hello from alias", "--model", "demo-model"], capsys)

    combined = (out + err).lower()
    assert code == 0
    assert captured["question"] == "hello from alias"
    assert captured["model"] == "demo-model"
    assert captured["options_type"] == "dict"
    assert "deprecated" in combined
    assert "navig ai ask" in combined
