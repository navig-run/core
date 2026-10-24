"""/status card: data collection, rendering and the new detail cards.

The card must survive a missing initiative artifact (Genesis not initialised yet, no
brain lease cached, no self-programming receipts) — that is the normal state of a
fresh install, and it must cost a line, never the card.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path

from navig.telegram import status_card as SC


def _snapshot(**over):
    base = {
        "chat_id": 1,
        "user_id": 2,
        "now": datetime(2026, 10, 22, 8, 7, tzinfo=timezone.utc),
        "host": "outpost-server",
        "space": "grabnite",
        "space_info": {},
        "project": {"name": "grabnite", "root": "/home/u/grabnite", "branch": "main"},
        "genesis": {
            "node_id": "navig_780188",
            "name": "outpost-server",
            "born_at": "2026-03-29T00:52:08+00:00",
            "engine": "2.0.0",
            "url": "https://navig.run/node/navig_780188",
        },
        "brain": {
            "known": True,
            "hostname": "outpost-server",
            "role": "all-in-one",
            "epoch": 1,
            "owner_id": "brn-7385bc487e4b6ba2",
            "age_s": 12.0,
            "ttl": 300.0,
            "lease_left_s": 288.0,
            "is_local": True,
            "cache_age_s": 5.0,
            "cache_fresh": True,
            "stale": False,
            "url": "https://navig-lighthouse.example/api/ownership",
        },
        "operator": {"state": "active", "time_of_day": "morning", "last_seen_age_s": 12.0},
        "self_prog": {
            "path": "/home/u/.navig/workspace/SELF-PROGRAMMING-RECEIPTS.md",
            "count": 2,
            "last_cycle": {"number": 1, "date": "2026-10-22"},
            "last_commit": "01cc18585f",
        },
        "tier": "auto",
        "persona": "default",
        "work": {"reminders": 2, "tasks_due": 3, "inbox_unread": 5},
        "system": {"version": "3.27.7", "uptime_s": 11_520.0, "cron_jobs": 26},
        "readiness": {"state": "ready", "score": 100, "issues": []},
    }
    base.update(over)
    return base


# ── rendering ───────────────────────────────────────────────────────────────


def test_the_header_is_stable_for_the_refresh_regression():
    """`test_telegram_refresh_keeps_formatting` pins this exact prefix."""
    assert SC.CARD_HEADER == "🧭 <b>NAVIG Status</b> — <i>Session Snapshot</i>"


def test_render_names_the_project_separately_from_the_space():
    text = SC.render(_snapshot())
    assert "Space    <code>grabnite</code>" in text
    assert "Project  <code>grabnite · main</code>" in text
    assert "Space" not in text.split("Project")[1], "the project line must not repeat the space"


def test_render_surfaces_brain_lease_and_genesis_identity():
    text = SC.render(_snapshot())
    assert "Authority <code>outpost-server</code>" in text
    assert "epoch 1" in text
    assert "lease 4m 48s left" in text  # 288s humanised
    assert "navig_780188" in text and "born 29 Mar 2026" in text


def test_render_surfaces_self_state_and_the_last_self_programming_cycle():
    text = SC.render(_snapshot())
    assert "State    <code>active</code>" in text
    assert "2</code> self-programming" in text
    assert "01cc1858" in text


def test_render_shows_work_and_system_stats():
    text = SC.render(_snapshot())
    assert "Reminders <code>2</code>" in text
    assert "Tasks due <code>3</code>" in text
    assert "Unread <code>5</code>" in text
    assert "Uptime <code>3h 12m</code>" in text
    assert "v3.27.7" in text and "cron <code>26</code> jobs" in text


def test_the_readiness_bar_is_clamped_to_ten_cells():
    for score in (0, 37, 100, 250, -50):
        text = SC.render(_snapshot(readiness={"state": "ready", "score": score, "issues": []}))
        bar = text.split("<code>")[-2] if "<code>" in text else ""
        # The bar line: `<code>██████████</code> 100% <code>ready</code>`
        row = [ln for ln in text.splitlines() if "<b>Setup</b>" in ln][0]
        assert row.count("█") + row.count("░") == 10, (score, row)
        assert "░" * 11 not in row and "█" * 11 not in row


def test_a_card_with_no_initiative_artifacts_still_renders():
    text = SC.render(
        _snapshot(genesis={}, brain={"known": False}, operator={}, self_prog={}, project={})
    )
    assert SC.CARD_HEADER in text
    assert "🧠 Brain" not in text
    assert "🌱 Genesis" not in text
    assert "Space    <code>grabnite</code>" in text


def test_the_card_stays_under_the_telegram_limit():
    assert len(SC.render(_snapshot())) < 4096


def test_keyboard_carries_the_new_detail_buttons():
    rows = SC.keyboard(_snapshot())
    callbacks = [btn["callback_data"] for row in rows for btn in row]
    for wanted in ("nav:open:status", "nav:brain", "nav:self", "nav:genesis", "nav:cmd:/spaces"):
        assert wanted in callbacks, f"{wanted} missing from {callbacks}"


def test_every_status_button_key_ships_in_the_operator_locales():
    """The labels are resolved, not literals — so the keys must actually exist.

    A missing key falls back to the fallback string (English), which is how a
    localized surface rots one button at a time. This account runs in Russian.
    """
    from pathlib import Path

    locales = Path(SC.__file__).resolve().parents[1] / "locales"
    keys = {
        "status.button.refresh",
        "status.button.briefing",
        "status.button.reminders",
        "status.button.spaces",
        "status.button.brain",
        "status.button.self",
        "status.button.genesis",
        "status.button.plans",
        "status.button.home",
    }
    for name in ("en.json", "ru.json"):
        data = json.loads((locales / name).read_text(encoding="utf-8"))
        missing = sorted(keys - set(data))
        assert not missing, f"{name} is missing {missing}"
    for key in sorted(keys):
        label = SC.button_label(key, "")
        assert label and label != key, f"{key} did not resolve to a shipped label"


def test_recovery_buttons_come_first():
    snapshot = _snapshot(
        readiness={
            "state": "partial",
            "score": 50,
            "issues": [
                {"code": "ffmpeg", "summary": "Install ffmpeg", "command": "navig init --fix ffmpeg"},
                {"code": "ssh", "summary": "Configure SSH", "command": "navig init --fix ssh"},
                {"code": "extra", "summary": "Third", "command": "navig init --fix extra"},
            ],
        }
    )
    rows = SC.keyboard(snapshot)
    assert rows[0][0]["callback_data"] == "stfix:ffmpeg"
    assert rows[1][0]["callback_data"] == "stfix:ssh"
    assert "stfix:extra" not in [btn["callback_data"] for row in rows for btn in row]


# ── artifact readers ────────────────────────────────────────────────────────


def _config_dir() -> Path:
    from navig.platform.paths import config_dir

    return Path(config_dir())


def test_genesis_identity_reads_the_immutable_document():
    cfg = _config_dir()
    (cfg / "genesis.json").write_text(
        json.dumps(
            {
                "nodeId": "navig_780188",
                "name": "NEURON",
                "bornAt": "2026-03-29T00:52:08+00:00",
                "engineVersion": "2.0.0",
                "qrTarget": "https://navig.run/node/navig_780188",
            }
        ),
        encoding="utf-8",
    )
    genesis = SC.genesis_identity()
    assert genesis["node_id"] == "navig_780188"
    assert genesis["name"] == "NEURON"
    assert SC.genesis_born_label(genesis["born_at"]) == "29 Mar 2026"


def test_brain_authority_reports_the_lease_and_marks_it_local():
    cfg = _config_dir()
    (cfg / "brain").mkdir(parents=True, exist_ok=True)
    now = time.time()
    (cfg / "brain" / "authority-cache.json").write_text(
        json.dumps(
            {
                "url": "https://example/api/ownership",
                "fetched_at": now,
                "record": {
                    "owner_id": "brn-abc123",
                    "role": "all-in-one",
                    "epoch": 2,
                    "heartbeat_at": now - 10,
                    "ttl": 300,
                    "hostname": "outpost-server",
                },
            }
        ),
        encoding="utf-8",
    )
    brain = SC.brain_authority("outpost-server")
    assert brain["known"] is True
    assert brain["is_local"] is True
    assert brain["cache_fresh"] is True
    assert 280 <= brain["lease_left_s"] <= 291
    assert brain["stale"] is False

    (cfg / "brain" / "authority-cache.json").write_text(
        json.dumps(
            {
                "fetched_at": now,
                "record": {"heartbeat_at": now - 400, "ttl": 300, "hostname": "other"},
            }
        ),
        encoding="utf-8",
    )
    stale = SC.brain_authority("outpost-server")
    assert stale["stale"] is True and stale["is_local"] is False


def test_an_old_snapshot_cache_is_not_called_a_stale_lease():
    """A non-owner install may not have refreshed the cache for hours.

    That is our snapshot being old, not evidence the brain stopped heartbeating —
    the card must say "snapshot N ago", never "stale".
    """
    cfg = _config_dir()
    (cfg / "brain").mkdir(parents=True, exist_ok=True)
    now = time.time()
    (cfg / "brain" / "authority-cache.json").write_text(
        json.dumps(
            {
                "fetched_at": now - 5_000,
                "record": {"heartbeat_at": now - 5_000, "ttl": 300, "hostname": "outpost-server"},
            }
        ),
        encoding="utf-8",
    )
    brain = SC.brain_authority("outpost-server")
    assert brain["cache_fresh"] is False
    assert brain["stale"] is False, "an old snapshot must not claim the lease is stale"
    text = SC.render(_snapshot(brain=brain))
    assert "snapshot 1h 23m ago" in text
    assert "⚠️ stale" not in text
    detail = SC.render_detail("brain", _snapshot(brain=brain))
    assert "Snapshot" in detail and "⚠️ <b>stale</b>" not in detail


def test_self_programming_parses_the_receipts_ledger():
    cfg = _config_dir()
    (cfg / "workspace").mkdir(parents=True, exist_ok=True)
    (cfg / "workspace" / "SELF-PROGRAMMING-RECEIPTS.md").write_text(
        "# Self-programming receipts\n\n"
        "## Cycle 1 — 2026-10-22\n\n**Commit.** `01cc18585f` — fix(api)\n\n"
        "## Cycle 0 — 2026-10-22 (pre-directive)\n\n**Commit.** `98e380daf7`\n",
        encoding="utf-8",
    )
    prog = SC.self_programming()
    assert prog["count"] == 2
    assert prog["last_cycle"] == {"number": 1, "date": "2026-10-22"}
    assert prog["last_commit"] == "01cc18585f"


# ── project detection ────────────────────────────────────────────────────────


def test_project_of_finds_a_git_root_and_its_branch(tmp_path):
    root = tmp_path / "grabnite"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("ref: refs/heads/feature/x\n", encoding="utf-8")
    (root / "apps").mkdir()

    project = SC.project_of(root / "apps")
    assert project == {"name": "grabnite", "root": str(root), "branch": "feature/x"}


def test_project_of_reads_a_worktree_git_file(tmp_path):
    main = tmp_path / "repo"
    (main / ".git").mkdir(parents=True)
    (main / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {main / '.git'}\n", encoding="utf-8")

    project = SC.project_of(wt)
    assert project["name"] == "repo"
    assert project["branch"] == "main"


def test_project_of_plain_folder_is_empty(tmp_path):
    import shutil
    import tempfile

    # `tmp_path` lives inside THIS repo (core/.dev/tmp), which would legitimately
    # resolve to the repo root. A truly outside folder has no git ancestor.
    outside = Path(tempfile.mkdtemp(prefix="navig-not-a-repo-"))
    try:
        (outside / "plain").mkdir()
        assert SC.project_of(outside / "plain") == {}
    finally:
        shutil.rmtree(outside, ignore_errors=True)


def test_a_folder_inside_this_repo_resolves_to_it(tmp_path):
    (tmp_path / "sub").mkdir()
    project = SC.project_of(tmp_path / "sub")
    assert project.get("root"), "a nested folder must resolve to the checkout it lives in"


def test_project_of_detached_head_names_the_commit(tmp_path):
    root = tmp_path / "grabnite"
    (root / ".git").mkdir(parents=True)
    (root / ".git" / "HEAD").write_text("deadbeef00c0ffee\n", encoding="utf-8")
    project = SC.project_of(root)
    assert project["branch"] == "detached deadbeef"


def test_space_details_falls_back_to_a_checkout_named_after_the_space(tmp_path, monkeypatch):
    """A space folder that is only a manifest must not hide the project it names."""
    home = tmp_path / "home"
    project = home / "vibe" / "grabnite"
    (project / ".git").mkdir(parents=True)
    (project / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    monkeypatch.setattr(SC.Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr("navig.spaces.registry.canonical_root", lambda *a, **k: None)

    info = SC.space_details("grabnite")
    assert info["project"]["name"] == "grabnite"
    assert info["project"]["branch"] == "main"


def test_uptime_falls_back_to_the_pid_file_age(tmp_path, monkeypatch):
    import os

    cfg = tmp_path / "cfg"
    cfg.mkdir()
    pid = cfg / "supervisor.pid"
    pid.write_text("1234", encoding="utf-8")
    old = time.time() - 120
    os.utime(pid, (old, old))
    monkeypatch.setattr(SC, "_config_dir", lambda: cfg)
    monkeypatch.setattr(
        "navig.daemon.supervisor.NavigDaemon.read_state", staticmethod(lambda: {})
    )

    stats = SC.system_stats()
    assert stats["uptime_s"] is not None and 110 <= stats["uptime_s"] <= 130


# ── detail cards ────────────────────────────────────────────────────────────


def test_brain_detail_card_shows_epoch_lease_and_source():
    text = SC.render_detail("brain", _snapshot())
    assert "🧠 <b>Brain</b>" in text
    assert "Epoch     <code>1</code>" in text
    assert "example/api/ownership" in text


def test_genesis_detail_card_shows_the_node_url_and_readiness():
    text = SC.render_detail("genesis", _snapshot())
    assert "🌱 <b>Genesis</b>" in text
    assert "navig.run/node/navig_780188" in text
    assert "Setup" in text and "100%" in text


def test_self_detail_card_shows_latest_cycle():
    text = SC.render_detail("self", _snapshot())
    assert "🧘 <b>Self</b>" in text
    assert "Latest   cycle 1" in text
    assert "01cc1858" in text


def test_unknown_detail_falls_back_to_the_session_card():
    assert SC.render_detail("nope", _snapshot()) == SC.render(_snapshot())

def test_delivery_health_reads_mode_unconfirmed_and_last_send(tmp_path, monkeypatch):
    monkeypatch.setattr(SC, "_config_dir", lambda: tmp_path)
    (tmp_path / "brain").mkdir()
    (tmp_path / "cache").mkdir()
    (tmp_path / "brain" / "telegram_mode.json").write_text(
        '{"mode": "polling", "transitions": [{}, {}]}', encoding="utf-8"
    )
    sent = tmp_path / "cache" / "telegram_sent.jsonl"
    sent.write_text("{}\n", encoding="utf-8")
    import os
    import time

    age = 120.0
    os.utime(sent, (time.time() - age, time.time() - age))

    from navig.telegram import receipts

    monkeypatch.setattr(receipts, "_path", lambda: tmp_path / "receipts.db")
    receipts.record_received(
        {"update_id": 41, "message": {"message_id": 1, "chat": {"id": 2}}}
    )

    info = SC.delivery_health()
    assert info["mode"] == "polling"
    assert info["transitions"] == 2
    assert info["pending"] == 1
    assert info["last_send_age_s"] >= age - 5


def test_render_shows_the_delivery_line():
    snap = _snapshot()
    snap["delivery"] = {"mode": "polling", "pending": 2, "last_send_age_s": 60}
    text = SC.render(snap)

    assert "Delivery" in text
    assert "unconfirmed" in text
    assert "polling" in text
