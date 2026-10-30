"""`navig monitoring` speaks the log dialect of the host it is pointed at.

The defect these pin: `view_service_logs` built `journalctl -u <service>` for every
target, so a macOS or Windows host answered "command not found" instead of its logs —
and `restart_remote_service` ran `sudo systemctl restart` on hosts that have no systemd.
"""

from __future__ import annotations

import subprocess

import pytest

from navig import remote as remote_mod
from navig.commands.monitoring import service_log_command, service_restart_command


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    remote_mod._TARGET_OS_CACHE.clear()
    remote_mod._WIN_PROBE_CACHE.clear()
    yield
    remote_mod._TARGET_OS_CACHE.clear()
    remote_mod._WIN_PROBE_CACHE.clear()


# ─── the command tables ───────────────────────────────────────────────────────


def test_linux_reads_the_journal_and_follows_when_asked():
    assert service_log_command("navig-agent", 50, follow=False, platform="linux") == (
        "journalctl -u navig-agent -n 50 --no-pager"
    )
    assert service_log_command("navig-agent", 50, follow=True, platform="linux") == (
        "journalctl -u navig-agent -n 50 -f --no-pager"
    )


def test_macos_reads_the_unified_log_over_a_bounded_window():
    cmd = service_log_command("run.navig.daemon", 20, follow=False, platform="macos")
    assert cmd is not None
    assert cmd.startswith("log show --last 1h")
    assert '--predicate \'process == "run.navig.daemon"\'' in cmd
    assert cmd.endswith("| tail -n 20")


def test_windows_reads_the_event_log_through_powershell():
    cmd = service_log_command("NavigAgent", 10, follow=False, platform="windows")
    assert cmd is not None
    assert "Get-WinEvent -LogName System" in cmd
    assert "$_.ProviderName -like '*NavigAgent*'" in cmd
    assert "-First 10" in cmd


def test_an_unrecognized_host_gets_no_command_at_all():
    assert service_log_command("navig-agent", 10, follow=False, platform="unknown") is None


def test_restart_uses_each_hosts_own_init():
    assert service_restart_command("navig-agent", platform="linux") == (
        "sudo systemctl restart navig-agent"
    )
    assert service_restart_command("run.navig.daemon", platform="macos") == (
        "launchctl kickstart -k gui/$(id -u)/run.navig.daemon"
    )
    assert service_restart_command("NavigAgent", platform="windows") == (
        "powershell -NoProfile -Command \"Restart-Service -Name 'NavigAgent' -Force\""
    )
    assert service_restart_command("navig-agent", platform="unknown") is None


# ─── target_platform probing ──────────────────────────────────────────────────

_NAMES = {
    "Darwin": "macos",
    "Linux": "linux",
    "FreeBSD": "unknown",
}


class _Probe(remote_mod.RemoteOperations):
    """A RemoteOperations whose SSH round trips answer from a table."""

    def __init__(self, answers: dict[str, tuple[int, str]]):
        self.answers = answers
        self.calls: list[str] = []

    def execute_command(self, command, server_config, **kwargs):  # noqa: ANN001, ANN003
        self.calls.append(command)
        rc, out = self.answers.get(command, (127, "command not found"))
        return subprocess.CompletedProcess(args=command, returncode=rc, stdout=out, stderr="")


def _config(host: str = "10.0.0.5", **extra):
    return {"host": host, "user": "operator", "port": 22, **extra}


@pytest.mark.parametrize(("kernel", "expected"), list(_NAMES.items()))
def test_uname_s_decides_the_posix_family(kernel, expected):
    probe = _Probe({"uname -s": (0, f"{kernel}\n")})
    assert probe.target_platform(_config()) == expected


def test_a_windows_host_is_named_after_the_ver_probe():
    probe = _Probe({"ver": (0, "\r\nMicrosoft Windows [Version 10.0.22631.4317]\r\n")})
    assert probe.target_platform(_config()) == "windows"


def test_an_unreachable_host_is_unknown_not_linux():
    probe = _Probe({})
    assert probe.target_platform(_config()) == "unknown"


def test_the_probe_runs_once_per_host():
    probe = _Probe({"uname -s": (0, "Linux\n")})
    config = _config()
    assert probe.target_platform(config) == "linux"
    assert probe.target_platform(config) == "linux"
    assert probe.calls == ["uname -s"]


def test_a_local_host_is_answered_without_any_round_trip(monkeypatch):
    probe = _Probe({})
    monkeypatch.setattr(
        "navig.platform.capabilities.os_name", lambda: "macos", raising=True
    )
    assert probe.target_platform(_config(host="127.0.0.1")) == "macos"
    assert probe.calls == []
