"""Behavioural cover for `navig host monitor` — the module that had none.

`commands/monitoring.py` is live code (`navig host monitor`, and
`host monitor show --resources|--disk|--services|--network` route into it via `host.py`) but
carried **no tests at all**. That is how sixty `console.console.print(...)` calls — every one an
`AttributeError` the moment it ran — sat in it undetected until a single sibling occurrence in
the AHK evolver turned one unrelated test red (#723).

A guard now forbids `console.console` tree-wide, but a guard only stops that ONE mistake. These
tests execute the real code paths end to end, so the next never-run defect fails here instead of
in front of a user.

The remote boundary is an autospec of ``RemoteOperations`` on purpose: a plain MagicMock invents
any attribute, so a call to a method that does not exist would pass silently — the exact way a
lying fake hid dead code before (#706).
"""

from __future__ import annotations

import subprocess
from unittest.mock import MagicMock, create_autospec

import pytest

from navig.commands import monitoring as mon
from navig.remote import RemoteOperations

# One SSH round-trip returns every metric in this delimited form (see `_BATCH`).
_BATCH_OK = """===CPU===
12.5
===MEM===
41.0 3300 8000
===DISK===
55 20G 40G 18G
===LOAD===
0.10, 0.20, 0.30
===CONN===
42
===UPTIME===
up 3 days
"""

_BATCH_HOT = _BATCH_OK.replace("12.5", "97.5")  # CPU over the 80% alert line


def _completed(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args="ssh", returncode=returncode, stdout=stdout, stderr="")


@pytest.fixture
def remote() -> MagicMock:
    """An autospec'd RemoteOperations — a wrong method name must fail, not be invented."""
    fake = create_autospec(RemoteOperations, instance=True)
    fake.execute_command.return_value = _completed(_BATCH_OK)
    return fake


@pytest.fixture(autouse=True)
def _wire_boundaries(monkeypatch, remote):
    """Point the module at a fake server + fake SSH, leaving all rendering real."""
    cfg = MagicMock()
    cfg.load_server_config.return_value = {"host": "example.com", "type": "ssh"}
    monkeypatch.setattr(mon, "get_config_manager", lambda: cfg)
    monkeypatch.setattr(mon, "require_active_server", lambda *_a, **_k: "web-1")
    monkeypatch.setattr(mon, "RemoteOperations", lambda *_a, **_k: remote)
    monkeypatch.setattr(mon, "is_local_host", lambda *_a, **_k: False)  # exercise the SSH path


# ── pure helpers ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("value", [95.0, 85.0])
def test_traffic_light_flags_high_values(value: float) -> None:
    assert mon._traffic_light(value) != mon._traffic_light(10.0)


def test_traffic_light_bands_are_distinct() -> None:
    high, med, ok = mon._traffic_light(95.0), mon._traffic_light(70.0), mon._traffic_light(10.0)
    assert high != med != ok and high != ok


def test_disk_status_respects_the_threshold() -> None:
    assert mon._disk_status(50.0, 80) != mon._disk_status(95.0, 80)


def test_health_icon_distinguishes_states() -> None:
    assert mon._health_icon("healthy") != mon._health_icon("unhealthy")


# ── monitor_resources ────────────────────────────────────────────────────────


def test_dry_run_reports_intent_and_touches_no_ssh(capsys, remote) -> None:
    mon.monitor_resources({"dry_run": True})
    assert "DRY RUN" in capsys.readouterr().out
    remote.execute_command.assert_not_called()


def test_resources_renders_the_metrics_it_parsed(capsys) -> None:
    """The whole render path runs — this is what 60 AttributeErrors used to break."""
    mon.monitor_resources({})
    out = capsys.readouterr().out
    assert "web-1" in out
    assert "CPU" in out.upper()
    assert "normal range" in out  # the no-alert verdict


def test_resources_raises_an_alert_on_high_cpu(capsys, remote) -> None:
    remote.execute_command.return_value = _completed(_BATCH_HOT)
    mon.monitor_resources({})
    out = capsys.readouterr().out
    assert "Alert" in out
    assert "normal range" not in out


def test_resources_survives_a_failed_ssh_call(capsys, remote) -> None:
    """A non-zero return must not crash the command."""
    remote.execute_command.return_value = _completed("", returncode=255)
    mon.monitor_resources({})
    assert capsys.readouterr().out  # it still reported something


def test_resources_survives_malformed_metrics(capsys, remote) -> None:
    """Garbage in a section is skipped, not fatal (the parser's own contract)."""
    remote.execute_command.return_value = _completed("===CPU===\nnot-a-number\n===MEM===\nx\n")
    mon.monitor_resources({})
    assert capsys.readouterr().out


# ── monitor_disk ─────────────────────────────────────────────────────────────

_DF = "/dev/sda1 40G 20G 18G 55% /\n/dev/sdb1 100G 95G 5G 95% /data\n"


def test_disk_flags_a_filesystem_over_the_threshold(capsys, remote) -> None:
    remote.execute_command.return_value = _completed(_DF)
    mon.monitor_disk(80, {})
    out = capsys.readouterr().out
    assert "Alert" in out  # /data at 95% is over 80


def test_disk_reports_all_clear_under_the_threshold(capsys, remote) -> None:
    remote.execute_command.return_value = _completed("/dev/sda1 40G 20G 18G 55% /\n")
    mon.monitor_disk(80, {})
    assert "below" in capsys.readouterr().out  # "All disks below 80% threshold"


def test_disk_reports_a_failed_lookup(capsys, remote) -> None:
    remote.execute_command.return_value = _completed("", returncode=1)
    mon.monitor_disk(80, {})
    assert "Failed" in capsys.readouterr().out


# ── the remaining verbs simply have to RUN ───────────────────────────────────


@pytest.mark.parametrize(
    "call",
    [
        pytest.param(lambda: mon.monitor_services({}), id="services"),
        pytest.param(lambda: mon.monitor_network({}), id="network"),
        pytest.param(lambda: mon.health_check({}), id="health"),
        pytest.param(lambda: mon.view_service_logs("nginx", False, 50, {}), id="logs"),
    ],
)
def test_command_paths_execute_and_print(call, capsys) -> None:
    """Each of these renders through `console` — the surface that was entirely dead."""
    call()
    assert capsys.readouterr().out


def test_restart_remote_service_reports_its_outcome(capsys, remote) -> None:
    remote.execute_command.return_value = _completed("", returncode=0)
    mon.restart_remote_service("nginx", {})
    assert capsys.readouterr().out


# ── the local-Windows fast paths (no SSH) ────────────────────────────────────
# These are what the operator hits on their own machine, and they carried the same dead
# `console.console.print` calls. They are skipped entirely by the SSH tests above, which force
# `is_local_host` False — so without these the busiest real-world path stayed uncovered.


def _disk(mountpoint: str, percent: int) -> dict:
    """The shape `commands.monitor.get_disk_info()` yields."""
    return {
        "mountpoint": mountpoint,
        "percent": percent,
        "total_gb": 500,
        "used_gb": int(500 * percent / 100),
        "free_gb": 500 - int(500 * percent / 100),
    }


def test_local_disk_path_renders_and_flags_a_full_drive(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        "navig.commands.monitor.get_disk_info", lambda: [_disk("C:", 55), _disk("D:", 95)]
    )
    mon._monitor_disk_local_windows("this-pc", 80, {})
    out = capsys.readouterr().out
    assert "Alert" in out and "D:" in out


def test_local_disk_path_reports_all_clear(monkeypatch, capsys) -> None:
    monkeypatch.setattr("navig.commands.monitor.get_disk_info", lambda: [_disk("C:", 20)])
    mon._monitor_disk_local_windows("this-pc", 80, {})
    assert "Alert" not in capsys.readouterr().out


def test_local_disk_path_emits_parseable_json(monkeypatch, capsys) -> None:
    """`--json` must print ONE machine-readable document, not prose."""
    import json as _json

    monkeypatch.setattr("navig.commands.monitor.get_disk_info", lambda: [_disk("C:", 91)])
    mon._monitor_disk_local_windows("this-pc", 80, {"json_output": True})
    payload = _json.loads(capsys.readouterr().out)
    assert payload["server"] == "this-pc"
    assert payload["threshold"] == 80
    assert payload["alerts"]


def test_local_resources_path_runs_and_alerts(monkeypatch, capsys) -> None:
    """psutil is stubbed so the render path executes on any machine."""
    fake_psutil = MagicMock()
    fake_psutil.cpu_percent.return_value = 97.0  # over the 80% alert line
    fake_psutil.virtual_memory.return_value = MagicMock(
        percent=45.0, used=4 * 1024**3, total=16 * 1024**3
    )
    # Real numbers, not MagicMocks: these reach `format_bytes`, which does arithmetic.
    fake_psutil.disk_usage.return_value = MagicMock(
        percent=55.0, used=200 * 1024**3, total=500 * 1024**3, free=300 * 1024**3
    )
    monkeypatch.setitem(__import__("sys").modules, "psutil", fake_psutil)

    mon._monitor_resources_local_windows("this-pc", {})
    out = capsys.readouterr().out
    assert out
    assert "Alert" in out or "97" in out
