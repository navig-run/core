"""A hand-typed ``pytest -n auto`` respects the machine's heavy-job slots.

The machine-wide protocol is scripts/heavy-slot-lib.mjs: slot files under
~/.navig/slots/heavy and a per-lane CPU share exported as NAVIG_HEAVY_CPUS. The gate
takes a slot; a manual ``pytest -n auto`` did not and claimed ~20 workers on top of the
gates (2026-10-08: a dozen xdist workers crashed, the run died with no summary). The root
``conftest.py`` reads that protocol — it never takes a slot or writes a file.
"""

from __future__ import annotations

import importlib.util
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT_CONFTEST = Path(__file__).resolve().parents[2] / "conftest.py"


@pytest.fixture(scope="module")
def rc():
    spec = importlib.util.spec_from_file_location("_navig_root_conftest_slots", _ROOT_CONFTEST)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k in ("NAVIG_HEAVY_CPUS", "NAVIG_HEAVY_SLOTS", "NAVIG_HEAVY_OVERFLOW", "CI"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NAVIG_SLOT_DIR", str(tmp_path))
    return tmp_path


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _slot(d: Path, name: str, pid: int, started: datetime | None = None) -> None:
    started = started or datetime.now(timezone.utc)
    (d / name).write_text(
        json.dumps({"pid": pid, "repo": "t", "label": "t", "started": _iso(started)}), encoding="utf-8"
    )


def test_inside_a_slot_holder_the_exported_share_wins(rc, env, monkeypatch):
    monkeypatch.setenv("NAVIG_HEAVY_CPUS", "6")
    _slot(env, "slot-0.json", os.getppid())
    assert rc.pytest_xdist_auto_num_workers(SimpleNamespace()) == 6


def test_idle_machine_keeps_xdist_default(rc, env):
    assert rc.pytest_xdist_auto_num_workers(SimpleNamespace()) is None


def test_busy_slots_take_the_lane_share(rc, env, monkeypatch):
    _slot(env, "slot-0.json", os.getppid())
    monkeypatch.setattr(rc.os, "cpu_count", lambda: 32)
    # cpuShare: cores // (slots 3 + overflow 1), min 2
    assert rc.pytest_xdist_auto_num_workers(SimpleNamespace()) == 8
    monkeypatch.setenv("NAVIG_HEAVY_SLOTS", "7")
    assert rc.pytest_xdist_auto_num_workers(SimpleNamespace()) == 4


def test_dead_stale_and_garbage_slots_count_as_free(rc, env):
    dead = 4_000_000
    _slot(env, "slot-0.json", dead)
    _slot(env, "slot-1.json", os.getppid(), datetime.now(timezone.utc) - timedelta(hours=4))
    (env / "slot-2.json").write_text("{not json", encoding="utf-8")
    (env / "overflow-0.json").write_text(json.dumps({"pid": os.getppid()}), encoding="utf-8")
    (env / "unrelated.json").write_text(json.dumps({"pid": os.getppid(), "started": _iso(datetime.now())}))
    assert rc._busy_heavy_slots(env) == 0
    _slot(env, "overflow-0.json", os.getppid())
    assert rc._busy_heavy_slots(env) == 1


@pytest.mark.parametrize("off", [("CI", "true"), ("NAVIG_HEAVY_SLOTS", "off"), ("NAVIG_HEAVY_SLOTS", "0")])
def test_disabled_like_the_protocol(rc, env, monkeypatch, off):
    _slot(env, "slot-0.json", os.getppid())
    monkeypatch.setenv(*off)
    assert rc.pytest_xdist_auto_num_workers(SimpleNamespace()) is None


def test_never_writes_into_the_slot_dir(rc, env):
    _slot(env, "slot-0.json", os.getppid())
    before = sorted(p.name for p in env.iterdir())
    rc.pytest_xdist_auto_num_workers(SimpleNamespace())
    assert sorted(p.name for p in env.iterdir()) == before
