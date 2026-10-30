"""`navig cdp eval '<long JS>'` crashed on Windows before the command ran.

Click (under Typer) glob-expands every argument on Windows and catches only `re.error`.
A long expression full of `/` and `[` made `glob._iglob` recurse once per path segment
until RecursionError, and the CLI died with a crash report (seen 2026-10-10 driving the
vault SVG opener page). `navig.platform.cli_args` expands per argument, skips anything
longer than a path, and falls back to the literal argument.

It also stops Click substituting local **environment variables** into arguments: doing so
sent `navig run "echo $HOME"`'s LOCAL home directory (or a credential-bearing variable)
to the remote host, which should expand its own.
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path

import pytest

from navig.platform import cli_args

# The shape that crashed: many path segments that each contain a glob character.
DEEP = "/".join(["a[b"] * 1500)


def test_the_original_crash_is_real_in_click():
    """Guard against a vacuous pass: Click's own expansion must still blow up on this."""
    from click.utils import _expand_args

    with pytest.raises(RecursionError):
        _expand_args([DEEP])


def test_a_deep_pattern_passes_through_literally():
    assert cli_args.expand_windows_args([DEEP]) == [DEEP]


def test_one_bad_argument_does_not_sink_the_others(tmp_path, monkeypatch):
    (tmp_path / "a.txt").write_text("x")
    monkeypatch.chdir(tmp_path)
    assert cli_args.expand_windows_args(["eval", DEEP, "*.txt"]) == ["eval", DEEP, "a.txt"]


def test_long_content_is_never_globbed(tmp_path, monkeypatch):
    """A long argument is code or a message — even one that would match files."""
    (tmp_path / "a.txt").write_text("x")
    monkeypatch.chdir(tmp_path)
    js = "*.txt" + " " * cli_args.MAX_PATTERN_LEN
    assert cli_args.expand_windows_args([js]) == [js]


def test_short_patterns_still_expand_like_before(tmp_path, monkeypatch):
    for name in ("a.txt", "b.txt", "c.md"):
        (tmp_path / name).write_text("x")
    monkeypatch.chdir(tmp_path)
    assert sorted(cli_args.expand_windows_args(["*.txt"])) == ["a.txt", "b.txt"]
    assert cli_args.expand_windows_args(["nomatch*.zip"]) == ["nomatch*.zip"]
    assert cli_args.expand_windows_args(["x[0]"]) == ["x[0]"]


def test_main_switches_clicks_own_expansion_off():
    """Without this, Click would expand a second time and the crash would come back."""
    from navig import main as navig_main

    src = inspect.getsource(navig_main)
    assert "app(args=cli_args(), windows_expand_args=False)" in src


def test_cli_args_is_none_off_windows(monkeypatch):
    monkeypatch.setattr(cli_args.sys, "platform", "linux")
    assert cli_args.cli_args() is None


# ── environment variables must never be expanded ────────────────────────────


def test_a_local_variable_is_never_substituted_into_an_argument(monkeypatch):
    """Click expands `$VAR`/`%VAR%` on Windows — that ships LOCAL values to the target.

    `navig run "echo $HOME"` must reach the remote shell as `$HOME`, so the remote
    expands its own. Substituting here would send this computer's home directory, and a
    credential-bearing variable would send the credential.
    """
    monkeypatch.setenv("NAVG_EXPAND_LEAK", "C:/local/secret-value")
    for literal in ("echo $NAVG_EXPAND_LEAK", "echo %NAVG_EXPAND_LEAK%"):
        assert cli_args.expand_windows_args([literal]) == [literal]


def test_a_variable_is_not_expanded_even_when_it_looks_like_a_glob(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVG_EXPAND_LEAK", "*.txt")
    (tmp_path / "a.txt").write_text("x")
    monkeypatch.chdir(tmp_path)
    assert cli_args.expand_windows_args(["$NAVG_EXPAND_LEAK"]) == ["$NAVG_EXPAND_LEAK"]


def test_path_shaped_expansion_is_unchanged(tmp_path, monkeypatch):
    """The reason Windows expansion exists at all still works: ~ and globs."""
    for name in ("a.txt", "b.txt", "keep.md"):
        (tmp_path / name).write_text("x")
    monkeypatch.chdir(tmp_path)
    assert sorted(cli_args.expand_windows_args(["*.txt"])) == ["a.txt", "b.txt"]
    home = cli_args.expand_windows_args(["~"])
    assert home and home[0] != "~" and home[0] in (str(Path.home()), os.path.expanduser("~"))
