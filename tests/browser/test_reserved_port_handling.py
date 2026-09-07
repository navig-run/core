"""A port with nothing listening is not necessarily a port you can USE.

Windows reserves ranges for Hyper-V / WSL / Docker. Inside one, `bind()` raises
``PermissionError(13)`` while nothing owns the port, nothing is listening, and `netstat`
shows it free — only ``netsh interface ipv4 show excludedportrange protocol=tcp`` reveals
it, and the reservations **move across reboots**.

`targets.find_free_port` has always known this. `profiles.allocate_port` did not, and it is
the other port allocator — so it handed out reserved ports and the browser then failed to
open its debug port. Measured on the operator's machine: the reservation was **9181-9280**
and ``PROFILE_PORT_BASE`` is **9280**, so the FIRST profile ever created got a port that can
never bind. Their `navig-epic` profile held it, which is why the scheduled Epic claim could
not open a browser at all.
"""

from __future__ import annotations

import pytest

from navig.browser import profiles as p
from navig.browser import targets as t


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setattr(p, "registry_path", lambda: tmp_path / "cdp-profiles.json")


@pytest.fixture
def reserved(monkeypatch):
    """Simulate an OS reservation over a set of ports."""
    blocked: set[int] = set()

    def _fake_bindable(port: int) -> bool:
        return port not in blocked

    monkeypatch.setattr(t, "port_is_bindable", _fake_bindable)
    monkeypatch.setattr(t, "probe_port", lambda *a, **k: None)  # nothing serving CDP
    return blocked


def test_allocate_port_skips_a_reserved_port(registry, reserved):
    """The regression: PROFILE_PORT_BASE itself was reserved on the operator's box."""
    reserved.add(p.PROFILE_PORT_BASE)
    assert p.allocate_port() == p.PROFILE_PORT_BASE + 1


def test_allocate_port_skips_a_whole_reserved_run(registry, reserved):
    reserved.update(range(p.PROFILE_PORT_BASE, p.PROFILE_PORT_BASE + 10))
    assert p.allocate_port() == p.PROFILE_PORT_BASE + 10


def test_allocate_port_still_avoids_taken_and_serving_ports(registry, reserved, monkeypatch):
    """The pre-existing rules must survive the new one."""
    p.create_profile("first")
    taken = p.get_profile("first").port
    second = p.allocate_port()
    assert second != taken


def test_port_is_bindable_is_true_for_a_free_port():
    """Anti-vacuity: if this helper always returned False the guards above would 'pass'."""
    free = t._os_assigned_port()
    assert free is not None
    assert t.port_is_bindable(free) is True


def test_port_is_bindable_is_false_while_a_listener_holds_it():
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as held:
        held.bind(("127.0.0.1", 0))
        held.listen(1)
        port = held.getsockname()[1]
        # SO_REUSEADDR lets a *bind* succeed against a lingering TIME_WAIT socket, but not
        # against a live LISTENER — which is the case that matters here.
        assert t.port_is_bindable(port) is False


def test_find_free_port_still_skips_reserved_ports(reserved):
    """Both allocators must share the behaviour — that is the point of the shared helper."""
    reserved.update(range(9222, 9232))
    assert t.find_free_port(start=9222, count=20) == 9232


# ── the self-heal on an EXISTING profile ──────────────────────────────────────


def test_reallocate_port_keeps_the_profile_dir(registry, reserved):
    """The login lives in user_data_dir. Moving the port must not touch it."""
    p.create_profile("epic")
    before = p.get_profile("epic")
    reserved.add(before.port)

    new_port = p.reallocate_port("epic")
    after = p.get_profile("epic")

    assert new_port != before.port
    assert after.port == new_port
    assert after.user_data_dir == before.user_data_dir, "the login must survive"


def test_reallocate_port_on_an_unknown_profile_returns_none(registry, reserved):
    assert p.reallocate_port("ghost") is None


def test_profile_open_repairs_a_reserved_port(registry, reserved, monkeypatch):
    """End to end: opening a profile whose stable port went reserved must self-heal.

    Before this, the launch simply timed out and reported a generic failure — with no hint
    that the cause was an OS reservation the operator could not see.
    """
    from navig.browser import cdp_actions

    p.create_profile("epic")
    dead = p.get_profile("epic").port
    reserved.add(dead)

    launched: dict = {}

    class _T:
        def to_dict(self):
            return {"port": launched.get("port")}

    def _fake_launch(app, port=None, user_data_dir=None, profile_directory=None, extra_args=None):
        launched["port"] = port
        return _T()

    monkeypatch.setattr(t, "launch_with_cdp", _fake_launch)
    monkeypatch.setattr("navig.browser.visibility._configured_headless", lambda: None)

    res = cdp_actions.profile_open("epic", context="script")

    assert res["ok"] is True
    assert res["port"] != dead, "it must not relaunch on the unusable port"
    assert launched["port"] == res["port"]
    assert p.get_profile("epic").port == res["port"], "the repair must be persisted"


def test_the_repair_is_actually_recorded_as_an_incident(registry, reserved, monkeypatch):
    """A self-heal nobody can see is how the original problem stayed hidden.

    ⚠ This test exists because its absence hid a real bug. The `record(...)` call sits
    inside `except Exception: pass`, so when it was written with the wrong signature — a
    positional dict against `record(event, **data)`, plus a name collision with this
    module's own `async def record` — it raised TypeError on every call, was swallowed, and
    the incident was NEVER written. Every other assertion in this file still passed. Only
    the build guards caught it, and the fix is to assert the EFFECT, not the call.
    """
    from navig.browser import cdp_actions
    from navig.core import incidents

    p.create_profile("epic")
    dead = p.get_profile("epic").port
    reserved.add(dead)

    seen: list[tuple] = []
    monkeypatch.setattr(incidents, "record",
                        lambda event, **data: seen.append((event, data)))
    monkeypatch.setattr(t, "launch_with_cdp",
                        lambda *a, **k: type("T", (), {"to_dict": lambda self: {}})())
    monkeypatch.setattr("navig.browser.visibility._configured_headless", lambda: None)

    cdp_actions.profile_open("epic", context="script")

    assert seen, "the port repair recorded no incident — it healed silently"
    event, data = seen[0]
    assert event == "PROFILE_PORT_REALLOCATED"
    assert data["profile"] == "epic"
    assert data["old_port"] == dead
    assert data["new_port"] != dead


def test_profile_open_reports_clearly_when_the_repair_cannot_be_saved(
    registry, reserved, monkeypatch
):
    """A failed repair must name the cause, not fall back to a generic launch failure."""
    from navig.browser import cdp_actions

    p.create_profile("epic")
    reserved.add(p.get_profile("epic").port)
    monkeypatch.setattr(p, "reallocate_port", lambda name: None)
    monkeypatch.setattr("navig.browser.visibility._configured_headless", lambda: None)

    res = cdp_actions.profile_open("epic", context="script")
    assert res["ok"] is False
    assert "excludedportrange" in res["error"], "the operator needs the command that shows it"
