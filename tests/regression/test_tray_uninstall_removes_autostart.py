"""`navig tray uninstall` must actually remove the auto-start entry — or say it didn't.

The command called `winreg.OpenSubKey`, which is not a function (the real name is
`OpenKey`). It raised AttributeError on every run, an `except Exception` downgraded that
to a warning, and the command finished with `ch.success("NAVIG Tray uninstalled")` and
exit 0. The Run entry that `desktop/install-tray.ps1` writes was therefore never once
removed: the tray came back at the next login, after the user had uninstalled it and
been told it worked.

Two things are pinned here — that the *right* API is used, and that a registry failure
can no longer end in a green tick.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

TRAY_SRC = Path(__file__).resolve().parents[2] / "navig" / "commands" / "tray.py"


def test_every_winreg_call_names_a_real_function() -> None:
    """The defect in its exact shape. Source-level so it holds on any platform —
    the bug was a name that exists on none."""
    winreg = pytest.importorskip("winreg")

    tree = ast.parse(TRAY_SRC.read_text(encoding="utf-8-sig"))
    used = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "winreg"
    }
    assert used, "no winreg usage found — this test is guarding nothing"

    missing = sorted(a for a in used if not hasattr(winreg, a))
    assert not missing, f"tray.py calls winreg attributes that do not exist: {missing}"


def test_the_registry_key_is_not_re_typed_as_a_literal() -> None:
    """`desktop/tray_app.py` owns the key path and value name, and the installer writes
    that same pair. They were duplicated as literals here, so renaming either would
    have orphaned the removal a second time — silently, exactly like the first."""
    src = TRAY_SRC.read_text(encoding="utf-8-sig")
    assert "REGISTRY_KEY" in src and "REGISTRY_VALUE" in src
    assert "CurrentVersion\\Run" not in src.replace(
        'f\'"HKCU\\\\{REGISTRY_KEY}"', ""
    ), "the Run key path is hardcoded again instead of imported from tray_app"


@pytest.mark.skipif(sys.platform != "win32", reason="tray uninstall is Windows-only")
def test_a_registry_failure_does_not_report_a_successful_uninstall(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The honesty half. A partial uninstall leaves the tray starting at login — the
    one thing the command exists to stop — so it must not exit 0 with a green tick."""
    import typer

    import navig.commands.tray as tray

    monkeypatch.setattr(tray, "_is_tray_running", lambda: (False, None))
    monkeypatch.setattr(tray, "config_dir", lambda: tmp_path)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    import winreg

    def _boom(*a: object, **k: object) -> None:
        raise OSError("access is denied")

    monkeypatch.setattr(winreg, "OpenKey", _boom)

    messages: list[str] = []
    monkeypatch.setattr(tray.ch, "success", lambda m, *a, **k: messages.append(f"OK:{m}"))
    monkeypatch.setattr(tray.ch, "error", lambda m, *a, **k: messages.append(f"ERR:{m}"))
    monkeypatch.setattr(tray.ch, "info", lambda m, *a, **k: None)
    monkeypatch.setattr(tray.ch, "warning", lambda m, *a, **k: None)

    with pytest.raises(typer.Exit) as excinfo:
        tray.tray_uninstall()

    assert excinfo.value.exit_code == 1
    assert not [m for m in messages if m == "OK:NAVIG Tray uninstalled"], (
        "claimed a successful uninstall while the auto-start entry survived"
    )
    assert any("still start at login" in m for m in messages)


@pytest.mark.skipif(sys.platform != "win32", reason="tray uninstall is Windows-only")
def test_a_clean_uninstall_still_reports_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The partner. A 'fix' that always failed would satisfy the test above."""
    import navig.commands.tray as tray

    monkeypatch.setattr(tray, "_is_tray_running", lambda: (False, None))
    monkeypatch.setattr(tray, "config_dir", lambda: tmp_path)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    import winreg

    class _Key:
        def __enter__(self) -> _Key:
            return self

        def __exit__(self, *a: object) -> None:
            return None

    monkeypatch.setattr(winreg, "OpenKey", lambda *a, **k: _Key())
    monkeypatch.setattr(winreg, "DeleteValue", lambda *a, **k: None)

    messages: list[str] = []
    monkeypatch.setattr(tray.ch, "success", lambda m, *a, **k: messages.append(m))
    monkeypatch.setattr(tray.ch, "info", lambda m, *a, **k: None)
    monkeypatch.setattr(tray.ch, "error", lambda m, *a, **k: messages.append(f"ERR:{m}"))
    monkeypatch.setattr(tray.ch, "warning", lambda m, *a, **k: None)

    tray.tray_uninstall()  # must NOT raise

    assert "NAVIG Tray uninstalled" in messages
    assert not [m for m in messages if m.startswith("ERR:")]
