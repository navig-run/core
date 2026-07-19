"""The deck Monitors card shows a truthful requirement per monitor — webcam needs
Windows, connectivity needs Lighthouse, resources needs psutil."""

from __future__ import annotations

from navig.gateway.deck.routes.notify import _monitor_availability


def test_webcam_requires_windows():
    assert _monitor_availability("webcam", mode="lighthouse", is_win=True, has_psutil=True) == (True, None)
    avail, req = _monitor_availability("webcam", mode="lighthouse", is_win=False, has_psutil=True)
    assert avail is False and req == "Windows only"


def test_connectivity_requires_lighthouse():
    assert _monitor_availability("connectivity", mode="lighthouse", is_win=True, has_psutil=True) == (True, None)
    avail, req = _monitor_availability("connectivity", mode="", is_win=True, has_psutil=True)
    assert avail is False and "Lighthouse" in req


def test_resources_requires_psutil():
    avail, req = _monitor_availability("resources", mode="", is_win=True, has_psutil=False)
    assert avail is False and req == "Needs psutil"
    assert _monitor_availability("resources", mode="", is_win=True, has_psutil=True) == (True, None)


def test_self_errors_always_available():
    assert _monitor_availability("self_errors", mode="", is_win=False, has_psutil=False) == (True, None)


# ── default-on: config_incidents is the one monitor that starts without a toggle ──
#
# The config-health push (a wiped config / re-identified deck key → a notification) was
# built across #279/#282/#289 but sat DORMANT — opt-in, off by default, so nobody got the
# alert unless they knew to flip it. It is now the single default-ON monitor: a missed
# rescue is exactly the silent bot-killer this surface exists to catch, and it is noiseless
# on a healthy install.


def test_config_incidents_defaults_on_at_both_the_gateway_and_the_deck():
    from navig.gateway.deck.routes.notify import _DEFAULT_ON
    from navig.gateway.server import NavigGateway

    assert "config_incidents" in NavigGateway.MONITORS_DEFAULT_ON
    assert "config_incidents" in _DEFAULT_ON


def test_monitor_defaults_agree():
    """The gateway (what starts at boot) and the deck card (what the operator sees) must
    never disagree about a monitor's default — a card showing OFF while the gateway runs it
    is exactly the kind of lie this session has been hunting."""
    from navig.gateway.deck.routes.notify import _DEFAULT_ON, _MONITOR_KEYS
    from navig.gateway.server import NavigGateway

    assert set(NavigGateway.MONITORS_DEFAULT_ON) == set(_DEFAULT_ON)
    # Every default-on monitor must actually be a known monitor on both sides.
    assert set(_DEFAULT_ON) <= set(NavigGateway.MONITOR_KEYS)
    assert set(_DEFAULT_ON) <= _MONITOR_KEYS


def test_deck_shows_default_on_when_unset_and_respects_an_explicit_disable():
    from navig.gateway.deck.routes.notify import _monitor_enabled

    class _Cfg:
        def __init__(self, **v):
            self._v = v

        def get(self, key, default=None):
            return self._v.get(key, default)

    # never toggled → shown ON (the default)
    assert _monitor_enabled(_Cfg(), "config_incidents") is True
    # an opt-in monitor never toggled → OFF
    assert _monitor_enabled(_Cfg(), "webcam") is False
    # explicit disable WINS over the default (a real bool from the deck toggle)
    assert _monitor_enabled(_Cfg(**{"monitors.config_incidents.enabled": False}), "config_incidents") is False
    # explicit enable of an opt-in monitor
    assert _monitor_enabled(_Cfg(**{"monitors.webcam.enabled": True}), "webcam") is True


def test_gateway_boot_starts_config_incidents_by_default(monkeypatch):
    """The boot path must start config_incidents when it was never configured, and NOT
    start it when explicitly disabled."""
    from navig.gateway.server import NavigGateway

    started: list[str] = []

    class _Fake:
        MONITOR_KEYS = NavigGateway.MONITOR_KEYS
        MONITORS_DEFAULT_ON = NavigGateway.MONITORS_DEFAULT_ON

        def __init__(self, monitors_cfg):
            self.config_manager = type("CM", (), {"global_config": {"monitors": monitors_cfg}})()
            self._monitor_tasks = {}

        def _start_monitor(self, name):
            started.append(name)

    # unset → config_incidents starts; opt-in monitors don't
    NavigGateway._init_notify_monitors(_Fake({}))
    assert "config_incidents" in started
    assert "webcam" not in started and "resources" not in started

    # explicitly disabled → does NOT start
    started.clear()
    NavigGateway._init_notify_monitors(_Fake({"config_incidents": {"enabled": False}}))
    assert "config_incidents" not in started


# ── the raw-string config gotcha: `navig config set ... false` must actually disable ──
#
# `navig config set monitors.x.enabled false` persists the STRING "false", which is truthy
# in Python. The gateway boot used a raw truthiness check → it started a monitor the
# operator had just disabled, while the deck card (coercing via _truthy) showed it OFF. The
# two must coerce identically or the card lies about what the daemon is doing.


def test_monitor_enabled_coercion_matches_the_deck():
    from navig.gateway.deck.routes.notify import _truthy as deck_truthy
    from navig.gateway.server import _monitor_enabled_truthy as gw_truthy

    for v in (True, False, "true", "false", "1", "0", "yes", "no", "True", "False", "", "foo", None):
        assert gw_truthy(v) == deck_truthy(v), f"gateway and deck disagree on {v!r}"


def test_boot_honors_a_string_false_from_navig_config_set(monkeypatch):
    """The regression: `navig config set monitors.config_incidents.enabled false` stores
    the STRING 'false'; boot must NOT start the monitor (it used to, via raw truthiness)."""
    from navig.gateway.server import NavigGateway

    started: list[str] = []

    class _Fake:
        MONITOR_KEYS = NavigGateway.MONITOR_KEYS
        MONITORS_DEFAULT_ON = NavigGateway.MONITORS_DEFAULT_ON

        def __init__(self, monitors_cfg):
            self.config_manager = type("CM", (), {"global_config": {"monitors": monitors_cfg}})()
            self._monitor_tasks = {}

        def _start_monitor(self, name):
            started.append(name)

    # string "false" disables the default-on monitor
    NavigGateway._init_notify_monitors(_Fake({"config_incidents": {"enabled": "false"}}))
    assert "config_incidents" not in started, '"false" (string) must disable the monitor'

    # string "true" enables an otherwise-opt-in monitor
    started.clear()
    NavigGateway._init_notify_monitors(_Fake({"webcam": {"enabled": "true"}}))
    assert "webcam" in started, '"true" (string) must enable the monitor'

    # real bool False (the deck toggle) also disables
    started.clear()
    NavigGateway._init_notify_monitors(_Fake({"config_incidents": {"enabled": False}}))
    assert "config_incidents" not in started


def test_monitor_toggle_honors_on_off_and_case():
    """Migrating to the canonical coercion fixes the old case-sensitive whitelist:
    `config set monitors.x.enabled on/ON/Off/…` (raw strings) now resolve correctly.
    The old `_truthy` only matched (True,"1","true","yes","True"), so "ON"/"on"/"off"
    all silently read as OFF (a monitor the operator toggled would ignore them)."""
    from navig.gateway.deck.routes.notify import _monitor_enabled, _truthy
    from navig.gateway.server import _monitor_enabled_truthy as gw_truthy

    class _Cfg:
        def __init__(self, **v):
            self._v = v

        def get(self, key, default=None):
            return self._v.get(key, default)

    # on/ON/yes/… enable an opt-in monitor (previously read as OFF)
    for on in ("on", "ON", "On", "yes", "1", "true", "TRUE"):
        assert _monitor_enabled(_Cfg(**{"monitors.webcam.enabled": on}), "webcam") is True, on
    # off/OFF/no/… disable a default-on monitor (previously read as ON)
    for off in ("off", "OFF", "no", "0", "false", "False", ""):
        assert _monitor_enabled(
            _Cfg(**{"monitors.config_incidents.enabled": off}), "config_incidents"
        ) is False, off
    # deck and gateway must still coerce identically across the newly-handled tokens
    for v in ("on", "ON", "off", "OFF", "TRUE", "no", "yes"):
        assert _truthy(v) == gw_truthy(v), v
