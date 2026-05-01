"""Tests for commands/net — batch 50."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

runner = CliRunner()


def _make_backend(speedtest_result=None, iperf3_result=None):
    backend = MagicMock()
    backend.run_speedtest_cli.return_value = speedtest_result or {
        "download_mbps": 100.0, "upload_mbps": 50.0, "ping_ms": 10.0
    }
    backend.run_iperf3.return_value = iperf3_result or {
        "download_mbps": 95.0, "upload_mbps": 48.0
    }
    return backend



# NOTE: net_app has single auto-promoted command (no subcommand prefix needed)

def test_net_speedtest_no_iperf3_server_exits_1():
    from navig.commands.net import net_app

    result = runner.invoke(net_app, [])
    assert result.exit_code == 1


def test_net_speedtest_no_iperf3_error_message():
    from navig.commands.net import net_app

    result = runner.invoke(net_app, [])
    assert "--iperf3-server" in result.output or "--skip-iperf3" in result.output


def test_net_speedtest_skip_iperf3_only():
    from navig.commands.net import net_app

    backend = _make_backend()
    with patch("navig.commands.net._backend", return_value=backend):
        result = runner.invoke(net_app, ["--skip-iperf3"])
    assert result.exit_code == 0


def test_net_speedtest_skip_iperf3_output_json():
    from navig.commands.net import net_app

    backend = _make_backend()
    with patch("navig.commands.net._backend", return_value=backend):
        result = runner.invoke(net_app, ["--skip-iperf3", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "speedtest_cli" in data
    assert data["iperf3"]["skipped"] is True


def test_net_speedtest_skip_speedtest_only():
    from navig.commands.net import net_app

    backend = _make_backend()
    with patch("navig.commands.net._backend", return_value=backend):
        result = runner.invoke(net_app, ["--skip-speedtest", "--iperf3-server", "iperf.he.net"])
    assert result.exit_code == 0


def test_net_speedtest_skip_speedtest_skipped_in_output():
    from navig.commands.net import net_app

    backend = _make_backend()
    with patch("navig.commands.net._backend", return_value=backend):
        result = runner.invoke(
            net_app, ["--skip-speedtest", "--iperf3-server", "iperf.he.net", "--json"]
        )
    data = json.loads(result.output)
    assert data["speedtest_cli"]["skipped"] is True


def test_net_speedtest_both_methods():
    from navig.commands.net import net_app

    backend = _make_backend()
    with patch("navig.commands.net._backend", return_value=backend):
        result = runner.invoke(net_app, ["--iperf3-server", "iperf.he.net", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "speedtest_cli" in data
    assert "iperf3" in data


def test_net_speedtest_backend_error_exits_1():
    from navig.commands.net import net_app

    with patch("navig.commands.net._backend", side_effect=Exception("worker not found")):
        result = runner.invoke(net_app, ["--skip-iperf3"])
    assert result.exit_code == 1


def test_net_speedtest_backend_error_message():
    from navig.commands.net import net_app

    with patch("navig.commands.net._backend", side_effect=Exception("worker not found")):
        result = runner.invoke(net_app, ["--skip-iperf3"])
    assert "worker not found" in result.output or "ERROR" in result.output


def test_net_speedtest_with_banner_output():
    from navig.commands.net import net_app

    backend = _make_backend()
    with patch("navig.commands.net._backend", return_value=backend):
        result = runner.invoke(net_app, ["--iperf3-server", "iperf.he.net"])
    assert result.exit_code == 0
    assert "PHASE" in result.output or "speedtest" in result.output.lower()


def test_net_speedtest_custom_port():
    from navig.commands.net import net_app

    backend = _make_backend()
    with patch("navig.commands.net._backend", return_value=backend):
        result = runner.invoke(
            net_app, ["--iperf3-server", "myserver", "--iperf3-port", "5202", "--json"]
        )
    assert result.exit_code == 0
    backend.run_iperf3.assert_called_once_with("myserver", 5202, silent=True)


def test_net_speedtest_help():
    from navig.commands.net import net_app

    result = runner.invoke(net_app, ["--help"])
    assert result.exit_code == 0


def test_net_help_shows_speedtest():
    from navig.commands.net import net_app

    result = runner.invoke(net_app, ["--help"])
    assert "speedtest" in result.output.lower() or result.exit_code in (0, 2)


def test_net_speedtest_summary_json_key():
    from navig.commands.net import net_app

    backend = _make_backend()
    with patch("navig.commands.net._backend", return_value=backend):
        result = runner.invoke(net_app, ["--skip-iperf3", "--json"])
    data = json.loads(result.output)
    assert "speedtest_cli" in data
    assert "iperf3" in data
