"""`navig desktop crash-chrome` reports the RAM it actually freed.

The headline is a measurement — available system RAM after the kill minus
before — never an estimate, and never a confident "0" when RAM could not be read.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

from navig.commands import desktop, processes

GB = 1024**3


@pytest.fixture
def crash(monkeypatch):
    """Run crash-chrome with fake renderers and a scripted RAM reading sequence."""

    def _run(ram_reads, *args):
        reads = iter(ram_reads)
        monkeypatch.setattr(desktop.sys, "platform", "win32")
        monkeypatch.setattr(desktop.time, "sleep", lambda _s: None)
        monkeypatch.setattr(
            processes,
            # Mirror the real signature (processes.py): the automation-browsers
            # feature passes include_automation through (2026-10-13) — a double
            # that drops it TypeErrors every test in this file.
            "find_chrome_renderers",
            lambda include_extensions=False, include_automation=False: [{"pid": 1}, {"pid": 2}],
        )
        monkeypatch.setattr(
            processes,
            "kill_processes",
            lambda procs, **_kw: {"killed": list(procs), "failed": [], "skipped": []},
        )
        monkeypatch.setattr(processes, "available_ram_bytes", lambda: next(reads))
        return CliRunner().invoke(desktop.desktop_app, ["crash-chrome", "-y", *args])

    return _run


def test_json_reports_measured_freed_bytes(crash):
    res = crash([10 * GB, 11 * GB + GB // 2], "--json")
    assert res.exit_code == 0, res.output
    doc = json.loads(res.output)
    assert doc["crashed"] == 2
    assert doc["freed_bytes"] == GB + GB // 2
    assert doc["ram_available_before"] == 10 * GB
    assert doc["ram_available_after"] == 11 * GB + GB // 2


def test_human_line_leads_with_freed_amount(crash):
    res = crash([10 * GB, 11 * GB + GB // 2])
    assert res.exit_code == 0, res.output
    assert "Freed ~1.5 GB" in res.output


def test_other_apps_allocating_never_reports_negative(crash):
    res = crash([10 * GB, 9 * GB], "--json")
    assert json.loads(res.output)["freed_bytes"] == 0


def test_unreadable_ram_is_unknown_not_zero(crash):
    res = crash([None, 11 * GB], "--json")
    assert json.loads(res.output)["freed_bytes"] is None
    human = crash([None, 11 * GB])
    assert "Freed" not in human.output


@pytest.mark.parametrize(
    ("n", "text"),
    [(GB + GB // 2, "1.5 GB"), (640 * 1024**2, "640 MB"), (0, "0 MB")],
)
def test_format_bytes_short(n, text):
    assert desktop.format_bytes_short(n) == text
