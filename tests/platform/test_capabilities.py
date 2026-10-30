"""The platform capability seam answers the same questions the same way on every OS.

Each test pins the identity explicitly (`os_name="windows"` etc.) instead of patching
`sys.platform`, because the seam takes the answer as an argument — and because patching a
global platform flag is what made `pathlib` build a `WindowsPath` on Linux in the first
place (see `tests/cli/test_doctor_path_health.py`).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from navig.platform import capabilities


@pytest.mark.parametrize(
    ("os_name", "suffix", "launcher", "shell"),
    [
        ("windows", ".exe", "navig.cmd", "cmd"),
        ("macos", "", "navig", "/bin/sh"),
        ("linux", "", "navig", "/bin/sh"),
        ("wsl", "", "navig", "/bin/sh"),
    ],
)
def test_identity_shapes_the_executable_and_shell(
    os_name: str, suffix: str, launcher: str, shell: str
) -> None:
    assert capabilities.executable_suffix(os_name) == suffix
    assert capabilities.launcher_name(os_name) == launcher
    assert capabilities.shell_name(os_name) == shell


def test_posix_is_everything_that_is_not_windows() -> None:
    assert capabilities.is_posix("linux") is True
    assert capabilities.is_posix("macos") is True
    assert capabilities.is_posix("wsl") is True
    assert capabilities.is_posix("windows") is False


def test_the_shell_argv_runs_a_command_string_without_re_quoting() -> None:
    assert capabilities.shell_argv("navig doctor", "windows") == [
        "cmd",
        "/d",
        "/s",
        "/c",
        "navig doctor",
    ]
    # `-l` on purpose: NAVIG's documented commands rely on the login shell's PATH.
    assert capabilities.shell_argv("navig doctor", "linux") == [
        "/bin/sh",
        "-lc",
        "navig doctor",
    ]


@pytest.mark.parametrize(
    ("feature", "allowed", "denied"),
    [
        ("ahk", "windows", "macos"),
        ("registry", "windows", "linux"),
        ("powershell", "windows", "macos"),
        ("launchd", "macos", "windows"),
        ("osascript", "macos", "linux"),
        ("systemd", "linux", "macos"),
        ("systemd", "wsl", "windows"),
        ("notify-send", "linux", "windows"),
    ],
)
def test_supports_names_the_feature_instead_of_the_os(
    feature: str, allowed: str, denied: str
) -> None:
    assert capabilities.supports(feature, allowed) is True
    assert capabilities.supports(feature, denied) is False


def test_the_shell_is_everywhere() -> None:
    for os_name in ("windows", "macos", "linux", "wsl"):
        assert capabilities.supports("shell", os_name) is True


def test_a_typo_in_a_guard_raises_instead_of_disabling_a_feature() -> None:
    with pytest.raises(ValueError, match="unknown capability 'not-a-feature'"):
        capabilities.supports("not-a-feature")
    assert "ahk" in capabilities.known_features()


@pytest.mark.parametrize(
    ("os_name", "expected"),
    [
        ("windows", "schtasks"),
        ("macos", "launchd"),
        ("linux", "systemd"),
        ("wsl", "systemd"),
    ],
)
def test_a_pinned_identity_answers_its_own_supervisor(os_name: str, expected: str) -> None:
    # Pinning is a matrix question, never a live probe: no host is asked for its units.
    assert capabilities.service_manager(os_name) == expected


def test_the_launcher_lives_in_one_directory_on_every_os() -> None:
    expected = Path.home() / ".local" / "bin"
    assert capabilities.user_bin_dir() == expected


@pytest.mark.parametrize(
    ("os_name", "bin_dir", "interpreter"),
    [
        ("windows", "Scripts", "python.exe"),
        ("linux", "bin", "python"),
        ("macos", "bin", "python"),
    ],
)
def test_a_venv_is_spelled_for_this_os(
    tmp_path, os_name: str, bin_dir: str, interpreter: str
) -> None:
    root = tmp_path / "venv"
    assert capabilities.venv_bin_dir(root, os_name) == root / bin_dir
    assert capabilities.python_in_venv(root, os_name) == root / bin_dir / interpreter


def test_macos_notifies_through_osascript_and_escapes_the_string() -> None:
    argv = capabilities.notify_argv("NAVIG", 'say "hi"', "macos")
    assert argv is not None and argv[0] == "osascript" and argv[1] == "-e"
    assert '\\"hi\\"' in argv[2]


def test_windows_has_no_subprocess_notifier_on_purpose() -> None:
    # Windows toasts live in navig.notify.toast (WinRT); an argv here would be a second path.
    assert capabilities.notify_argv("NAVIG", "hi", "windows") is None


def test_linux_notifies_only_when_notify_send_is_installed(monkeypatch) -> None:
    monkeypatch.setattr(capabilities, "_which", lambda *names: "/usr/bin/notify-send")
    assert capabilities.notify_argv("NAVIG", "hi", "linux") == ["/usr/bin/notify-send", "NAVIG", "hi"]
    monkeypatch.setattr(capabilities, "_which", lambda *names: None)
    assert capabilities.notify_argv("NAVIG", "hi", "linux") is None


def test_the_clipboard_argv_matches_the_platform_tool(monkeypatch) -> None:
    assert capabilities.clipboard_argv(read=True, explicit="windows") is None
    monkeypatch.setattr(capabilities, "_which", lambda *names: "/usr/bin/pbpaste")
    assert capabilities.clipboard_argv(read=True, explicit="macos") == ["/usr/bin/pbpaste"]
    monkeypatch.setattr(capabilities, "_which", lambda *names: "/usr/bin/xclip")
    assert capabilities.clipboard_argv(read=False, explicit="linux") == [
        "/usr/bin/xclip",
        "-selection",
        "clipboard",
    ]
    monkeypatch.setattr(capabilities, "_which", lambda *names: None)
    assert capabilities.clipboard_argv(read=True, explicit="linux") is None


def test_describe_is_a_bug_report_snapshot() -> None:
    snap = capabilities.describe("macos")
    assert snap["os"] == "macos"
    assert snap["posix"] is True
    assert snap["launcher"] == "navig"
    assert snap["shell"] == "/bin/sh"
    assert snap["service_manager"] == "launchd"
    assert "launchd" in snap["supports"]
    assert "ahk" not in snap["supports"]
    assert snap["python"] == sys.version.split()[0]
