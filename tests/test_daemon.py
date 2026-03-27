"""Tests for the NAVIG daemon supervisor and service manager."""

import json
import os
import sys
from unittest.mock import patch


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
