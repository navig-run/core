"""
Focused regressions for _handle_powershell_parsing_error() in navig/main.py.

Covers:
  - plain 'run' / 'r' tokens trigger the handler
  - unknown commands skip the handler (no hint)
  - global-flag-prefixed 'run' still triggers the handler (the core fix)
  - global-flag-prefixed unknown command still skips the handler
"""
from __future__ import annotations

import pytest

import navig.main as main_mod

pytestmark = pytest.mark.unit


def _run_ps(argv_tail: list[str], *, monkeypatch, capsys) -> str:
    """Invoke _handle_powershell_parsing_error under a simulated PS environment
    and return any stderr output written."""
    monkeypatch.setattr(main_mod.sys, "platform", "win32")
    monkeypatch.delenv("PROMPT", raising=False)
    main_mod._handle_powershell_parsing_error(["navig", *argv_tail])
    return capsys.readouterr().err


# ---------------------------------------------------------------------------
# Non-PowerShell environment — never shows hint
# ---------------------------------------------------------------------------

def test_non_ps_run_no_hint(monkeypatch, capsys):
    monkeypatch.setattr(main_mod.sys, "platform", "linux")
    main_mod._handle_powershell_parsing_error(["navig", "run", 'echo \\"hello\\"'])
    assert capsys.readouterr().err == ""


# ---------------------------------------------------------------------------
# PowerShell — plain run token with mangled argument
# ---------------------------------------------------------------------------

def test_ps_plain_run_mangled_quotes_shows_hint(monkeypatch, capsys):
    """A `navig run 'echo \\"hello\\"'` on PS must show the PowerShell hint."""
    stderr = _run_ps(["run", 'echo \\"hello\\"'], monkeypatch=monkeypatch, capsys=capsys)
    assert "PowerShell" in stderr


def test_ps_plain_r_alias_mangled_shows_hint(monkeypatch, capsys):
    stderr = _run_ps(["r", 'echo \\"hello\\"'], monkeypatch=monkeypatch, capsys=capsys)
    assert "PowerShell" in stderr


def test_ps_plain_run_clean_args_no_hint(monkeypatch, capsys):
    """Clean `navig run ls` on PS — no mangled quotes, no hint."""
    stderr = _run_ps(["run", "ls"], monkeypatch=monkeypatch, capsys=capsys)
    assert stderr == ""


def test_ps_unknown_command_skips_handler(monkeypatch, capsys):
    """A non-run command on PS must not trigger the PowerShell hint."""
    stderr = _run_ps(["host", "list"], monkeypatch=monkeypatch, capsys=capsys)
    assert stderr == ""


# ---------------------------------------------------------------------------
# Global-flag-prefixed run token (the core fix)
# ---------------------------------------------------------------------------

def test_ps_prefixed_global_run_mangled_shows_hint(monkeypatch, capsys):
    """`navig --host prod run 'echo \\"hello\\"'` must detect run despite global prefix."""
    stderr = _run_ps(
        ["--host", "prod", "run", 'echo \\"hello\\"'],
        monkeypatch=monkeypatch,
        capsys=capsys,
    )
    assert "PowerShell" in stderr


def test_ps_prefixed_global_run_clean_args_no_hint(monkeypatch, capsys):
    """Prefixed `navig --host prod run ls` with clean args must not hint."""
    stderr = _run_ps(
        ["--host", "prod", "run", "ls"],
        monkeypatch=monkeypatch,
        capsys=capsys,
    )
    assert stderr == ""


def test_ps_prefixed_global_unknown_command_skips_handler(monkeypatch, capsys):
    """`navig --host prod host list` must NOT trigger a PowerShell hint."""
    stderr = _run_ps(
        ["--host", "prod", "host", "list"],
        monkeypatch=monkeypatch,
        capsys=capsys,
    )
    assert stderr == ""


# ---------------------------------------------------------------------------
# Phase D: hostname-value false-positive guard
# ---------------------------------------------------------------------------

def test_ps_hostname_with_odd_quote_does_not_false_positive(monkeypatch, capsys):
    """`navig --host my-server' run ls` must NOT show a PS hint.

    Before the fix, argv[2:] included the hostname value "my-server'" whose
    odd single-quote count triggered the mangled-quote heuristic, producing a
    false-positive PowerShell hint even though the `run ls` arguments were clean.
    After the fix, attempted_cmd is built from _ps_cmd_tokens[1:] (= ["ls"])
    so the hostname is never considered.
    """
    stderr = _run_ps(
        ["--host", "my-server'", "run", "ls"],
        monkeypatch=monkeypatch,
        capsys=capsys,
    )
    assert stderr == "", (
        "False-positive PS hint triggered by odd quote in hostname value; "
        "attempted_cmd must only contain run-command arguments"
    )


def test_ps_hostname_with_backslash_does_not_false_positive(monkeypatch, capsys):
    r"""``navig --host win\server run ls`` must NOT show a PS hint.

    A Windows-style hostname  \\win\server  mangled to  win\server  would
    previously appear in argv[2:] and match the backslash-quote check.
    """
    stderr = _run_ps(
        ["--host", "win\\server", "run", "ls"],
        monkeypatch=monkeypatch,
        capsys=capsys,
    )
    assert stderr == ""
