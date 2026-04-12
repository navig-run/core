"""Tests for the NAVIG daemon supervisor and service manager."""

import json
import os
import sys
from unittest.mock import patch
import pytest

pytestmark = pytest.mark.integration


class TestNavigDaemon:
    """Test the NavigDaemon supervisor."""

    def test_import(self):
        from navig.daemon.supervisor import NavigDaemon

        assert NavigDaemon is not None

    def test_create_daemon(self, tmp_path):
        from navig.daemon.supervisor import NavigDaemon

        d = NavigDaemon()
        assert d.children == []
        assert d._running is False

    def test_add_telegram_bot(self, tmp_path):
        """Bot is registered when script exists."""
        from navig.daemon.supervisor import NavigDaemon

        bot_script = tmp_path / "navig_bot.py"
        bot_script.write_text("# dummy bot")

        d = NavigDaemon()
        d.add_telegram_bot(bot_script=bot_script)
        assert len(d.children) == 1
        assert d.children[0].name == "telegram-bot"
        assert d.children[0].critical is True
        assert str(bot_script) in d.children[0].command[1]

    def test_add_telegram_bot_missing(self, tmp_path):
        """Bot is NOT registered when script doesn't exist."""
        from navig.daemon.supervisor import NavigDaemon

        d = NavigDaemon()
        d.add_telegram_bot(bot_script=tmp_path / "nonexistent.py")
        assert len(d.children) == 0

    def test_add_telegram_bot_defaults_to_module(self):
        """Without script override, daemon launches telegram_worker module."""
        from navig.daemon.supervisor import NavigDaemon

        d = NavigDaemon()
        d.add_telegram_bot()
        assert len(d.children) == 1
        assert d.children[0].name == "telegram-bot"
        assert d.children[0].command[:3] == [
            sys.executable,
            "-m",
            "navig.daemon.telegram_worker",
        ]

    def test_add_gateway(self):
        from navig.daemon.supervisor import NavigDaemon

        d = NavigDaemon()
        d.add_gateway(port=9999)
        assert len(d.children) == 1
        assert d.children[0].name == "gateway"
        assert "9999" in d.children[0].command

    def test_add_scheduler(self):
        from navig.daemon.supervisor import NavigDaemon

        d = NavigDaemon()
        d.add_scheduler()
        assert len(d.children) == 1
        assert d.children[0].name == "scheduler"

    def test_pid_management(self, tmp_path):
        """PID file write/read/remove cycle."""
        from navig.daemon import supervisor as sv

        orig_pid = sv.PID_FILE
        sv.PID_FILE = tmp_path / "test.pid"
        try:
            from navig.daemon.supervisor import NavigDaemon

            d = NavigDaemon()
            d._write_pid()
            assert sv.PID_FILE.exists()
            assert NavigDaemon.read_pid() == os.getpid()
            d._remove_pid()
            assert not sv.PID_FILE.exists()
        finally:
            sv.PID_FILE = orig_pid

    def test_child_process_to_dict(self, tmp_path):
        from navig.daemon.supervisor import ChildProcess

        c = ChildProcess("test", [sys.executable, "-c", "pass"])
        d = c.to_dict()
        assert d["name"] == "test"
        assert d["alive"] is False
        assert d["restart_count"] == 0

    def test_child_exponential_backoff(self):
        from navig.daemon.supervisor import ChildProcess

        c = ChildProcess("test", ["echo"])
        delays = [c.next_restart_delay for _ in range(5)]
        # Should be increasing
        assert delays[0] < delays[-1]
        # Reset
        c.reset_backoff()
        assert c._backoff == 2

    def test_stop_running_daemon_returns_false_if_process_persists(self, monkeypatch):
        from navig.daemon.supervisor import NavigDaemon

        monkeypatch.setattr("navig.daemon.supervisor.sys.platform", "linux")
        monkeypatch.setattr(NavigDaemon, "read_pid", staticmethod(lambda: 4242))
        monkeypatch.setattr(NavigDaemon, "is_running", staticmethod(lambda: True))
        monkeypatch.setattr("navig.daemon.supervisor.os.kill", lambda *_args, **_kwargs: None)
        monkeypatch.setattr("navig.daemon.supervisor.time.sleep", lambda *_args, **_kwargs: None)

        assert NavigDaemon.stop_running_daemon() is False

    def test_stop_running_daemon_returns_false_on_permission_error(self, monkeypatch):
        from navig.daemon.supervisor import NavigDaemon

        monkeypatch.setattr("navig.daemon.supervisor.sys.platform", "linux")
        monkeypatch.setattr(NavigDaemon, "read_pid", staticmethod(lambda: 31337))
        monkeypatch.setattr(
            "navig.daemon.supervisor.os.kill",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(PermissionError("denied")),
        )

        assert NavigDaemon.stop_running_daemon() is False


class TestDaemonConfig:
    """Test daemon configuration."""

    def test_save_default_config(self, tmp_path, monkeypatch):
        from navig.daemon import entry

        config_path = tmp_path / "config.json"
        monkeypatch.setattr(entry, "DAEMON_CONFIG", config_path)

        result = entry.save_default_config()
        assert result == config_path
        assert config_path.exists()

        cfg = json.loads(config_path.read_text())
        assert cfg["telegram_bot"] is True
        assert cfg["gateway"] is False

    def test_load_config_defaults(self, tmp_path, monkeypatch):
        from navig.daemon import entry

        monkeypatch.setattr(entry, "DAEMON_CONFIG", tmp_path / "missing.json")
        cfg = entry._load_config()
        assert cfg["telegram_bot"] is True
        assert cfg["health_port"] == 0

    def test_load_config_non_object_defaults(self, tmp_path, monkeypatch):
        from navig.daemon import entry

        config_path = tmp_path / "config.json"
        config_path.write_text("[]", encoding="utf-8")
        monkeypatch.setattr(entry, "DAEMON_CONFIG", config_path)

        cfg = entry._load_config()
        assert cfg == entry.DEFAULT_DAEMON_CONFIG

    def test_save_default_config_repairs_malformed_json(self, tmp_path, monkeypatch):
        from navig.daemon import entry

        config_path = tmp_path / "config.json"
        config_path.write_text("{bad-json", encoding="utf-8")
        monkeypatch.setattr(entry, "DAEMON_CONFIG", config_path)

        result = entry.save_default_config()
        assert result == config_path

        repaired = json.loads(config_path.read_text(encoding="utf-8"))
        assert repaired == entry.DEFAULT_DAEMON_CONFIG

    def test_as_bool_string_values(self):
        from navig.daemon import entry

        assert entry._as_bool("true", False) is True
        assert entry._as_bool("false", True) is False
        assert entry._as_bool("0", True) is False
        assert entry._as_bool("1", False) is True

    def test_as_int_string_values(self):
        from navig.daemon import entry

        assert entry._as_int("8080", 0) == 8080
        assert entry._as_int("", 8789) == 8789
        assert entry._as_int("not-a-number", 8789) == 8789

    def test_main_respects_string_false_flags(self, monkeypatch):
        from navig.daemon import entry

        calls = {"telegram": 0, "gateway": 0, "scheduler": 0, "run": 0}

        class FakeDaemon:
            def __init__(self, health_port=0):
                self.health_port = health_port

            def add_telegram_bot(self, bot_script=None):
                calls["telegram"] += 1

            def add_gateway(self, port=8789):
                calls["gateway"] += 1

            def add_scheduler(self):
                calls["scheduler"] += 1

            def run(self):
                calls["run"] += 1

        monkeypatch.setattr(
            entry,
            "_load_config",
            lambda: {
                "telegram_bot": "false",
                "gateway": "false",
                "scheduler": "false",
                "health_port": 0,
            },
        )
        monkeypatch.setattr("navig.daemon.supervisor.NavigDaemon", FakeDaemon)

        entry.main()

        assert calls["telegram"] == 0
        assert calls["gateway"] == 0
        assert calls["scheduler"] == 0
        assert calls["run"] == 1

    def test_main_coerces_string_ports(self, monkeypatch):
        from navig.daemon import entry

        calls = {"health_port": None, "gateway_port": None, "run": 0}

        class FakeDaemon:
            def __init__(self, health_port=0):
                calls["health_port"] = health_port

            def add_telegram_bot(self, bot_script=None):
                return None

            def add_gateway(self, port=8789):
                calls["gateway_port"] = port

            def add_scheduler(self):
                return None

            def run(self):
                calls["run"] += 1

        monkeypatch.setattr(
            entry,
            "_load_config",
            lambda: {
                "telegram_bot": False,
                "gateway": True,
                "scheduler": False,
                "health_port": "0",
                "gateway_port": "9001",
            },
        )
        monkeypatch.setattr("navig.daemon.supervisor.NavigDaemon", FakeDaemon)

        entry.main()

        assert calls["health_port"] == 0
        assert calls["gateway_port"] == 9001
        assert calls["run"] == 1


class TestServiceManager:
    """Test service manager detection."""

    def test_detect_best_method_no_nssm(self):
        from navig.daemon import service_manager as sm

        with patch.object(sm, "has_nssm", return_value=False):
            assert sm.detect_best_method() == "task"

    def test_detect_best_method_nssm_admin(self):
        from navig.daemon import service_manager as sm

        with (
            patch.object(sm, "has_nssm", return_value=True),
            patch.object(sm, "is_admin", return_value=True),
        ):
            assert sm.detect_best_method() == "nssm"

    def test_detect_best_method_nssm_no_admin(self):
        from navig.daemon import service_manager as sm

        with (
            patch.object(sm, "has_nssm", return_value=True),
            patch.object(sm, "is_admin", return_value=False),
        ):
            assert sm.detect_best_method() == "task"

    def test_install_unknown_method(self):
        from navig.daemon import service_manager as sm

        ok, msg = sm.install(method="foobar")
        assert ok is False
        assert "Unknown method" in msg

    def test_nssm_install_not_found(self):
        from navig.daemon import service_manager as sm

        ok, msg = sm.install(method="nssm")
        if not sm.has_nssm():
            assert ok is False
            assert "NSSM not found" in msg
