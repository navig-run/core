"""Tests for commands/mcp_cmd and gateway/audit_log — batch 49."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

runner = CliRunner()

_WARN = "navig.console_helper.warn"


# ---------------------------------------------------------------------------
# mcp_cmd
# ---------------------------------------------------------------------------

def test_mcp_list_no_servers():
    from navig.commands.mcp_cmd import mcp_app

    mock_manager = MagicMock()
    mock_manager.list_servers.return_value = []

    with patch("navig.mcp_manager.MCPManager", return_value=mock_manager):
        result = runner.invoke(mcp_app, ["list"])
    assert result.exit_code == 0
    assert "No MCP servers" in result.output


def test_mcp_list_with_servers():
    from navig.commands.mcp_cmd import mcp_app

    mock_manager = MagicMock()
    mock_manager.list_servers.return_value = ["myserver:8080"]

    with patch("navig.mcp_manager.MCPManager", return_value=mock_manager):
        result = runner.invoke(mcp_app, ["list"])
    assert result.exit_code == 0
    assert "myserver:8080" in result.output


def test_mcp_list_manager_no_list_servers():
    from navig.commands.mcp_cmd import mcp_app

    mock_manager = MagicMock(spec=[])  # no list_servers attribute

    with patch("navig.mcp_manager.MCPManager", return_value=mock_manager):
        result = runner.invoke(mcp_app, ["list"])
    assert result.exit_code == 0
    assert "No MCP servers" in result.output


def test_mcp_serve_print_config():
    from navig.commands.mcp_cmd import mcp_app

    mock_mcp_server = MagicMock()
    mock_mcp_server.generate_perplexity_mcp_config.return_value = {
        "mcp_server_url": "http://127.0.0.1:3001/mcp"
    }
    mock_mcp_server.generate_vscode_mcp_config.return_value = {"type": "http", "url": "http://127.0.0.1:3001"}
    mock_mcp_server.generate_claude_mcp_config.return_value = {"mcpServers": {}}

    with patch.dict("sys.modules", {"navig.mcp_server": mock_mcp_server}):
        result = runner.invoke(mcp_app, ["serve", "--print-config"])
    assert result.exit_code == 0


def test_mcp_serve_import_error_exits_1():
    from navig.commands.mcp_cmd import mcp_app

    mock_mcp_server = MagicMock()
    mock_mcp_server.start_mcp_server.side_effect = ImportError("No module")

    with patch.dict("sys.modules", {"navig.mcp_server": mock_mcp_server}):
        result = runner.invoke(mcp_app, ["serve"])
    assert result.exit_code == 1


def test_mcp_status_no_impl():
    from navig.commands.mcp_cmd import mcp_app

    with patch(_WARN, create=True):
        result = runner.invoke(mcp_app, ["status"])
    assert result.exit_code == 0


def test_mcp_serve_help():
    from navig.commands.mcp_cmd import mcp_app

    result = runner.invoke(mcp_app, ["serve", "--help"])
    assert result.exit_code == 0


def test_mcp_list_help():
    from navig.commands.mcp_cmd import mcp_app

    result = runner.invoke(mcp_app, ["list", "--help"])
    assert result.exit_code == 0


# ---------------------------------------------------------------------------
# AuditLog
# ---------------------------------------------------------------------------

def test_audit_log_record_returns_dict(tmp_path):
    from navig.gateway.audit_log import AuditLog

    log = AuditLog(path=tmp_path / "audit.jsonl")
    record = log.record(actor="user:1", action="db.query", status="success")
    assert isinstance(record, dict)


def test_audit_log_record_has_required_fields(tmp_path):
    from navig.gateway.audit_log import AuditLog

    log = AuditLog(path=tmp_path / "audit.jsonl")
    record = log.record(actor="user:1", action="run.shell", status="success")
    for field in ("ts", "actor", "action", "policy", "status"):
        assert field in record, f"Missing field: {field}"


def test_audit_log_record_actor_matches(tmp_path):
    from navig.gateway.audit_log import AuditLog

    log = AuditLog(path=tmp_path / "audit.jsonl")
    record = log.record(actor="telegram:999", action="test", status="success")
    assert record["actor"] == "telegram:999"


def test_audit_log_record_action_matches(tmp_path):
    from navig.gateway.audit_log import AuditLog

    log = AuditLog(path=tmp_path / "audit.jsonl")
    record = log.record(actor="a", action="file.upload", status="success")
    assert record["action"] == "file.upload"


def test_audit_log_record_input_hash(tmp_path):
    from navig.gateway.audit_log import AuditLog

    log = AuditLog(path=tmp_path / "audit.jsonl")
    record = log.record(actor="a", action="b", raw_input="SELECT * FROM users")
    assert "input_hash" in record
    assert record["input_hash"].startswith("sha256:")


def test_audit_log_record_output_len(tmp_path):
    from navig.gateway.audit_log import AuditLog

    log = AuditLog(path=tmp_path / "audit.jsonl")
    record = log.record(actor="a", action="b", raw_output="hello world")
    assert "output_len" in record
    assert record["output_len"] == len("hello world")


def test_audit_log_record_no_input_no_hash(tmp_path):
    from navig.gateway.audit_log import AuditLog

    log = AuditLog(path=tmp_path / "audit.jsonl")
    record = log.record(actor="a", action="b")
    assert "input_hash" not in record


def test_audit_log_record_metadata(tmp_path):
    from navig.gateway.audit_log import AuditLog

    log = AuditLog(path=tmp_path / "audit.jsonl")
    record = log.record(actor="a", action="b", metadata={"key": "value"})
    assert record.get("metadata", {}).get("key") == "value"


def test_audit_log_writes_to_file(tmp_path):
    from navig.gateway.audit_log import AuditLog

    path = tmp_path / "audit.jsonl"
    log = AuditLog(path=path)
    log.record(actor="a", action="b", status="success")
    assert path.exists()
    content = path.read_text()
    assert len(content) > 0


def test_audit_log_writes_jsonl(tmp_path):
    from navig.gateway.audit_log import AuditLog

    path = tmp_path / "audit.jsonl"
    log = AuditLog(path=path)
    log.record(actor="a", action="b", status="success")
    line = path.read_text().strip()
    data = json.loads(line)
    assert data["action"] == "b"


def test_audit_log_tail_no_file(tmp_path):
    from navig.gateway.audit_log import AuditLog

    log = AuditLog(path=tmp_path / "audit.jsonl")
    result = log.tail()
    assert result == []


def test_audit_log_tail_returns_records(tmp_path):
    from navig.gateway.audit_log import AuditLog

    path = tmp_path / "audit.jsonl"
    log = AuditLog(path=path)
    log.record(actor="a", action="r1", status="success")
    log.record(actor="b", action="r2", status="success")
    records = log.tail(n=10)
    assert len(records) == 2


def test_audit_log_tail_limits_n(tmp_path):
    from navig.gateway.audit_log import AuditLog

    path = tmp_path / "audit.jsonl"
    log = AuditLog(path=path)
    for i in range(20):
        log.record(actor="a", action=f"action{i}", status="success")
    records = log.tail(n=5)
    assert len(records) == 5


def test_audit_log_build_record_no_raw():
    from navig.gateway.audit_log import AuditLog

    r = AuditLog._build_record(
        actor="u", action="a", policy="allow", status="success",
        raw_input=None, raw_output=None, metadata={},
    )
    assert r["actor"] == "u"
    assert "input_hash" not in r
    assert "output_len" not in r


def test_audit_log_default_policy():
    from navig.gateway.audit_log import AuditLog

    r = AuditLog._build_record(
        actor="u", action="a", policy="allow", status="success",
        raw_input=None, raw_output=None, metadata={},
    )
    assert r["policy"] == "allow"
