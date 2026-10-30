"""navig backup watch — the newest backup must be young; alerts fire once per edge."""

from __future__ import annotations

import json
import os
import time

from typer.testing import CliRunner

from navig import backup_watch as bw


def _file(folder, name, age_h):
    p = folder / name
    p.write_bytes(b"x")
    t = time.time() - age_h * 3600
    os.utime(p, (t, t))
    return p


def test_fresh_and_stale_by_newest_matching_file(tmp_path):
    _file(tmp_path, "gitea-dump-old.zip", 200)
    _file(tmp_path, "gitea-dump-new.zip", 5)
    _file(tmp_path, "notes.txt", 0)  # newer, but not a backup
    v = bw.check(tmp_path, name="gitea", max_age_hours=30, pattern="gitea-dump-*.zip")
    assert v.ok and v.newest == "gitea-dump-new.zip" and v.age_hours == 5.0
    v = bw.check(tmp_path, name="gitea", max_age_hours=4, pattern="gitea-dump-*.zip")
    assert not v.ok and "5 h old" in v.reason


def test_unreachable_and_empty_are_stale(tmp_path):
    assert not bw.check(tmp_path / "nope", name="x", max_age_hours=30).ok
    v = bw.check(tmp_path, name="x", max_age_hours=30, pattern="*.zip")
    assert not v.ok and "no file" in v.reason


def test_latch_fires_once_per_edge(tmp_path):
    state = tmp_path / "s.json"
    fresh = bw.Verdict("g", "p", True, "fresh")
    stale = bw.Verdict("g", "p", False, "old")
    assert bw.latch(fresh, state) is None
    assert bw.latch(stale, state) == "stale"
    assert bw.latch(stale, state) is None  # a daily run never repeats the alarm
    assert bw.latch(fresh, state) == "recovered"
    assert bw.latch(fresh, state) is None
    assert json.loads(state.read_text())["g"]["ok"] is True


def test_first_run_stale_alerts(tmp_path):
    assert bw.latch(bw.Verdict("g", "p", False, "old"), tmp_path / "s.json") == "stale"


def test_cli_exit_codes_and_alert(tmp_path, monkeypatch):
    from navig.commands.backup import backup_app

    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    sent = []

    async def fake_dispatch(type_key, title, body="", **kw):
        sent.append((type_key, title, kw.get("priority")))
        return {}

    import navig.notify.router as router

    monkeypatch.setattr(router, "dispatch", fake_dispatch)
    folder = tmp_path / "b"
    folder.mkdir()
    _file(folder, "dump.zip", 100)
    runner = CliRunner()
    r = runner.invoke(backup_app, ["watch", str(folder), "--name", "gitea", "--notify", "--json"])
    assert r.exit_code == 1, r.output
    assert sent == [("system_alert", "Backup stale: gitea", "high")]
    r = runner.invoke(backup_app, ["watch", str(folder), "--name", "gitea", "--notify"])
    assert r.exit_code == 1 and len(sent) == 1  # no repeat
    _file(folder, "dump2.zip", 1)
    r = runner.invoke(backup_app, ["watch", str(folder), "--name", "gitea", "--notify"])
    assert r.exit_code == 0 and sent[-1][1] == "Backup fresh again: gitea"


def test_cli_keeps_the_edge_when_no_channel_delivered(tmp_path, monkeypatch):
    """Every channel failing must not consume the edge — the next run alerts again."""
    from navig.commands.backup import backup_app

    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    results = [
        {"channels": [{"channel": "telegram", "ok": False, "detail": "down"}]},
        {"channels": [{"channel": "telegram", "ok": True, "detail": "sent"}]},
    ]
    sent = []

    async def fake_dispatch(type_key, title, body="", **kw):
        sent.append(title)
        return results[len(sent) - 1] if len(sent) <= len(results) else {}

    import navig.notify.router as router

    monkeypatch.setattr(router, "dispatch", fake_dispatch)
    folder = tmp_path / "b"
    folder.mkdir()
    _file(folder, "dump.zip", 100)
    runner = CliRunner()
    args = ["watch", str(folder), "--name", "gitea", "--notify"]
    assert runner.invoke(backup_app, args).exit_code == 1
    assert sent == ["Backup stale: gitea"]
    assert runner.invoke(backup_app, args).exit_code == 1
    assert sent == ["Backup stale: gitea"] * 2  # retried: the first alert reached nobody
    assert runner.invoke(backup_app, args).exit_code == 1
    assert len(sent) == 2  # delivered on the second try, so no repeat now


def test_cli_json_stays_parseable_when_the_alert_fails(tmp_path, monkeypatch):
    """A delivery failure under --json goes INTO the payload, never as prose on stdout."""
    from navig.commands.backup import backup_app

    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))

    async def failing_dispatch(type_key, title, body="", **kw):
        raise RuntimeError("router offline")

    import navig.notify.router as router

    monkeypatch.setattr(router, "dispatch", failing_dispatch)
    folder = tmp_path / "b"
    folder.mkdir()
    _file(folder, "dump.zip", 100)
    r = CliRunner().invoke(
        backup_app, ["watch", str(folder), "--name", "gitea", "--notify", "--json"]
    )
    assert r.exit_code == 1, r.output
    doc = json.loads(r.stdout)
    assert doc["edge"] == "stale"
    assert "router offline" in doc["notify_error"]
