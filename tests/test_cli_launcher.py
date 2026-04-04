from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    "stdin_tty,stdout_tty",
    [
        (False, True),
        (True, False),
        (False, False),
    ],
)
def test_smart_launch_non_tty_exits_with_hint(monkeypatch, capsys, stdin_tty: bool, stdout_tty: bool):
    """smart_launch must short-circuit in any non-TTY environment."""
    from navig.cli import launcher as launcher_mod

    monkeypatch.setattr(launcher_mod.sys, "stdin", SimpleNamespace(isatty=lambda: stdin_tty))
    monkeypatch.setattr(launcher_mod.sys, "stdout", SimpleNamespace(isatty=lambda: stdout_tty))

    with pytest.raises(SystemExit) as exc:
        launcher_mod.smart_launch("host", app=SimpleNamespace())

    captured = capsys.readouterr()
    assert exc.value.code == 0
    assert "non-tty detected" in captured.err.lower()
    assert "navig host --help" in captured.err.lower()
