"""Wave 10 path-centralisation tests.

Covers:
  - navig.config.ConfigManager.global_config_dir
  - navig.cache_store.global_cache_dir()
  - navig.ai_context.AIContextManager.config_dir
  - navig.daemon.supervisor path resolvers (call-time — no reload needed)
  - navig.daemon.entry path resolvers      (call-time — no reload needed)
  - navig.daemon.service_manager path resolvers (call-time — no reload needed)
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# navig.config.ConfigManager.global_config_dir
# ---------------------------------------------------------------------------


def test_config_manager_global_config_dir_respects_env(tmp_path, monkeypatch):
    """ConfigManager.global_config_dir uses NAVIG_CONFIG_DIR when set."""
    custom = tmp_path / "custom_navig_cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))

    # Use an explicit config_dir so _resolve_paths() writes into tmp_path
    # rather than the real filesystem.
    from navig.config import ConfigManager

    mgr = ConfigManager(config_dir=tmp_path)
    assert mgr.global_config_dir == custom


def test_config_manager_global_config_dir_default(tmp_path, monkeypatch):
    """ConfigManager.global_config_dir falls back to ~/.navig when env unset."""
    monkeypatch.delenv("NAVIG_CONFIG_DIR", raising=False)

    from navig.config import ConfigManager

    mgr = ConfigManager(config_dir=tmp_path)
    assert mgr.global_config_dir == Path.home() / ".navig"


# ---------------------------------------------------------------------------
# navig.cache_store.global_cache_dir
# ---------------------------------------------------------------------------


def test_global_cache_dir_respects_env(tmp_path, monkeypatch):
    """global_cache_dir() returns the platform cache dir driven by NAVIG_CACHE_DIR."""
    custom = tmp_path / "my_cache"
    monkeypatch.setenv("NAVIG_CACHE_DIR", str(custom))

    # Reload because paths is re-evaluated at call time (no module-level constant)
    import navig.platform.paths as _paths_mod

    importlib.reload(_paths_mod)

    from navig.cache_store import global_cache_dir

    result = global_cache_dir()
    assert result == custom


def test_global_cache_dir_default(monkeypatch):
    """global_cache_dir() has a deterministic baseline when NAVIG_CACHE_DIR is unset."""
    monkeypatch.delenv("NAVIG_CACHE_DIR", raising=False)
    monkeypatch.delenv("NAVIG_CONFIG_DIR", raising=False)

    import navig.platform.paths as _paths_mod

    importlib.reload(_paths_mod)

    from navig.cache_store import global_cache_dir

    result = global_cache_dir()
    assert isinstance(result, Path)
    # Falls back to a path under home (exact path depends on platform helper)
    assert result != Path("/")


# ---------------------------------------------------------------------------
# navig.ai_context.AIContextManager.config_dir
# ---------------------------------------------------------------------------


def test_ai_context_manager_config_dir_respects_env(tmp_path, monkeypatch):
    """AIContextManager picks up NAVIG_CONFIG_DIR for its default config_dir."""
    custom = tmp_path / "ai_cfg"
    custom.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))

    from navig.ai_context import AIContextManager

    mgr = AIContextManager()
    assert mgr.config_dir == custom


def test_ai_context_manager_config_dir_explicit_arg(tmp_path, monkeypatch):
    """AIContextManager accepts an explicit config_dir override."""
    explicit = tmp_path / "explicit_cfg"
    explicit.mkdir(parents=True, exist_ok=True)

    from navig.ai_context import AIContextManager

    mgr = AIContextManager(config_dir=explicit)
    assert mgr.config_dir == explicit


# ---------------------------------------------------------------------------
# navig.daemon.supervisor path resolvers (call-time)
# ---------------------------------------------------------------------------


def test_daemon_supervisor_navig_home_respects_env(tmp_path, monkeypatch):
    """supervisor path resolvers honour NAVIG_CONFIG_DIR set AFTER import."""
    import navig.daemon.supervisor as sup_mod

    custom = tmp_path / "sup_cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))

    assert sup_mod._daemon_dir() == custom / "daemon"
    assert sup_mod._pid_file() == custom / "daemon" / "supervisor.pid"
    assert sup_mod._state_file() == custom / "daemon" / "state.json"


# ---------------------------------------------------------------------------
# navig.daemon.entry path resolvers (call-time)
# ---------------------------------------------------------------------------


def test_daemon_entry_navig_home_respects_env(tmp_path, monkeypatch):
    """entry path resolvers honour NAVIG_CONFIG_DIR set AFTER import."""
    import navig.daemon.entry as entry_mod

    custom = tmp_path / "entry_cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))

    assert entry_mod._daemon_config_path() == custom / "daemon" / "config.json"


# ---------------------------------------------------------------------------
# navig.daemon.service_manager path resolvers (call-time)
# ---------------------------------------------------------------------------


def test_daemon_service_manager_navig_home_respects_env(tmp_path, monkeypatch):
    """service_manager path resolvers honour NAVIG_CONFIG_DIR set AFTER import."""
    import navig.daemon.service_manager as sm_mod

    custom = tmp_path / "svc_cfg"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(custom))

    assert sm_mod._navig_home() == custom
    assert sm_mod.daemon_dir() == custom / "daemon"
    assert sm_mod._stop_flag_path() == custom / "daemon" / "stop_requested"
