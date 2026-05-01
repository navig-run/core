"""Tests for daemon/supervisor.py and providers/airllm.py — batch 113."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# navig/daemon/supervisor.py — constants and ChildProcess
# ---------------------------------------------------------------------------

class TestSupervisorConstants:
    def test_max_restart_delay(self):
        from navig.daemon.supervisor import MAX_RESTART_DELAY
        assert isinstance(MAX_RESTART_DELAY, int)
        assert MAX_RESTART_DELAY > 0

    def test_initial_restart_delay(self):
        from navig.daemon.supervisor import INITIAL_RESTART_DELAY
        assert isinstance(INITIAL_RESTART_DELAY, int)
        assert INITIAL_RESTART_DELAY > 0

    def test_initial_less_than_max(self):
        from navig.daemon.supervisor import INITIAL_RESTART_DELAY, MAX_RESTART_DELAY
        assert INITIAL_RESTART_DELAY < MAX_RESTART_DELAY

    def test_health_check_interval(self):
        from navig.daemon.supervisor import HEALTH_CHECK_INTERVAL
        assert isinstance(HEALTH_CHECK_INTERVAL, int)
        assert HEALTH_CHECK_INTERVAL > 0

    def test_pid_file_is_path(self):
        from navig.daemon.supervisor import PID_FILE
        assert isinstance(PID_FILE, Path)

    def test_state_file_is_path(self):
        from navig.daemon.supervisor import STATE_FILE
        assert isinstance(STATE_FILE, Path)

    def test_daemon_dir_is_path(self):
        from navig.daemon.supervisor import DAEMON_DIR
        assert isinstance(DAEMON_DIR, Path)


class TestChildProcessInit:
    def _make(self, **kwargs):
        from navig.daemon.supervisor import ChildProcess
        defaults = dict(name="bot", command=["python", "-m", "navig"])
        defaults.update(kwargs)
        return ChildProcess(**defaults)

    def test_name_stored(self):
        cp = self._make(name="mybot")
        assert cp.name == "mybot"

    def test_command_stored(self):
        cp = self._make(command=["echo", "hello"])
        assert cp.command == ["echo", "hello"]

    def test_enabled_default_true(self):
        cp = self._make()
        assert cp.enabled is True

    def test_enabled_can_be_false(self):
        cp = self._make(enabled=False)
        assert cp.enabled is False

    def test_critical_default_false(self):
        cp = self._make()
        assert cp.critical is False

    def test_critical_can_be_true(self):
        cp = self._make(critical=True)
        assert cp.critical is True

    def test_restart_count_starts_zero(self):
        cp = self._make()
        assert cp.restart_count == 0

    def test_process_starts_none(self):
        cp = self._make()
        assert cp.process is None

    def test_last_exit_code_starts_none(self):
        cp = self._make()
        assert cp.last_exit_code is None

    def test_env_extra_empty_default(self):
        cp = self._make()
        assert cp.env_extra == {}

    def test_env_extra_stored(self):
        cp = self._make(env_extra={"FOO": "bar"})
        assert cp.env_extra == {"FOO": "bar"}

    def test_cwd_default_none(self):
        cp = self._make()
        assert cp.cwd is None


class TestChildProcessIsAlive:
    def _make(self):
        from navig.daemon.supervisor import ChildProcess
        return ChildProcess(name="test", command=["echo"])

    def test_no_process_not_alive(self):
        cp = self._make()
        assert cp.is_alive() is False

    def test_alive_when_process_running(self):
        cp = self._make()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # still running
        cp.process = mock_proc
        assert cp.is_alive() is True

    def test_dead_when_process_exited(self):
        cp = self._make()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # exited
        cp.process = mock_proc
        assert cp.is_alive() is False


class TestChildProcessPoll:
    def _make(self):
        from navig.daemon.supervisor import ChildProcess
        return ChildProcess(name="test", command=["echo"])

    def test_no_process_returns_none(self):
        cp = self._make()
        assert cp.poll() is None

    def test_returns_exit_code(self):
        cp = self._make()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1
        cp.process = mock_proc
        result = cp.poll()
        assert result == 1

    def test_stores_exit_code(self):
        cp = self._make()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 42
        cp.process = mock_proc
        cp.poll()
        assert cp.last_exit_code == 42

    def test_running_process_returns_none(self):
        cp = self._make()
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        cp.process = mock_proc
        assert cp.poll() is None


class TestChildProcessBackoff:
    def _make(self):
        from navig.daemon.supervisor import ChildProcess, INITIAL_RESTART_DELAY
        return ChildProcess(name="test", command=["echo"]), INITIAL_RESTART_DELAY

    def test_first_delay_is_initial(self):
        cp, initial = self._make()
        assert cp.next_restart_delay == initial

    def test_delay_doubles(self):
        cp, initial = self._make()
        d1 = cp.next_restart_delay
        d2 = cp.next_restart_delay
        assert d2 == d1 * 2

    def test_delay_capped_at_max(self):
        from navig.daemon.supervisor import ChildProcess, MAX_RESTART_DELAY
        cp = ChildProcess(name="test", command=["echo"])
        # Exhaust backoff until capped
        for _ in range(20):
            _ = cp.next_restart_delay
        assert cp.next_restart_delay == MAX_RESTART_DELAY

    def test_reset_backoff_resets(self):
        from navig.daemon.supervisor import ChildProcess, INITIAL_RESTART_DELAY
        cp = ChildProcess(name="test", command=["echo"])
        _ = cp.next_restart_delay
        _ = cp.next_restart_delay
        cp.reset_backoff()
        assert cp.next_restart_delay == INITIAL_RESTART_DELAY


class TestChildProcessDrainOutput:
    def test_drain_output_noop(self):
        from navig.daemon.supervisor import ChildProcess
        cp = ChildProcess(name="test", command=["echo"])
        logger = logging.getLogger("test")
        # Should not raise
        cp.drain_output(logger, logger)


# ---------------------------------------------------------------------------
# navig/providers/airllm.py — pure helpers
# ---------------------------------------------------------------------------

class TestAirLLMConfig:
    def test_default_model_path_empty(self):
        from navig.providers.airllm import AirLLMConfig
        cfg = AirLLMConfig()
        assert cfg.model_path == ""

    def test_default_max_vram(self):
        from navig.providers.airllm import AirLLMConfig
        cfg = AirLLMConfig()
        assert cfg.max_vram_gb == 8.0

    def test_default_device_cuda(self):
        from navig.providers.airllm import AirLLMConfig
        cfg = AirLLMConfig()
        assert cfg.device == "cuda"

    def test_from_dict_basic(self):
        from navig.providers.airllm import AirLLMConfig
        cfg = AirLLMConfig.from_dict({"model_path": "meta-llama/Llama-2-7b", "max_vram_gb": 4.0})
        assert cfg.model_path == "meta-llama/Llama-2-7b"
        assert cfg.max_vram_gb == 4.0

    def test_from_dict_empty(self):
        from navig.providers.airllm import AirLLMConfig
        cfg = AirLLMConfig.from_dict({})
        assert cfg.model_path == ""
        assert cfg.max_vram_gb == 8.0

    def test_from_dict_compression(self):
        from navig.providers.airllm import AirLLMConfig
        cfg = AirLLMConfig.from_dict({"compression": "4bit"})
        assert cfg.compression == "4bit"

    def test_prefetching_default_true(self):
        from navig.providers.airllm import AirLLMConfig
        cfg = AirLLMConfig()
        assert cfg.prefetching is True

    def test_delete_original_default_false(self):
        from navig.providers.airllm import AirLLMConfig
        cfg = AirLLMConfig()
        assert cfg.delete_original is False


class TestIsAirLLMAvailable:
    def test_returns_bool(self):
        from navig.providers.airllm import is_airllm_available
        result = is_airllm_available()
        assert isinstance(result, bool)

    def test_reflects_import_flag(self):
        from navig.providers import airllm as airllm_mod
        from navig.providers.airllm import is_airllm_available
        # Whatever AIRLLM_AVAILABLE is, is_airllm_available() should match
        assert is_airllm_available() == airllm_mod.AIRLLM_AVAILABLE


class TestGetAirLLMVramRecommendations:
    def test_returns_dict(self):
        from navig.providers.airllm import get_airllm_vram_recommendations
        result = get_airllm_vram_recommendations()
        assert isinstance(result, dict)

    def test_has_model_sizes(self):
        from navig.providers.airllm import get_airllm_vram_recommendations
        result = get_airllm_vram_recommendations()
        assert any("7B" in k for k in result)
        assert any("70B" in k for k in result)

    def test_values_are_strings(self):
        from navig.providers.airllm import get_airllm_vram_recommendations
        for v in get_airllm_vram_recommendations().values():
            assert isinstance(v, str)

    def test_non_empty(self):
        from navig.providers.airllm import get_airllm_vram_recommendations
        assert len(get_airllm_vram_recommendations()) > 0
