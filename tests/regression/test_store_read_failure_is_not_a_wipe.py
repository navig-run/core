"""A failed READ must never become a destructive WRITE — every remaining store.

`45d9bebbf` closed this in `TriggerManager` and `MCPManager` after reproducing it (3
triggers in, 1 out). `bd1c68530` closed four daemon-side stores. This file now also
covers the last two the measurement listed — `UserStateTracker` and
`SystemEventQueue` — so the user-data half of the class is finished.

    _load*()                      _save*()
      self._x = {}  /  populates    writes self._x, atomically, over the real file
      except: log-and-return        <- so an EMPTY or PARTIAL set replaces the data

Every store here is DAEMON-side, which is what makes them nastier than the CLI ones:
there is no exit code and no ✗ on anyone's terminal. `GoalPlanner` swallowed its load
failure into a DebugLogger line; the rest into a daemon log. The autonomy loop then
saved on its own schedule and the operator had no way to know anything had happened.

Two of them lose more than history. `agent/session_store` reset the session's token
and cost ACCOUNTING (`append()` does `total_cost += ...` on whatever the load
returned). `UserStateTracker` reset `autonomy_level`, `quiet_hours_*` and
`notifications_enabled` — that does not merely lose settings, it CHANGES HOW THE
AGENT BEHAVES.

Return types are deliberately UNCHANGED: `add_goal` returns a goal id with no sensible
refusal value, and these run inside the Heart's loop where raising would be worse than
declining. The bug is the wipe, not the signature. Visibility comes from
`incidents.record(STORE_WRITE_REFUSED, ...)` instead — the seam this repo already uses
for "self-healing that must not be silent", which surfaces in `navig doctor` ->
Config Health and pushes through the notify path.

Each store is pinned in BOTH directions: a failed read must not destroy, and a NORMAL
save must still work — otherwise "refuse always" would satisfy every test here and the
stores would simply stop persisting.
"""

from __future__ import annotations

import builtins
import json
import pathlib

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def no_incident_side_effects(monkeypatch):
    """Capture recorded incidents instead of writing to the real config dir."""
    from navig.core import incidents

    seen: list[tuple[str, dict]] = []
    monkeypatch.setattr(incidents, "record", lambda event, **d: seen.append((event, d)))
    return seen


def _lock_path(monkeypatch, filename):
    """Make ONE file unreadable at every seam any version of the code might use.

    `Path.read_text` and `builtins.open` are what the PRE-FIX code called;
    `read_text_retrying` (which the fixed code calls) funnels into `Path.read_text`
    anyway. Patching only the retrying reader would give a fault the old code cannot
    hit — proving the fix works, but never demonstrating the bug, and in one case
    passing in BOTH directions. Filtered by filename so unrelated reads still work.
    """
    real_read_text = pathlib.Path.read_text
    real_open = builtins.open

    def _locked_read_text(self, *a, **kw):
        if self.name == filename:
            raise PermissionError(13, "The process cannot access the file")
        return real_read_text(self, *a, **kw)

    def _locked_open(file, *a, **kw):
        if str(file).endswith(filename):
            raise PermissionError(13, "The process cannot access the file")
        return real_open(file, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "read_text", _locked_read_text)
    monkeypatch.setattr(builtins, "open", _locked_open)

    def _unlock():
        """Release the lock — the ASSERTIONS have to read the file we just locked."""
        monkeypatch.setattr(pathlib.Path, "read_text", real_read_text)
        monkeypatch.setattr(builtins, "open", real_open)

    return _unlock


def test_the_refusal_is_visible_in_doctor_and_names_the_file(tmp_path, monkeypatch):
    """The whole point of recording it: `navig doctor` must show WHICH store.

    Three stores share STORE_WRITE_REFUSED, so a description with no address leaves
    the operator with a problem and nowhere to go.
    """
    from navig.core import incidents

    entry = {
        "ts": 0,
        "event": incidents.STORE_WRITE_REFUSED,
        "data": {"store": "goals", "path": "/x/goals.json"},
    }
    rendered = incidents.describe(entry)

    assert "could not be READ" in rendered, "the raw event id leaked instead of prose"
    assert "/x/goals.json" in rendered, "the operator cannot tell WHICH store to fix"


# ── GoalPlanner ───────────────────────────────────────────────────────────


def _planner(tmp_path):
    from navig.agent.goals import GoalPlanner

    return GoalPlanner(storage_dir=tmp_path)


def test_a_locked_goals_file_is_not_overwritten(tmp_path, monkeypatch, no_incident_side_effects):
    planner = _planner(tmp_path)
    planner.add_goal("keep me")
    planner.add_goal("and me")
    original = planner.goals_file.read_text(encoding="utf-8")

    unlock = _lock_path(monkeypatch, "goals.json")
    fresh = _planner(tmp_path)
    fresh.add_goal("added while the file was unreadable")

    unlock()
    assert planner.goals_file.read_text(encoding="utf-8") == original, (
        "one transient lock destroyed every stored goal"
    )
    assert [e for e, _ in no_incident_side_effects] == ["store_write_refused"], (
        "the refusal was silent — the trap the incident log exists for"
    )


def test_a_malformed_goal_entry_does_not_drop_the_others(tmp_path, no_incident_side_effects):
    """A partial load is worse than an empty one: it looks plausible on disk."""
    planner = _planner(tmp_path)
    planner.add_goal("keep me")

    broken = json.dumps({"goals": [{"id": "ok", "description": "d"}, 12345]})
    planner.goals_file.write_text(broken, encoding="utf-8")

    fresh = _planner(tmp_path)
    fresh.add_goal("new")
    assert planner.goals_file.read_text(encoding="utf-8") == broken


def test_goals_still_persist_normally(tmp_path, no_incident_side_effects):
    """Anti-vacuity — refusing always would satisfy both tests above."""
    planner = _planner(tmp_path)
    gid = planner.add_goal("real goal")

    reread = _planner(tmp_path)
    assert gid in {g.id for g in reread.list_goals()}
    assert no_incident_side_effects == [], "a healthy save recorded an incident"


# ── RemediationEngine ─────────────────────────────────────────────────────


def _engine(tmp_path):
    from navig.agent.remediation import RemediationEngine

    return RemediationEngine(config_dir=tmp_path, log_dir=tmp_path / "logs")


def test_a_locked_remediation_file_is_not_overwritten(
    tmp_path, monkeypatch, no_incident_side_effects
):
    engine = _engine(tmp_path)
    engine.schedule_restart_sync("disk", "disk over 90%")
    original = engine.actions_file.read_text(encoding="utf-8")

    unlock = _lock_path(monkeypatch, "remediation_actions.json")
    fresh = _engine(tmp_path)
    fresh.schedule_restart_sync("cpu", "load spike")

    unlock()
    assert engine.actions_file.read_text(encoding="utf-8") == original, (
        "the audit trail of what the agent did to real machines was destroyed"
    )
    assert [e for e, _ in no_incident_side_effects] == ["store_write_refused"]


def test_remediation_still_persists_normally(tmp_path, no_incident_side_effects):
    """Anti-vacuity."""
    engine = _engine(tmp_path)
    engine.schedule_restart_sync("disk", "disk over 90%")
    assert engine.actions_file.exists()

    reread = _engine(tmp_path)
    assert reread._actions, "a healthy round-trip lost the action"
    assert no_incident_side_effects == []


# ── agent SessionStore (a DIFFERENT class of the same name) ───────────────
#
# `navig/agent/session_store.py` and `navig/gateway/session_store.py` are two
# distinct `SessionStore` classes. Fixing one says nothing about the other — the
# same two-classes-one-name trap as the RuntimeStore wipe. This one is the sharpest
# instance of the whole class: it did not wait for a later save, it built blank
# metadata and persisted it IMMEDIATELY on a failed read.


def _agent_store(tmp_path, session_id="s1"):
    from navig.agent.session_store import SessionStore as AgentSessionStore

    return AgentSessionStore(session_id=session_id, base_dir=tmp_path)


def _entry(tokens=100, cost=0.5):
    from navig.agent.session_store import SessionEntry

    return SessionEntry(role="user", content="hi", tokens_used=tokens, cost=cost)


def test_an_unreadable_session_meta_does_not_reset_cost_accounting(
    tmp_path, monkeypatch, no_incident_side_effects
):
    """`append()` does `total_cost += ...` on whatever the load returned.

    So a blank load did not merely lose the numbers — it wrote near-zero back over
    them, restarting the session's token and cost accounting silently.
    """
    store = _agent_store(tmp_path)
    store.append(_entry(tokens=100, cost=0.5))
    store.append(_entry(tokens=200, cost=1.5))
    original = store.meta_file.read_text(encoding="utf-8")
    assert '"total_tokens": 300' in original

    unlock = _lock_path(monkeypatch, "s1.meta.json")
    fresh = _agent_store(tmp_path)
    fresh.append(_entry(tokens=1, cost=0.01))

    unlock()
    assert store.meta_file.read_text(encoding="utf-8") == original, (
        "one transient lock reset turn_count / total_tokens / total_cost to a "
        "fresh baseline over the real numbers"
    )
    assert [e for e, _ in no_incident_side_effects] == ["store_write_refused"]


def test_agent_session_meta_still_persists_normally(tmp_path, no_incident_side_effects):
    """Anti-vacuity — and an ABSENT sidecar must still be created on first write."""
    store = _agent_store(tmp_path)
    store.append(_entry(tokens=7, cost=0.25))

    assert store.meta_file.exists(), "a first write must still create the sidecar"

    reread = _agent_store(tmp_path)
    meta = reread._load_or_create_meta()
    assert meta.total_tokens == 7, "a healthy round-trip lost the counters"
    assert no_incident_side_effects == []


# ── gateway SessionStore ──────────────────────────────────────────────────


def _store(tmp_path):
    from navig.gateway.session_store import SessionStore

    return SessionStore(persist_path=tmp_path / "sessions.json")


def _key(thread_id):
    from navig.gateway.session_store import SessionKey

    return SessionKey(channel_type="telegram", thread_id=thread_id)


def test_a_locked_session_file_is_not_overwritten(
    tmp_path, monkeypatch, no_incident_side_effects
):
    store = _store(tmp_path)
    store.get_or_create(_key("1"))
    store.save()
    path = tmp_path / "sessions.json"
    original = path.read_text(encoding="utf-8")

    unlock = _lock_path(monkeypatch, "sessions.json")
    fresh = _store(tmp_path)
    fresh.get_or_create(_key("2"))
    fresh.save()

    unlock()
    assert path.read_text(encoding="utf-8") == original, (
        "every operator's active host and conversation state was destroyed"
    )
    assert [e for e, _ in no_incident_side_effects] == ["store_write_refused"]


def test_sessions_still_persist_normally(tmp_path, no_incident_side_effects):
    """Anti-vacuity."""
    store = _store(tmp_path)
    store.get_or_create(_key("1"))
    store.save()

    reread = _store(tmp_path)
    assert "telegram:1" in reread._contexts, "a healthy round-trip lost the session"
    assert no_incident_side_effects == []


# ── UserStateTracker ──────────────────────────────────────────────────────
#
# The one whose loss CHANGES BEHAVIOUR rather than just losing history:
# `preferences` carries `autonomy_level`, `quiet_hours_*` and
# `notifications_enabled`, so a reset silently puts the agent back to acting at
# "balanced" autonomy and messaging outside the operator's quiet hours.


def _tracker(tmp_path):
    from navig.agent.proactive.user_state import UserStateTracker

    return UserStateTracker(state_dir=tmp_path)


def test_a_locked_user_state_does_not_reset_preferences(
    tmp_path, monkeypatch, no_incident_side_effects
):
    tracker = _tracker(tmp_path)
    tracker.preferences.autonomy_level = "high"
    tracker.preferences.quiet_hours_start = 1
    tracker.preferences.notifications_enabled = False
    tracker._save_state()
    original = (tmp_path / "user_state.json").read_text(encoding="utf-8")
    assert '"autonomy_level": "high"' in original

    unlock = _lock_path(monkeypatch, "user_state.json")
    fresh = _tracker(tmp_path)
    fresh._save_state()

    unlock()
    assert (tmp_path / "user_state.json").read_text(encoding="utf-8") == original, (
        "one transient lock reset autonomy_level / quiet hours / notifications to "
        "defaults — that does not merely lose settings, it changes how the agent acts"
    )
    assert [e for e, _ in no_incident_side_effects] == ["store_write_refused"]


def test_user_state_still_persists_normally(tmp_path, no_incident_side_effects):
    """Anti-vacuity."""
    tracker = _tracker(tmp_path)
    tracker.preferences.autonomy_level = "high"
    tracker._save_state()

    reread = _tracker(tmp_path)
    assert reread.preferences.autonomy_level == "high", "a healthy round-trip lost it"
    assert no_incident_side_effects == []


# ── SystemEventQueue ──────────────────────────────────────────────────────


def _queue(tmp_path):
    from navig.gateway.system_events import SystemEventQueue

    return SystemEventQueue(storage_path=tmp_path)


def _seed_events(tmp_path, count=2):
    import asyncio

    q = _queue(tmp_path)

    async def _go():
        for i in range(count):
            await q.emit("host_check", {"n": i})

    asyncio.run(_go())
    return q


def test_a_locked_event_queue_does_not_drop_pending_events(
    tmp_path, monkeypatch, no_incident_side_effects
):
    """Every event the operator has not seen yet lives only in this file."""
    import asyncio

    q = _seed_events(tmp_path)
    path = q._get_events_path()
    original = path.read_text(encoding="utf-8")
    assert original.count('"event_type"') == 2

    unlock = _lock_path(monkeypatch, "events.json")
    fresh = _queue(tmp_path)
    asyncio.run(fresh.emit("host_check", {"n": 99}))

    unlock()
    assert path.read_text(encoding="utf-8") == original, (
        "one transient lock silently dropped every unseen pending event"
    )
    assert [e for e, _ in no_incident_side_effects] == ["store_write_refused"]


def test_event_queue_still_persists_normally(tmp_path, no_incident_side_effects):
    """Anti-vacuity — and the counter must survive so ids stay monotonic."""
    q = _seed_events(tmp_path, count=2)
    assert q._get_events_path().exists()

    reread = _queue(tmp_path)
    assert len(reread._pending) == 2, "a healthy round-trip lost pending events"
    assert reread._event_counter == 2, "the id counter reset — ids stop being monotonic"
    assert no_incident_side_effects == []


def test_the_last_seen_sidecar_is_not_wiped_after_a_failed_load(
    tmp_path, monkeypatch, no_incident_side_effects
):
    """`_flush_last_seen` is the SECOND writer of `self.stats`.

    Guarding `_save_state` alone left this one open: after a failed load
    `stats.last_seen` is the `None` default, so it wrote `{"last_seen": null}` over
    the sidecar — the recovery mirror a restart depends on, destroyed at exactly the
    moment it is most likely to be needed.
    """
    tracker = _tracker(tmp_path)
    tracker.stats.last_seen = "2026-07-31T12:00:00"
    tracker._save_state()          # the MAIN file must exist, or _load_state falls
    tracker._flush_last_seen()     # through to the sidecar branch and restores from it
    sidecar = tmp_path / "last_seen.json"
    original = sidecar.read_text(encoding="utf-8")
    assert "2026-07-31" in original

    unlock = _lock_path(monkeypatch, "user_state.json")
    fresh = _tracker(tmp_path)
    fresh._flush_last_seen()

    unlock()
    assert sidecar.read_text(encoding="utf-8") == original, (
        "the last_seen recovery mirror was overwritten with null"
    )


def test_the_last_seen_sidecar_still_updates_normally(tmp_path, no_incident_side_effects):
    """Anti-vacuity — refusing always would stop restart resilience working at all."""
    tracker = _tracker(tmp_path)
    tracker.stats.last_seen = "2026-08-01T09:00:00"
    tracker._flush_last_seen()

    assert "2026-08-01" in (tmp_path / "last_seen.json").read_text(encoding="utf-8")
