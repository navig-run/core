from __future__ import annotations

import builtins
import importlib
import pytest

pytestmark = pytest.mark.integration


def _reload_selector_module():
    import navig.cli.selector as selector

    importlib.reload(selector)
    selector._hints_shown.clear()
    return selector


def test_hint_prints_once_on_tty(monkeypatch, capsys):
    selector = _reload_selector_module()

    monkeypatch.setattr(selector.sys.stderr, "isatty", lambda: True)

    selector._hint("k1", "hello")
    selector._hint("k1", "hello")

    err = capsys.readouterr().err
    assert err.count("hello") == 1


def test_hint_suppressed_when_not_tty(monkeypatch, capsys):
    selector = _reload_selector_module()

    monkeypatch.setattr(selector.sys.stderr, "isatty", lambda: False)
    selector._hint("k1", "hidden")

    err = capsys.readouterr().err
    assert "hidden" not in err


def test_fzf_or_fallback_emits_fzf_hint_when_tty(monkeypatch, capsys):
    selector = _reload_selector_module()

    commands = [selector.CommandEntry(name="host", description="Host commands")]

    monkeypatch.setattr(selector.shutil, "which", lambda _name: None)
    monkeypatch.setattr(selector.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(selector.sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr(selector, "_arrow_selector", lambda cmds, prompt: None)

    selector.fzf_or_fallback(commands, prompt="> ")

    err = capsys.readouterr().err
    assert "install fzf" in err.lower()


def test_fzf_or_fallback_no_hint_when_stdin_not_tty(monkeypatch, capsys):
    selector = _reload_selector_module()

    commands = [selector.CommandEntry(name="host", description="Host commands")]

    monkeypatch.setattr(selector.shutil, "which", lambda _name: None)
    monkeypatch.setattr(selector.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr(selector.sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr(selector, "_arrow_selector", lambda cmds, prompt: None)

    selector.fzf_or_fallback(commands, prompt="> ")

    err = capsys.readouterr().err
    assert "install fzf" not in err.lower()


def test_arrow_selector_importerror_emits_readchar_hint(monkeypatch, capsys):
    selector = _reload_selector_module()

    commands = [selector.CommandEntry(name="host", description="Host commands")]

    monkeypatch.setattr(selector.sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr(selector, "_numbered_prompt", lambda cmds, prompt: None)

    original_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "readchar":
            raise ImportError("readchar missing")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)

    selector._arrow_selector(commands, "> ")

    err = capsys.readouterr().err
    assert "navig[interactive]" in err


def test_fzf_hint_printed_once_across_multiple_calls(monkeypatch, capsys):
    selector = _reload_selector_module()

    commands = [selector.CommandEntry(name="host", description="Host commands")]

    monkeypatch.setattr(selector.shutil, "which", lambda _name: None)
    monkeypatch.setattr(selector.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(selector.sys.stderr, "isatty", lambda: True)
    monkeypatch.setattr(selector, "_arrow_selector", lambda cmds, prompt: None)

    selector.fzf_or_fallback(commands)
    selector.fzf_or_fallback(commands)

    err = capsys.readouterr().err
    hint_lines = [line for line in err.splitlines() if "tip: install fzf" in line.lower()]
    assert len(hint_lines) == 1
