"""Regression: the ``sms.verify_signature`` security toggle must be reachable.

This toggle was silently STUCK OFF for a period: ``get_config_manager().get(...)``
raised ``AttributeError`` (ConfigManager had no ``.get``), the bare ``except`` swallowed
it, and enforcement defaulted off — so an operator who ran
``navig config set sms.verify_signature true`` got NO Twilio signature verification while
every light stayed green. ``.get`` was added, and the gate now goes through the canonical
``coerce_bool`` (the value is stored as the raw string ``"true"``). These lock the behavior:

  * unset            → no enforcement (opt-in default)
  * string "true"    → enforcement ON  → a request with no signature is REJECTED
  * string "false"   → enforcement OFF (the config-set footgun, handled)
"""

import pytest
from aiohttp.test_utils import make_mocked_request

from navig.gateway.routes.sms_webhook import _signature_ok


class _FakeCM:
    """Minimal stand-in for ConfigManager returning a fixed sms.verify_signature."""

    def __init__(self, value):
        self._value = value

    def get(self, key, default=None):
        return self._value if key == "sms.verify_signature" else default


@pytest.fixture
def set_verify(monkeypatch):
    def _install(value):
        import navig.config as cfg

        monkeypatch.setattr(cfg, "get_config_manager", lambda: _FakeCM(value))

    return _install


def test_unset_means_no_enforcement(set_verify):
    """Opt-in default: with the toggle absent, intake is never blocked."""
    set_verify(None)
    req = make_mocked_request("POST", "/sms/webhook")
    assert _signature_ok(req, {}) is True


def test_string_true_enables_enforcement_and_rejects_unsigned(set_verify):
    """The security regression: `navig config set sms.verify_signature true` stores the
    STRING "true" — enforcement must switch ON and reject a request with no signature."""
    set_verify("true")
    req = make_mocked_request("POST", "/sms/webhook")  # no X-Twilio-Signature header
    assert _signature_ok(req, {}) is False


def test_string_false_disables_enforcement(set_verify):
    """The footgun: the string "false" must read as OFF, not truthy."""
    set_verify("false")
    req = make_mocked_request("POST", "/sms/webhook")
    assert _signature_ok(req, {}) is True


def test_real_bool_true_also_enforces(set_verify):
    set_verify(True)
    req = make_mocked_request("POST", "/sms/webhook")
    assert _signature_ok(req, {}) is False
