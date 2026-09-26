"""The same health issues must raise ONE remediate mission, not one per heartbeat.

Regression for the operator-visible symptom: a runtime store holding 79 missions,
every one of them "Remediate health issues", 77 auto-cancelled when the approval
prompt timed out. The issues were an expired Anthropic key and two retired NVIDIA
models — things the agent cannot fix by itself — so every heartbeat re-found them
and, with no suppression, asked again.

These tests drive the REAL ``NavigGateway._on_heartbeat_issues`` against a REAL
persisted ``RuntimeStore`` and a REAL ``MissionExecutor``. Only ``_spawn`` is
patched out, so the mission is never actually executed but every persistence step
(``create_mission`` → ``flush`` → ``_active``) is the shipping code path.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from navig.contracts.store import RuntimeStore
from navig.gateway.server import NavigGateway
from navig.missions.executor import MissionExecutor

# ACTIONABLE issues — a shell agent can diagnose a host or a disk. Provider/model
# conditions (an expired key, a retired model) are triaged OUT before this path
# now (navig.heartbeat.triage): they never raise a mission, so they cannot be
# the fixture for a test about how missions are de-duplicated.
ISSUES = [
    "[HIGH] host web-1 unreachable (ssh timed out after 2.0s)",
    "[MEDIUM] disk / on web-1 at 91% used",
]


def _executor(store: RuntimeStore, monkeypatch: pytest.MonkeyPatch) -> MissionExecutor:
    """A real MissionExecutor that persists but never runs the mission."""
    ex = MissionExecutor(gateway=SimpleNamespace(config_manager=None), store=store)
    monkeypatch.setattr(ex, "_spawn", lambda mission: None)
    return ex


def _gateway(executor: MissionExecutor, **missions_cfg) -> NavigGateway:
    """A real NavigGateway object with only the attributes this path reads.

    ``__new__`` skips ``__init__`` (which would bind sockets); the methods under
    test are the real, unmodified ones.
    """
    gw = NavigGateway.__new__(NavigGateway)
    gw.config_manager = SimpleNamespace(
        global_config={"missions": {"autonomous_enabled": True, **missions_cfg}}
    )
    gw.mission_executor = executor
    return gw


def _remediate(store: RuntimeStore) -> list:
    return [m for m in store.list_missions(limit=500) if m.capability == "remediate"]


async def test_first_issue_set_raises_a_mission(tmp_path, monkeypatch):
    store = RuntimeStore(tmp_path)
    gw = _gateway(_executor(store, monkeypatch))

    await gw._on_heartbeat_issues(ISSUES)

    missions = _remediate(store)
    assert len(missions) == 1
    # The signature is what every later check keys on — it must be recorded.
    assert missions[0].metadata.get("issue_signature")


async def test_identical_issues_do_not_raise_a_second_mission(tmp_path, monkeypatch):
    store = RuntimeStore(tmp_path)
    gw = _gateway(_executor(store, monkeypatch))

    for _ in range(5):
        await gw._on_heartbeat_issues(ISSUES)

    assert len(_remediate(store)) == 1


async def test_issue_order_does_not_defeat_suppression(tmp_path, monkeypatch):
    """The heartbeat need not report issues in a stable order."""
    store = RuntimeStore(tmp_path)
    gw = _gateway(_executor(store, monkeypatch))

    await gw._on_heartbeat_issues(ISSUES)
    await gw._on_heartbeat_issues(list(reversed(ISSUES)))

    assert len(_remediate(store)) == 1


async def test_suppression_survives_a_daemon_restart(tmp_path, monkeypatch):
    """The dominant duplicate source: HeartbeatRunner checks 10-60s after start.

    A fresh store/executor/gateway reads ``missions.json`` back off disk, so this
    also pins that ``Mission.metadata`` survives the JSON round-trip — without
    that, dedup would silently fail for exactly the case it targets.
    """
    store = RuntimeStore(tmp_path)
    gw = _gateway(_executor(store, monkeypatch))
    await gw._on_heartbeat_issues(ISSUES)

    # Restart: nothing in memory carries over.
    store2 = RuntimeStore(tmp_path)
    assert _remediate(store2), "mission did not persist — the rest of this test would be vacuous"
    gw2 = _gateway(_executor(store2, monkeypatch))
    await gw2._on_heartbeat_issues(ISSUES)

    assert len(_remediate(store2)) == 1


async def test_a_genuinely_new_issue_still_raises_a_mission(tmp_path, monkeypatch):
    """Suppression must not swallow a problem we have never reported.

    The blanket floor is disabled here so this exercises the identity rule
    alone; ``test_the_floor_delays_a_new_issue`` covers their interaction.
    """
    store = RuntimeStore(tmp_path)
    gw = _gateway(_executor(store, monkeypatch), remediate_min_interval_secs=0)

    await gw._on_heartbeat_issues(ISSUES)
    await gw._on_heartbeat_issues([*ISSUES, "[HIGH] disk 97% full on C:"])

    assert len(_remediate(store)) == 2


async def test_flapping_issue_text_cannot_defeat_suppression(tmp_path, monkeypatch):
    """A wobbling detail changes the signature — the floor must still hold.

    Measured against the live provider: probing one model twice returned "live"
    once and a transient "503 Service Unavailable" the other time. Each flap is
    a different issue line, so the identity rule alone would raise a mission per
    flap.
    """
    store = RuntimeStore(tmp_path)
    gw = _gateway(_executor(store, monkeypatch))

    for detail in ("503 Service Unavailable", "504 Gateway Timeout", "live", "502 Bad Gateway"):
        await gw._on_heartbeat_issues([f"[HIGH] host web-1 health probe is error ({detail})"])

    assert len(_remediate(store)) == 1


async def test_the_floor_delays_a_new_issue_rather_than_dropping_it(tmp_path, monkeypatch):
    """The floor is a delay, not a mute: once it lapses the new issue is raised."""
    store = RuntimeStore(tmp_path)
    ex = _executor(store, monkeypatch)
    gw = _gateway(ex, remediate_min_interval_secs=1800)
    await gw._on_heartbeat_issues(ISSUES)

    new_issues = [*ISSUES, "[HIGH] disk 97% full on C:"]
    await gw._on_heartbeat_issues(new_issues)
    assert len(_remediate(store)) == 1, "floor should hold within the window"

    prior = _remediate(store)[0]
    prior.created_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    store.flush()
    ex.active.discard(prior.mission_id)

    await gw._on_heartbeat_issues(new_issues)
    assert len(_remediate(store)) == 2


async def test_asks_again_once_the_backoff_window_expires(tmp_path, monkeypatch):
    store = RuntimeStore(tmp_path)
    ex = _executor(store, monkeypatch)
    gw = _gateway(ex, remediate_backoff_base_secs=3600)
    await gw._on_heartbeat_issues(ISSUES)

    # Age the mission past its window and let it leave the in-flight set, the
    # way a real one does when its approval times out.
    prior = _remediate(store)[0]
    prior.created_at = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    store.flush()
    ex.active.discard(prior.mission_id)

    await gw._on_heartbeat_issues(ISSUES)
    assert len(_remediate(store)) == 2


async def test_in_flight_mission_suppresses_even_past_the_window(tmp_path, monkeypatch):
    """A mission still awaiting the operator's answer is not worth asking twice."""
    store = RuntimeStore(tmp_path)
    ex = _executor(store, monkeypatch)
    gw = _gateway(ex, remediate_backoff_base_secs=1)
    await gw._on_heartbeat_issues(ISSUES)

    prior = _remediate(store)[0]
    prior.created_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    store.flush()
    assert prior.mission_id in ex.active

    await gw._on_heartbeat_issues(ISSUES)
    assert len(_remediate(store)) == 1


async def test_window_is_read_from_a_string_config_value(tmp_path, monkeypatch):
    """`navig config set` stores raw strings — a bare int() would raise and the
    caller's except would silently restore the spam."""
    store = RuntimeStore(tmp_path)
    ex = _executor(store, monkeypatch)
    gw = _gateway(ex, remediate_backoff_base_secs="3600")

    assert gw._remediate_backoff_base_secs() == 3600


async def test_unparseable_timestamp_is_not_treated_as_recent(tmp_path, monkeypatch):
    """Unknown age must never mean 'brand new' — that would suppress forever."""
    store = RuntimeStore(tmp_path)
    ex = _executor(store, monkeypatch)
    gw = _gateway(ex)
    await gw._on_heartbeat_issues(ISSUES)

    prior = _remediate(store)[0]
    prior.created_at = "not-a-timestamp"
    store.flush()
    ex.active.discard(prior.mission_id)

    await gw._on_heartbeat_issues(ISSUES)
    assert len(_remediate(store)) == 2


async def test_the_window_doubles_each_time_the_same_issues_recur(tmp_path, monkeypatch):
    """"Tell me, then stop nagging": the same issue set backs off exponentially.

    Replaying the operator's real history (79 missions / 30 days / 2 distinct
    issue sets) gave 17 raises under backoff versus 41 under a flat 6h window —
    while still re-asking within the 1h base rather than after 6h.
    """
    store = RuntimeStore(tmp_path)
    ex = _executor(store, monkeypatch)
    gw = _gateway(ex, remediate_backoff_base_secs=3600, remediate_min_interval_secs=0)

    def age_all_to(seconds: float) -> None:
        """Put every stored mission at the same age, and out of the in-flight set."""
        stamp = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
        for m in _remediate(store):
            m.created_at = stamp
            ex.active.discard(m.mission_id)
        store.flush()

    await gw._on_heartbeat_issues(ISSUES)
    assert len(_remediate(store)) == 1

    # Raised once → next window 1h; twice → 2h; three times → 4h.
    for seen, window_h in ((1, 1), (2, 2), (3, 4)):
        assert len(_remediate(store)) == seen

        age_all_to(window_h * 3600 - 600)  # just inside
        await gw._on_heartbeat_issues(ISSUES)
        assert len(_remediate(store)) == seen, f"raised inside the {window_h}h window"

        age_all_to(window_h * 3600 + 600)  # just outside
        await gw._on_heartbeat_issues(ISSUES)
        assert len(_remediate(store)) == seen + 1, f"not raised past the {window_h}h window"


async def test_backoff_is_capped_so_an_issue_still_resurfaces(tmp_path, monkeypatch):
    """Doubling without a ceiling would eventually mean 'never again'."""
    gw = _gateway(_executor(RuntimeStore(tmp_path), monkeypatch))
    cap = gw._remediate_backoff_max_secs()

    assert cap > 0, "an uncapped default would silence a real issue forever"
    # 40 recurrences must not produce an astronomically large (or overflowing) wait.
    assert gw._remediate_backoff_wait(40, 3600, cap) == float(cap)
    assert gw._remediate_backoff_wait(1, 3600, cap) == 3600.0
    assert gw._remediate_backoff_wait(3, 3600, cap) == 14400.0
