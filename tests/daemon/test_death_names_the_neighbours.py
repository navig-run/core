"""When the daemon dies, the incident says who was around — without leaking.

2026-09-14: the daemon died at 20:01 and `navig cdp stop --all` ran at 20:01:13
from another session. Correlating the two took an hour by hand, because the
audit rows carried `session_id: None` and the incident carried nothing about
its surroundings. Both halves are fixed here, and one property dominates: the
incident must never copy a command's ARGUMENTS — `navig config set
gateway.auth.token <secret>` is an ordinary audited command, and the incident
reaches Telegram.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

from navig.daemon import supervisor as sup

FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _iso(dt: datetime) -> str:
    return dt.strftime(FMT)


class _Audit:
    """Stands in for the audit store: a fixed list of rows, plus a record of the
    window it was asked for."""

    def __init__(self, rows):
        self.rows = rows
        self.asked: list[tuple[str, str]] = []

    def events_between(self, start_iso, end_iso, *, limit=20):
        self.asked.append((start_iso, end_iso))
        return [r for r in self.rows if start_iso <= r["timestamp"] <= end_iso][:limit]


def _row(when: datetime, command: str, session: str | None = None, status="success"):
    return {
        "timestamp": _iso(when),
        "action": "local_command",
        "details": json.dumps({"command": command, "exit_code": 0}),
        "session_id": session,
        "status": status,
    }


# ── (b) the incident names the neighbours ────────────────────────────────────


def test_commands_in_the_window_are_named_by_verb_and_session(monkeypatch):
    now = datetime.now(timezone.utc)
    audit = _Audit(
        [
            _row(now - timedelta(minutes=2), "navig cdp stop --all", "9f39fd34-abcd"),
            _row(now - timedelta(minutes=1), "navig doctor", "fe25d07e-1234", status="failed"),
        ]
    )
    monkeypatch.setattr("navig.store.audit.get_audit_store", lambda: audit)

    out = sup.NavigDaemon._commands_before_now(_iso(now - timedelta(minutes=10)))

    assert [c["command"] for c in out] == ["navig cdp stop", "navig doctor"]
    assert [c["session"] for c in out] == ["9f39fd34", "fe25d07e"]
    assert out[1]["status"] == "failed"


def test_arguments_never_reach_the_incident(monkeypatch):
    """⚠ The property that dominates. A secret typed on the command line is in
    the audit's `details.command`; the incident is pushed to Telegram."""
    now = datetime.now(timezone.utc)
    audit = _Audit(
        [
            _row(
                now - timedelta(minutes=1), "navig config set gateway.auth.token SUPER-SECRET-VALUE"
            ),
            _row(now - timedelta(minutes=1), "navig vault put openai sk-live-abcdef"),
        ]
    )
    monkeypatch.setattr("navig.store.audit.get_audit_store", lambda: audit)

    out = sup.NavigDaemon._commands_before_now(None)

    flat = json.dumps(out)
    assert "SUPER-SECRET" not in flat
    assert "sk-live" not in flat
    assert out[0]["command"] == "navig config set"
    assert out[1]["command"] == "navig vault put"


def test_the_window_never_reaches_before_the_dead_daemon_started(monkeypatch):
    """Anything that ran before the daemon was born cannot have killed it."""
    now = datetime.now(timezone.utc)
    born = now - timedelta(minutes=3)
    audit = _Audit(
        [
            _row(now - timedelta(minutes=12), "navig gateway restart"),  # the PREVIOUS life
            _row(now - timedelta(minutes=1), "navig cdp stop --all"),
        ]
    )
    monkeypatch.setattr("navig.store.audit.get_audit_store", lambda: audit)

    out = sup.NavigDaemon._commands_before_now(_iso(born))

    assert [c["command"] for c in out] == ["navig cdp stop"]
    start_asked, _ = audit.asked[0]
    assert start_asked >= _iso(born)


def test_an_unavailable_audit_store_yields_nothing_not_an_error(monkeypatch):
    """The incident must still be recorded when the audit db cannot be opened."""

    def _boom():
        raise RuntimeError("audit.db locked")

    monkeypatch.setattr("navig.store.audit.get_audit_store", _boom)

    assert sup.NavigDaemon._commands_before_now(None) == []


def test_the_death_incident_carries_the_neighbours_end_to_end(tmp_path, monkeypatch):
    """Through the real reap path: stale pid file → incident with nearby_commands."""
    import subprocess
    import sys

    pf = tmp_path / "supervisor.pid"
    monkeypatch.setattr(sup, "PID_FILE", pf)
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    pf.write_text(str(p.pid), encoding="utf-8")
    # The pid file's mtime is "when that daemon started", and the window never
    # reaches before it. A file written this instant would make the window
    # zero-width — the real shape is a daemon that started, then a command ran,
    # then it died, then the death was noticed.
    born = datetime.now(timezone.utc) - timedelta(minutes=2)
    os.utime(pf, (born.timestamp(), born.timestamp()))

    now = datetime.now(timezone.utc)
    audit = _Audit([_row(now - timedelta(seconds=30), "navig cdp stop --all", "9f39fd34")])
    monkeypatch.setattr("navig.store.audit.get_audit_store", lambda: audit)
    recorded: list[tuple[str, dict]] = []
    from navig.core import incidents

    monkeypatch.setattr(incidents, "record", lambda ev, **d: recorded.append((ev, d)))

    assert sup.NavigDaemon.is_running() is False

    ((event, data),) = recorded
    assert event == "daemon_died_ungracefully"
    assert data["nearby_commands"][0]["command"] == "navig cdp stop"


def test_doctor_renders_the_neighbours_on_the_row():
    from navig.core import incidents

    entry = {
        "ts": 1789409678.0,
        "event": incidents.DAEMON_DIED_UNGRACEFULLY,
        "data": {
            "previous_pid": 119828,
            "nearby_commands": [
                {
                    "at": "2026-09-14T18:01:13.157829Z",
                    "command": "navig cdp stop",
                    "session": "9f39fd34",
                    "status": "success",
                },
            ],
        },
    }
    text = incidents.describe(entry)

    assert "navig cdp stop (9f39fd34) @ 18:01:13Z" in text


# ── (a) audit rows carry the session ─────────────────────────────────────────


def test_cli_audit_rows_carry_the_callers_session(monkeypatch, tmp_path):
    """The recorder wrote `session_id=None` on every CLI row. It now uses the
    same identity the repo guard and host locks use, so two agents on one
    machine are told apart in the audit. Driven through the real recorder, the
    way tests/commands/test_history_dry_run.py does."""
    monkeypatch.setenv("NAVIG_SESSION_ID", "agent-A-0001")
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    captured: list[dict] = []

    class _Store:
        def log_event(self, **kw):
            captured.append(kw)

    monkeypatch.setattr("navig.store.audit.get_audit_store", lambda: _Store())

    import navig.operation_recorder as mod
    from navig.operation_recorder import OperationType, get_operation_recorder

    monkeypatch.setattr(mod, "_recorder", None, raising=False)
    rec = get_operation_recorder()
    rec.history_file = tmp_path / "operations.jsonl"
    rec.history_file.parent.mkdir(parents=True, exist_ok=True)

    record = rec.start_operation(command="navig doctor", operation_type=OperationType.LOCAL_COMMAND)
    rec.complete_operation(record, success=True)

    assert captured, "no audit row was written"
    assert captured[-1].get("session_id") == "agent-A-0001"
    assert "navig doctor" in captured[-1]["details"]["command"]


def test_the_recorder_passes_session_id_at_the_call_site():
    """A structural pin independent of the recorder's constructor shape: the
    audit write must name `session_id=` and resolve it from host_lock."""
    import ast
    import inspect

    from navig import operation_recorder as orec

    src = inspect.getsource(orec)
    tree = ast.parse(src)
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "log_event"
    ]
    assert calls, "the recorder no longer writes an audit row at all"
    assert any(any(k.arg == "session_id" for k in c.keywords) for c in calls), (
        "the audit write dropped session_id= — every CLI row is anonymous again"
    )
    assert "host_lock import session_id" in src
