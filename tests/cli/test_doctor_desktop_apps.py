"""`navig doctor` → Desktop Apps: which Anchor is installed, and whether login starts THAT one.

The outage this pins
--------------------
Anchor ran at every login for weeks as a development binary
(``target/debug/navig-anchor.exe``), whose frontend is a dev server that is never up at
boot -- so every window was a browser error page -- and nothing on the machine could say
so. Version alone cannot tell two builds apart (every rebuild is "0.2.0").

The judgement on the `Run` entry is a pure function, so its three failure shapes can be
pinned here without a registry. The rows that DO read the machine are exercised for the
one property every doctor row must have: never green over an unknown.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.commands import doctor

INSTALLED = Path(r"C:\Users\me\AppData\Local\NAVIG Anchor\navig-anchor.exe")


def test_a_dev_binary_in_the_run_key_is_the_original_outage_and_says_so() -> None:
    row = doctor._judge_anchor_login(r"E:\repo\target\debug\navig-anchor.exe ", INSTALLED)
    assert row[0] == doctor._WARN and row[1] is False
    assert "NOT the installed one" in row.detail
    assert "development build" in row.detail
    assert "can't reach this page" in row.detail  # the symptom the user actually saw


def test_some_other_binary_is_flagged_without_the_dev_hint() -> None:
    row = doctor._judge_anchor_login(r'"D:\old\NAVIG Anchor\navig-anchor.exe"', INSTALLED)
    assert row[1] is False and "NOT the installed one" in row.detail
    assert "development build" not in row.detail


def test_the_right_binary_unquoted_is_a_warn_with_the_reason() -> None:
    row = doctor._judge_anchor_login(str(INSTALLED), INSTALLED)
    assert row[0] == doctor._WARN
    assert "unquoted" in row.detail and "Program.exe" in row.detail


def test_the_right_binary_quoted_is_green() -> None:
    row = doctor._judge_anchor_login(f'"{INSTALLED}"', INSTALLED)
    assert row[0] == doctor._OK and row[1] is True
    assert "quoted" in row.detail


def test_a_longer_path_that_merely_starts_with_the_installed_one_is_not_a_match() -> None:
    row = doctor._judge_anchor_login(f'"{INSTALLED}.bak"', INSTALLED)
    assert row[1] is False and "NOT the installed one" in row.detail


def test_case_is_ignored_because_windows_paths_are() -> None:
    row = doctor._judge_anchor_login(f'"{str(INSTALLED).upper()}"', INSTALLED)
    assert row[1] is True


def test_running_from_the_installed_binary_is_green(tmp_path: Path) -> None:
    exe = tmp_path / "navig-anchor.exe"
    exe.write_bytes(b"MZ")
    row = doctor._judge_anchor_running([str(exe)], exe)
    assert row[0] == doctor._OK and row[1] is True
    assert "installed binary: 1" in row.detail


def test_a_dev_copy_running_alongside_the_installed_one_is_named_not_hidden(tmp_path: Path) -> None:
    exe = tmp_path / "navig-anchor.exe"
    exe.write_bytes(b"MZ")
    dev = r"E:\repo\target\debug\navig-anchor.exe"
    row = doctor._judge_anchor_running([str(exe), dev], exe)
    assert row[1] is True and "ALSO running" in row.detail and dev in row.detail


def test_only_a_different_binary_running_is_a_warn_naming_it(tmp_path: Path) -> None:
    exe = tmp_path / "navig-anchor.exe"
    dev = r"E:\repo\target\debug\navig-anchor.exe"
    row = doctor._judge_anchor_running([dev], exe)
    assert row[0] == doctor._WARN and "DIFFERENT" in row.detail and dev in row.detail


def test_an_unreadable_process_path_is_not_checked_never_an_accusation(tmp_path: Path) -> None:
    """psutil returns exe=None for an elevated Anchor: that is an unknown, not 'a different one'."""
    exe = tmp_path / "navig-anchor.exe"
    row = doctor._judge_anchor_running([None], exe)
    assert row[0] == doctor._WARN and row[1] is False
    assert "not checked" in row.detail and "could not be read" in row.detail
    assert "DIFFERENT" not in row.detail


def test_nothing_running_is_green_with_the_launch_hint(tmp_path: Path) -> None:
    row = doctor._judge_anchor_running([], tmp_path / "navig-anchor.exe")
    assert row[1] is True and "launch it" in row.detail


@pytest.mark.skipif(doctor.sys.platform != "win32", reason="the rows are Windows-only by design")
def test_the_section_never_reports_green_over_an_unknown(monkeypatch, tmp_path: Path) -> None:
    """The one contract every doctor row must keep: could-not-verify is a warn, not a tick.

    The scenario is FORCED, not found: a known installed Anchor under a private
    LOCALAPPDATA (so every machine-reading row is emitted), and psutil taken away (the
    one dependency the "running" row cannot verify without). Then the exact row is
    asserted -- a loop with a filter would pass on a machine with no Anchor at all.
    """
    fake = tmp_path / "NAVIG Anchor" / "navig-anchor.exe"
    fake.parent.mkdir()
    fake.write_bytes(b"MZ")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    import builtins

    real_import = builtins.__import__

    def no_psutil(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("simulated")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", no_psutil)
    rows = doctor.check_desktop_apps()
    by_label = {row.label: row for row in rows}

    installed = by_label["Anchor installed"]
    assert installed[1] is True and str(fake) in installed.detail and "built" in installed.detail

    running = by_label["Anchor running"]
    assert running[0] == doctor._WARN and running[1] is False, running[2]
    assert "not checked" in running.detail and "psutil" in running.detail


@pytest.mark.skipif(doctor.sys.platform != "win32", reason="the rows are Windows-only by design")
def test_no_anchor_on_the_machine_is_one_green_row_not_silence(monkeypatch, tmp_path: Path) -> None:
    """Not installed is a fact worth one line (it is optional), never a missing section."""
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "la"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "pf"))
    rows = doctor.check_desktop_apps()
    assert len(rows) == 1
    assert rows[0][1] is True and "not installed" in rows[0].detail


@pytest.mark.skipif(doctor.sys.platform == "win32", reason="elsewhere the section must be silent")
def test_the_section_is_silent_off_windows() -> None:
    assert doctor.check_desktop_apps() == []
