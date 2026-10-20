"""`navig voice` without a usable navig-audio says what to do, for every command it knows.

The voice commands live in navig-audio (``navig_audio.commands.voice``) and core mounts them. A
Typer group with no commands answers "No such command 'speak'", which gives no hint that a
plugin is missing, so the fallback registers each name and prints the install line (exit 2).
An installed navig-audio older than 0.2 has the engines but not the commands: say "update".
"""

from __future__ import annotations

import importlib
import importlib.util
import sys

import pytest
import typer
from typer.testing import CliRunner

from tests.fixtures.module_eviction import evicted_modules


def _fallback_app(monkeypatch: pytest.MonkeyPatch, *, audio_installed: bool) -> typer.Typer:
    monkeypatch.setitem(sys.modules, "navig_audio.commands.voice", None)  # import -> ImportError
    real_find_spec = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a: (object() if audio_installed else None)
        if name == "navig_audio"
        else real_find_spec(name, *a),
    )
    # The shared helper drops the fallback module on exit and restores the real one (and the
    # package attribute), so it never leaks into a later test in the same worker.
    with evicted_modules(monkeypatch, ("navig.commands.voice",)):
        mod = importlib.import_module("navig.commands.voice")
    root = typer.Typer()
    root.add_typer(mod.voice_app, name="voice")

    @root.callback()
    def _root() -> None:
        pass

    return root


@pytest.mark.parametrize(
    "args",
    [["voice", "speak", "hi", "--no-play"], ["voice", "transcribe", "a.wav", "-p", "x"], ["voice", "list-voices"]],
)
def test_every_voice_command_names_the_plugin_when_it_is_missing(monkeypatch, args) -> None:
    res = CliRunner().invoke(_fallback_app(monkeypatch, audio_installed=False), args)
    assert res.exit_code == 2, res.output
    assert "navig-audio" in res.output and "No such command" not in res.output


def test_an_old_navig_audio_is_told_to_update(monkeypatch) -> None:
    res = CliRunner().invoke(_fallback_app(monkeypatch, audio_installed=True), ["voice", "speak", "hi"])
    assert res.exit_code == 2, res.output
    assert "pip install -U navig-audio" in res.output


def test_with_navig_audio_the_real_commands_are_mounted() -> None:
    pytest.importorskip("navig_audio.commands.voice")
    from navig_audio.commands.voice import voice_app as impl

    from navig.commands.voice import voice_app

    assert voice_app is impl
