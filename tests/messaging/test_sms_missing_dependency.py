"""A channel that cannot send must not present itself as one that can.

Measured on the operator's own daemon: `adapters.sms.enabled` was true with a
twilio block configured, the `twilio` package was never installed, and every
habit reminder produced

    [navig.messaging.adapters.sms] ERROR: sms_send_failed | to=+33… |
        error=twilio package required: pip install twilio

one ERROR per delivery, indefinitely, for a channel that could never work.

The cause is import placement, not configuration: the SDK import lives inside
`_get_client`, several frames below `send`. So an adapter whose SDK is absent
constructs cleanly, registers as a usable channel, and only fails once a real
message is being delivered — at which point the failure is per-send noise rather
than one actionable startup line.

⚠ The "missing" cases FORCE the SDK absent (`sdk_absent`) rather than assume it.
The first version asserted `== "twilio"` against whatever happened to be installed,
so on any machine with `twilio` present — the operator's own, where `pip install
twilio` was the documented remedy — the check correctly answered `None` and the test
reported a defect in code that was right. A test that depends on the environment
rather than the code is a permanent false alarm in the gate: red on some machines,
green on others, and never because of the change under review. `vonage` passed for
the mirror-image reason (nobody had installed it), which is the same fragility with
better luck.
"""

from __future__ import annotations

import importlib.util

import pytest

from navig.messaging.adapters import sms as sms_mod
from navig.messaging.adapters.sms import SmsAdapter


@pytest.fixture
def sdk_absent(monkeypatch) -> None:
    """Make every provider SDK look uninstalled, whatever this machine has.

    Patches the one probe `missing_dependency` uses — `find_spec` — and only for
    the provider packages, so an unrelated module lookup still behaves. The
    positive direction (`test_a_present_package_reports_nothing`) maps onto a
    stdlib module for the same reason: the assertion must follow the CODE, not
    the site-packages of whoever runs it.
    """
    real = importlib.util.find_spec
    providers = set(sms_mod._PROVIDER_PACKAGES.values())

    def _absent(name: str, *args, **kwargs):
        if name in providers:
            return None
        return real(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", _absent)


def test_a_missing_sdk_is_reported_by_name(sdk_absent) -> None:
    """The bug, in the operator's own configuration shape.

    Returns the package NAME rather than a bool so the caller can name the
    remedy instead of saying "something is missing".
    """
    adapter = SmsAdapter(
        config={"provider": "twilio", "twilio": {"account_sid": "x", "auth_token": "y"}}
    )
    assert adapter.missing_dependency() == "twilio"


def test_vonage_is_covered_too(sdk_absent) -> None:
    """Both providers have a lazy import, so both have the defect."""
    assert SmsAdapter(config={"provider": "vonage"}).missing_dependency() == "vonage"


def test_the_fixture_actually_hides_an_installed_package() -> None:
    """Teeth for `sdk_absent` itself: it must hide a package that IS importable.

    Otherwise, on a machine where no provider SDK is installed, every test above
    passes without the fixture doing anything — and a fixture that is never
    load-bearing can rot unnoticed. `json` stands in for an installed SDK.
    """
    real = importlib.util.find_spec
    assert real("json") is not None  # the premise: it is importable here

    def _absent(name: str, *args, **kwargs):
        return None if name == "json" else real(name, *args, **kwargs)

    # Same shape as the fixture, applied to a package we KNOW is present.
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(importlib.util, "find_spec", _absent)
        mp.setitem(sms_mod._PROVIDER_PACKAGES, "fakeprov", "json")
        assert SmsAdapter(config={"provider": "fakeprov"}).missing_dependency() == "json"


def test_a_present_package_reports_nothing(monkeypatch) -> None:
    """⚠ The negative direction — without this the check could be always-true.

    Mapped onto a stdlib module, which is importable by construction, so the
    assertion cannot pass merely because everything looks missing.
    """
    monkeypatch.setitem(sms_mod._PROVIDER_PACKAGES, "fakeprov", "json")
    adapter = SmsAdapter(config={"provider": "fakeprov"})
    assert adapter.missing_dependency() is None


def test_an_unknown_provider_is_not_a_dependency_problem() -> None:
    """A typo'd provider must not be reported as a missing package — that would
    send the operator to `pip install` for a name that does not exist."""
    assert SmsAdapter(config={"provider": "carrier-pigeon"}).missing_dependency() is None


def test_the_default_provider_is_still_twilio(sdk_absent) -> None:
    """The check reads `self._provider`, which defaults — a config with only
    `enabled: true` must still resolve to a real provider rather than None."""
    assert SmsAdapter(config={}).missing_dependency() == "twilio"


@pytest.mark.parametrize("boom", [ImportError("no parent"), ValueError("bad name")])
def test_a_raising_find_spec_still_answers(monkeypatch, boom) -> None:
    """`find_spec` raises when a parent package is missing or the name is odd.

    Either way the SDK is not importable, so the honest answer is the package
    name — never an exception escaping into gateway startup.
    """
    import importlib.util

    def _raise(_name):
        raise boom

    monkeypatch.setattr(importlib.util, "find_spec", _raise)
    assert SmsAdapter(config={"provider": "twilio"}).missing_dependency() == "twilio"


def test_the_gateway_actually_asks_before_registering() -> None:
    """⚠ The wiring, not the helper.

    A `missing_dependency` that nobody consults changes nothing — the adapter
    would still register and still fail per send. This asserts the gateway's SMS
    block consults it BEFORE `registry.register`, and that the operator is told
    at WARNING (they switched the channel on, so "it is off again" must be
    visible; `logger.debug` is not captured at the daemon's normal level, which
    is how the original failure stayed invisible in the log).
    """
    from pathlib import Path

    server = Path(__file__).resolve().parents[2] / "navig" / "gateway" / "server.py"
    src = server.read_text(encoding="utf-8")

    start = src.index("# ── SMS adapter ──")
    block = src[start : start + 2000]

    assert "missing_dependency()" in block, (
        "the gateway registers the SMS adapter without asking whether its SDK "
        "is importable — every send will fail one at a time"
    )
    assert block.index("missing_dependency()") < block.index("registry.register(adapter)"), (
        "the dependency is checked AFTER registration, so the broken adapter is "
        "registered anyway"
    )
    assert "logger.warning(" in block, (
        "a channel the operator enabled was silently not registered"
    )
