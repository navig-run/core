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


# ── the in-app pane band ──────────────────────────────────────────────────────


def test_allocate_port_never_lands_on_a_pane_port(registry, reserved):
    """Profiles and the desktop app's in-app panes must not claim the same port.

    The panes own ``PANE_CDP_PORT_DEFAULT + slot`` (9450+, 8 slots). They used to start at
    9333, INSIDE the profile band (9280-9339); the band moved on 2026-09-15, but this
    allocator overflows upward past the profile band (+200, to 9539) and would still walk
    into the pane band — two subsystems, one port, and whoever binds second fails with no
    indication why. So the skip stays, now pinned against the new base.
    """
    band = set(range(p._PANE_PORT_BASE, p._PANE_PORT_BASE + p._PANE_PORT_COUNT))
    # Force the scan into the pane band by reserving everything below it.
    reserved.update(range(p.PROFILE_PORT_BASE, p._PANE_PORT_BASE))
    got = p.allocate_port()
    assert got not in band, f"allocated {got}, which a pane slot owns"
    assert got == p._PANE_PORT_BASE + p._PANE_PORT_COUNT


def test_allocate_port_never_lands_on_the_os_dev_cdp_port(registry, reserved):
    """The desktop app's dev WebView2 listens on a fixed CDP port; a profile must not take it.

    `tauri-dev.ts` moved that port to 9400 to get OUT of every NAVIG band — and the
    cross-language guard (`test_cdp_port_bands_do_not_collide.py`) proves it is outside
    the NOMINAL profile band, 9280-9339. But the allocator's fallback keeps scanning
    UPWARD for 200 more ports when the band is full or reserved, and 9400 sits inside
    that overflow. `taken` did not include it, and `probe_port` only notices the dev app
    if it happens to be RUNNING at allocation time; otherwise the profile is handed 9400
    and the next `npm run dev:os` binds second and silently gets no CDP at all.

    Same class as the pane overlap, same fix: the allocator knows the port, one file, no
    migration. A guard over the nominal band is a guard over a path; the allocator's
    reachable range is the surface.
    """
    # Force the scan past the pane band and right up to the dev port.
    reserved.update(range(p.PROFILE_PORT_BASE, p._OS_DEV_CDP_PORT))
    got = p.allocate_port()
    assert got != p._OS_DEV_CDP_PORT, (
        f"allocated {got}, the desktop app's dev CDP port — whoever binds second loses"
    )
    assert got == p._OS_DEV_CDP_PORT + 1


def test_the_os_dev_cdp_port_matches_tauri_dev_ts():
    """Parity: a hand-copied mirror of the TypeScript default, so pin it — cross-language,
    like the pane constant. If someone moves the dev port and not this, the allocator
    silently stops protecting the port that is actually in use."""
    import re
    from pathlib import Path

    ts = Path(__file__).resolve().parents[3] / "apps" / "os" / "scripts" / "tauri-dev.ts"
    if not ts.is_file():  # a checkout without apps/os
        pytest.skip("apps/os not present in this checkout")
    m = re.search(r"NAVIG_OS_CDP_PORT\s*\?\?\s*[\"'](\d+)[\"']", ts.read_text(encoding="utf-8"))
    assert m, "could not find the NAVIG_OS_CDP_PORT default in tauri-dev.ts — update this parser"
    assert int(m.group(1)) == p._OS_DEV_CDP_PORT, (
        f"tauri-dev.ts defaults to {m.group(1)} but profiles.py mirrors {p._OS_DEV_CDP_PORT}"
    )


def test_the_pane_band_matches_the_rust_constant():
    """Parity: this is a hand-copied mirror of `webview_pane.rs`, so pin it.

    A silent drift here re-opens the overlap in one direction while the test that is
    supposed to prove it closed keeps passing.
    """
    import re
    from pathlib import Path

    rs = Path(__file__).resolve().parents[3] / "apps" / "os" / "src-tauri" / "src" / "webview_pane.rs"
    if not rs.is_file():  # a checkout without apps/os
        pytest.skip(f"{rs} not present")
    m = re.search(r"PANE_CDP_PORT_DEFAULT:\s*u16\s*=\s*(\d+)", rs.read_text(encoding="utf-8"))
    assert m, "could not find PANE_CDP_PORT_DEFAULT — update this parser rather than deleting the check"
    assert int(m.group(1)) == p._PANE_PORT_BASE, (
        f"webview_pane.rs says {m.group(1)}, profiles.py assumes {p._PANE_PORT_BASE}"
    )


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
