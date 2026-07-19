"""`navig audit tail` — the terminal surface for the privileged-action audit log.

The gateway AuditLog (runtime/audit.jsonl) was queryable only via GET /audit on
a live daemon; `navig audit tail` reads the file directly from disk (offline,
no daemon — the `navig ledger show` contract). These tests pin:

- table rendering + every filter (--action prefix / --actor exact / --status)
- --tail/-n limiting
- --json emits exactly ONE JSON document in EVERY branch (populated, filtered,
  empty, missing) — the JSON stdout tripwire contract
- missing/empty files are honest non-failures: friendly message, exit 0
- malformed JSONL lines are skipped, never fatal
- CLI wiring: registration map + help dictionary + help page all exist
"""

from __future__ import annotations

import json

import pytest

from navig.commands.audit import audit_app

pytestmark = pytest.mark.integration


_RECORDS = [
    {
        "ts": "2026-07-16T10:00:00.000+00:00",
        "actor": "tg:u1",
        "action": "tool.execute.bash_exec",
        "policy": "require_approval",
        "status": "pending_approval",
        "input_hash": "sha256:aabbccddeeff0011",
        "metadata": {"safety_level": "dangerous"},
    },
    {
        "ts": "2026-07-16T10:00:05.000+00:00",
        "actor": "tg:u1",
        "action": "tool.execute.bash_exec",
        "policy": "require_approval",
        "status": "approved",
        "input_hash": "sha256:aabbccddeeff0011",
        "metadata": {"via": "approval_manager"},
    },
    {
        "ts": "2026-07-16T10:01:00.000+00:00",
        "actor": "cli:a",
        "action": "mission.create",
        "policy": "require_approval",
        "status": "denied",
        "input_hash": "sha256:1122334455667788",
        "metadata": {"reason": "approval_unavailable"},
    },
    {
        "ts": "2026-07-16T10:02:00.000+00:00",
        "actor": "cli:a",
        "action": "run.shell",
        "policy": "allow",
        "status": "success",
        "input_hash": "sha256:99aabbccddee0011",
    },
]


@pytest.fixture()
def audit_file(tmp_path):
    path = tmp_path / "audit.jsonl"
    lines = [json.dumps(r) for r in _RECORDS]
    lines.insert(2, "this line is not JSON {{{")  # malformed → skipped, not fatal
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _invoke(args):
    from typer.testing import CliRunner

    return CliRunner().invoke(audit_app, args, obj={})


# ─────────────────────────── table view ───────────────────────────


def test_tail_renders_records_and_exits_zero(audit_file):
    result = _invoke(["tail", "--path", str(audit_file)])
    assert result.exit_code == 0
    assert "run.shell" in result.stdout
    assert "mission.create" in result.stdout
    assert "denied" in result.stdout
    # malformed line skipped silently — all 4 real records counted
    assert "4/4" in result.stdout


def test_tail_limit_flag(audit_file):
    result = _invoke(["tail", "--path", str(audit_file), "-n", "1"])
    assert result.exit_code == 0
    assert "run.shell" in result.stdout  # newest record
    assert "mission.create" not in result.stdout


def test_status_filter(audit_file):
    result = _invoke(["tail", "--path", str(audit_file), "--status", "denied"])
    assert result.exit_code == 0
    assert "mission.create" in result.stdout
    assert "run.shell" not in result.stdout


def test_action_filter_is_a_prefix_match(audit_file):
    """Same semantics as GET /audit: action is a startswith filter."""
    result = _invoke(["tail", "--path", str(audit_file), "--action", "tool.execute", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["count"] == 2
    assert {e["action"] for e in payload["events"]} == {"tool.execute.bash_exec"}


def test_actor_filter_is_exact(audit_file):
    result = _invoke(["tail", "--path", str(audit_file), "--actor", "cli:a", "--json"])
    payload = json.loads(result.stdout)
    assert payload["count"] == 2
    assert all(e["actor"] == "cli:a" for e in payload["events"])


def test_no_filter_match_is_honest_and_exits_zero(audit_file):
    result = _invoke(["tail", "--path", str(audit_file), "--actor", "nobody:x"])
    assert result.exit_code == 0
    assert "No records match" in result.stdout


# ─────────────────────────── --json purity ───────────────────────────


def test_json_is_exactly_one_document(audit_file):
    result = _invoke(["tail", "--path", str(audit_file), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)  # whole stdout must be ONE document
    assert payload["total"] == 4
    assert payload["count"] == 4
    assert payload["events"][-1]["action"] == "run.shell"


def test_json_missing_file_is_one_document(tmp_path):
    result = _invoke(["tail", "--path", str(tmp_path / "nope.jsonl"), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["total"] == 0
    assert payload["count"] == 0
    assert payload["events"] == []


def test_json_empty_file_is_one_document(tmp_path):
    empty = tmp_path / "audit.jsonl"
    empty.write_text("", encoding="utf-8")
    result = _invoke(["tail", "--path", str(empty), "--json"])
    assert result.exit_code == 0
    assert json.loads(result.stdout)["count"] == 0


# ─────────────────────── honest non-failure states ───────────────────────


def test_missing_file_message_exits_zero(tmp_path):
    result = _invoke(["tail", "--path", str(tmp_path / "nope.jsonl")])
    assert result.exit_code == 0
    assert "nothing recorded yet" in result.stdout


def test_empty_file_message_exits_zero(tmp_path):
    empty = tmp_path / "audit.jsonl"
    empty.write_text("\n\n", encoding="utf-8")
    result = _invoke(["tail", "--path", str(empty)])
    assert result.exit_code == 0
    assert "empty" in result.stdout


def test_default_path_resolves_at_call_time(tmp_path, monkeypatch):
    """No --path → config_dir()/runtime/audit.jsonl, resolved at CALL time
    (frozen-path tripwire contract)."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    from navig.commands.audit import _resolve_audit_path

    assert _resolve_audit_path(None) == tmp_path / "runtime" / "audit.jsonl"


# ─────────────────────────── CLI wiring ───────────────────────────


def test_audit_is_registered_with_help_dict_and_help_page():
    from pathlib import Path

    import navig
    from navig.cli.help_dictionaries import HELP_REGISTRY
    from navig.cli.registration import _EXTERNAL_CMD_MAP

    assert _EXTERNAL_CMD_MAP["audit"] == ("navig.commands.audit", "audit_app")
    assert "tail" in HELP_REGISTRY["audit"]["commands"]
    assert (Path(navig.__file__).parent / "help" / "audit.md").exists()


def test_audit_group_has_explicit_callback():
    """Without a group callback Typer collapses a single-command app and
    `navig audit tail` stops parsing (the T-068 ledger gotcha)."""
    assert audit_app.registered_callback is not None
