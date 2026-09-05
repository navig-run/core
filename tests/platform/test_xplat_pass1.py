"""Cross-platform hardening (Pass 1) regression tests.

Locks in the fixes from the Windows/macOS/Linux audit so they cannot silently
regress:
  - liveness probing must not use os.kill (it TERMINATES the process on Windows)
  - open_path is best-effort and never raises
  - all OS adapters report the SSOT config dir (no divergent %APPDATA% guess)
  - the soul path resolves lazily (honors a late NAVIG_CONFIG_DIR)

Encoding discipline used to live here too; it now has its own guard — see the note further
down and tests/quality/test_text_encoding_discipline.py.
"""
from __future__ import annotations

import os

import pytest


class TestPidProbeNeverKills:
    def test_current_process_is_alive(self):
        from navig.providers.bridge_grid_reader import _is_pid_alive

        assert _is_pid_alive(os.getpid()) is True

    def test_dead_pid_is_not_alive(self):
        from navig.providers.bridge_grid_reader import _is_pid_alive

        assert _is_pid_alive(2_000_000_000) is False  # implausibly-high PID

    def test_nonpositive_pid_is_not_alive(self):
        from navig.providers.bridge_grid_reader import _is_pid_alive

        assert _is_pid_alive(0) is False
        assert _is_pid_alive(-1) is False

    def test_probe_never_calls_os_kill(self, monkeypatch):
        # Reverting to os.kill(pid, 0) would kill the probed process on Windows.
        import navig.providers.bridge_grid_reader as m

        def _boom(*_a, **_k):
            raise AssertionError("liveness probe must not use os.kill (kills on Windows)")

        monkeypatch.setattr(os, "kill", _boom)
        m._is_pid_alive(os.getpid())  # must not raise


class TestOpenPath:
    def test_never_raises_on_failure(self, monkeypatch):
        from navig.platform import opener

        def _raise(*_a, **_k):
            raise OSError("no opener available")

        monkeypatch.setattr(opener.subprocess, "Popen", _raise)
        monkeypatch.setattr(opener.os, "startfile", _raise, raising=False)
        assert opener.open_path("anything") is False

    def test_dispatches_and_returns_true(self, monkeypatch):
        from navig.platform import opener

        calls = []
        monkeypatch.setattr(opener.subprocess, "Popen", lambda *a, **k: calls.append(a))
        monkeypatch.setattr(opener.os, "startfile", lambda *a, **k: calls.append(a), raising=False)
        assert opener.open_path("x") is True
        assert calls


class TestConfigDirReconciled:
    @pytest.mark.parametrize("os_name", ["windows", "linux", "macos"])
    def test_adapter_matches_ssot(self, os_name):
        from navig.adapters.os.factory import get_os_adapter
        from navig.platform.paths import config_dir

        assert get_os_adapter(os_name).get_config_directory() == config_dir()


class TestSoulPathLazy:
    def test_honors_late_config_dir(self, monkeypatch, tmp_path):
        import navig.agent.conv.soul as soul

        monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
        got = soul._soul_md_path()
        assert str(tmp_path) in str(got)
        assert got.name == "SOUL.md"


# Encoding discipline moved to tests/quality/test_text_encoding_discipline.py.
#
# The check here was textual and matched the literal `read_text()`, so it saw exactly one
# spelling of one function in one tree: `write_text(...)`, `open(...)` and `Path.open(...)`
# were all invisible, as was every plugin. Measured when it was replaced, 49 sites had no
# encoding= and this check reported zero — and three of the four it *would* have flagged in
# core were `adapter.read_text(selector, control_id)`, an AutoHotkey UI read that is not a
# file read at all. The replacement resolves calls on the AST, covers core + plugins, and is
# registered in `sourceGuardArgs` so a plugin change reaches it.


class TestMediaDirReconciliation:
    """media_dir reconciles the ~/.navig vs ~/.navig/data split without moving files."""

    def test_fresh_install_uses_data_subdir(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("NAVIG_DATA_DIR", raising=False)
        from navig.platform.paths import media_dir

        assert media_dir("audio") == tmp_path / "data" / "audio"

    def test_legacy_dir_preserved(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("NAVIG_DATA_DIR", raising=False)
        (tmp_path / "audio").mkdir()  # an older build's output dir
        from navig.platform.paths import media_dir

        assert media_dir("audio") == tmp_path / "audio"

    def test_explicit_data_dir_is_authoritative(self, monkeypatch, tmp_path):
        monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
        (tmp_path / "audio").mkdir()  # legacy present...
        monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "dd"))
        from navig.platform.paths import media_dir

        assert media_dir("audio") == tmp_path / "dd" / "audio"  # ...but explicit env wins
