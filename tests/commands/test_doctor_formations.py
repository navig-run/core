"""Regression: the doctor Formations row counts what the loader discovers.

`check_formations` counted `*.yaml` under `config_dir()/formations`, but formations
are `formation.json` DIRS and installs land under `store_dir()/formations` (#373) —
so it read the wrong dir + wrong file type and effectively always showed 0. It now
uses `discover_formations()` (the loader's real discovery), and 0 discovered is a ⚠
(builtin store likely missing), not a green tick.

CheckResult behaves as the historical (icon, ok, line) 3-tuple.
"""

from __future__ import annotations

from pathlib import Path

import navig.formations.loader as loader_mod
from navig.commands.doctor import check_formations


def test_formations_counts_what_loader_discovers(monkeypatch):
    monkeypatch.setattr(
        loader_mod, "discover_formations", lambda: {"council": Path("x"), "duo": Path("y")}
    )
    rows = check_formations()
    assert len(rows) == 1
    _icon, ok, line = rows[0][0], rows[0][1], rows[0][2]
    assert ok is True
    assert "Formations" in line
    assert "2 discovered" in line


def test_formations_zero_is_a_warn_not_green(monkeypatch):
    monkeypatch.setattr(loader_mod, "discover_formations", lambda: {})
    row = check_formations()[0]
    assert row[1] is False  # 0 discovered ⇒ ⚠, not ✓ (doctor honesty rule)
    assert "0 discovered" in row[2]


def test_formations_discovery_failure_is_reported(monkeypatch):
    def _boom():
        raise RuntimeError("boom")

    monkeypatch.setattr(loader_mod, "discover_formations", _boom)
    row = check_formations()[0]
    assert row[1] is False
    assert "discovery failed" in row[2]
