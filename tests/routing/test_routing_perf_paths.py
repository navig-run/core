"""
tests/routing/test_routing_perf_paths.py

Verify that the router-trace log (navig.llm.routing.trace) and the perf
sample dir (navig.perf.profiler) resolve their paths at CALL time under
NAVIG_CONFIG_DIR — never as import-time module constants.

History: these tests originally asserted module-level ``TRACE_LOG_PATH`` /
``PERF_DIR`` constants. PR #189 (the frozen-path sweep, 46436b4b) converted
both to call-time resolvers — a module constant freezes the operator's REAL
home before test/daemon isolation applies (incident #179), and
tests/core/test_frozen_path_tripwire.py now bans the constant shape
outright. The contract asserted here is the CURRENT one:

- ``trace._trace_log_path()`` / ``profiler._perf_dir()`` honour
  NAVIG_CONFIG_DIR set AFTER import (no reload needed — that is the point);
- with no override they resolve under ``paths.config_dir()``;
- ``trace.TRACE_LOG_PATH`` survives only as a ``None`` monkeypatch seam.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# navig.llm.routing.trace
# ---------------------------------------------------------------------------

def test_trace_log_path_resolves_env_at_call_time(tmp_path, monkeypatch):
    """NAVIG_CONFIG_DIR set AFTER import must apply — no reload, no constant."""
    import navig.llm.routing.trace as trace_mod

    custom = tmp_path / "cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))

    assert trace_mod._trace_log_path() == custom / "logs" / "router_traces.jsonl"


def test_trace_log_path_default_is_config_dir(monkeypatch):
    """With no override the trace log lives under paths.config_dir()."""
    monkeypatch.delenv("NAVIG_CONFIG_DIR", raising=False)

    import navig.llm.routing.trace as trace_mod
    from navig.platform import paths

    assert trace_mod._trace_log_path() == paths.config_dir() / "logs" / "router_traces.jsonl"


def test_trace_log_path_seam_is_none_and_wins_when_set(tmp_path, monkeypatch):
    """TRACE_LOG_PATH is a None sentinel (the tripwire-approved seam); a
    monkeypatched value must override env resolution entirely."""
    import navig.llm.routing.trace as trace_mod

    assert trace_mod.TRACE_LOG_PATH is None  # never a frozen constant

    seam = tmp_path / "seam" / "traces.jsonl"
    monkeypatch.setattr(trace_mod, "TRACE_LOG_PATH", seam)
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "ignored"))

    assert trace_mod._trace_log_path() == seam


def test_log_trace_writes_inside_isolated_config_dir(tmp_path, monkeypatch):
    """End-to-end: a trace logged under isolation lands in the isolated dir,
    not the real home (the #179 failure mode)."""
    import navig.llm.routing.trace as trace_mod

    custom = tmp_path / "cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))

    trace_mod.log_trace(trace_mod.RouteTrace(trace_id="t-1", provider="test"))

    log_file = custom / "logs" / "router_traces.jsonl"
    assert log_file.exists()
    entry = json.loads(log_file.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert entry["trace_id"] == "t-1"


# ---------------------------------------------------------------------------
# navig.perf.profiler
# ---------------------------------------------------------------------------

def test_perf_dir_resolves_env_at_call_time(tmp_path, monkeypatch):
    """NAVIG_CONFIG_DIR set AFTER import must apply to the perf dir."""
    import navig.perf.profiler as profiler_mod

    custom = tmp_path / "cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))

    assert profiler_mod._perf_dir() == custom / "perf"


def test_perf_dir_default_is_config_dir(monkeypatch):
    """With no override the perf dir lives under paths.config_dir()."""
    monkeypatch.delenv("NAVIG_CONFIG_DIR", raising=False)

    import navig.perf.profiler as profiler_mod
    from navig.platform import paths

    assert profiler_mod._perf_dir() == paths.config_dir() / "perf"


def test_profiler_has_no_frozen_perf_dir_constant():
    """PERF_DIR must stay gone — a module constant is the #179/#189 bug shape
    (tests/core/test_frozen_path_tripwire.py bans it tree-wide)."""
    import navig.perf.profiler as profiler_mod

    assert not hasattr(profiler_mod, "PERF_DIR")
