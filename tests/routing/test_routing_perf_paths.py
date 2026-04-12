"""
tests/test_routing_perf_paths.py

Verify that TRACE_LOG_PATH (navig.routing.trace) and PERF_DIR
(navig.perf.profiler) respect NAVIG_CONFIG_DIR so that neither
module bakes ~/.navig into its compiled constant.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_trace_log_path_respects_env(tmp_path, monkeypatch):
    """TRACE_LOG_PATH must sit inside NAVIG_CONFIG_DIR when the env var is set."""
    custom = tmp_path / "cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))

    import navig.routing.trace as trace_mod

    importlib.reload(trace_mod)

    assert trace_mod.TRACE_LOG_PATH == custom / "logs" / "router_traces.jsonl"


def test_trace_log_path_not_home_raw(monkeypatch):
    """With no override, TRACE_LOG_PATH must equal paths.config_dir()/logs/... (not a raw Path.home())."""
    monkeypatch.delenv("NAVIG_CONFIG_DIR", raising=False)

    import navig.routing.trace as trace_mod

    importlib.reload(trace_mod)

    from navig.platform import paths

    assert trace_mod.TRACE_LOG_PATH == paths.config_dir() / "logs" / "router_traces.jsonl"


def test_perf_dir_respects_env(tmp_path, monkeypatch):
    """PERF_DIR must sit inside NAVIG_CONFIG_DIR when the env var is set."""
    custom = tmp_path / "cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))

    import navig.perf.profiler as profiler_mod

    importlib.reload(profiler_mod)

    assert profiler_mod.PERF_DIR == custom / "perf"


def test_perf_dir_not_home_raw(monkeypatch):
    """With no override, PERF_DIR must equal paths.config_dir()/perf (not a raw Path.home())."""
    monkeypatch.delenv("NAVIG_CONFIG_DIR", raising=False)

    import navig.perf.profiler as profiler_mod

    importlib.reload(profiler_mod)

    from navig.platform import paths

    assert profiler_mod.PERF_DIR == paths.config_dir() / "perf"
