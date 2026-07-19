"""The hard safe-mode gate for `navig do` (blocks outward browser actions)."""

from __future__ import annotations

import pytest

from navig.browser import safe_mode

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _reset_level():
    safe_mode.set_level("off")
    yield
    safe_mode.set_level("off")


def test_outward_labels():
    assert safe_mode.is_outward_label("Send")
    assert safe_mode.is_outward_label("Send ‎(Ctrl-Enter)‎")
    assert safe_mode.is_outward_label("Publish now")
    assert safe_mode.is_outward_label("Delete forever")
    assert safe_mode.is_outward_label("Confirm payment")
    assert not safe_mode.is_outward_label("Save draft")
    assert not safe_mode.is_outward_label("Cancel")
    assert not safe_mode.is_outward_label("Add attachment")
    assert not safe_mode.is_outward_label("")


def test_blocked_reason_respects_level():
    safe_mode.set_level("off")
    assert safe_mode.blocked_reason("Send") is None        # gate off → nothing blocked

    safe_mode.set_level("safe")
    r = safe_mode.blocked_reason("Send button")
    assert r and "BLOCKED" in r and "--yes" in r
    assert safe_mode.blocked_reason("Save draft button") is None  # not an outward action

    safe_mode.set_level("dry_run")
    assert safe_mode.blocked_reason("Publish") is not None


def test_set_level_validates_and_activates():
    safe_mode.set_level("bogus")
    assert safe_mode.get_level() == "off" and not safe_mode.is_active()
    safe_mode.set_level("safe")
    assert safe_mode.is_active()
