"""The doctor's `Mini App button` row — the deck side of "green lights over a
silently broken bot".

Its sibling row (`Telegram webhook`) catches "the bot cannot HEAR with every light
green". This one catches the mirror image: the deck SHOWS an old app with every light
green, because Telegram caches a Mini App by URL and the button's `v=` cache-bust
never changed.

Two things are pinned here, and both were defects when this row was added:
  1. The row must appear in EVERY cloud mode. `check_reachability()` used to bail out
     entirely when `cloud.mode != "lighthouse"`, which is right for the webhook rows
     (they read a lighthouse tenant) and wrong for this one — the button points at the
     deck, not at the brain's ingress.
  2. Could-not-verify must render ⚠, never a green ✓. `_check(ok=True, ...)` over an
     unknown is worse than a red row: it actively tells the operator not to look.
"""

from __future__ import annotations

import pytest

from navig.commands import doctor as d
from navig.commands import miniapp as mini


@pytest.fixture(autouse=True)
def _not_lighthouse(monkeypatch):
    """Every test here runs in a NON-lighthouse install, so the webhook rows are empty
    and any row we see came from the button check alone."""

    class _Cfg:
        def get(self, key, default=None):
            return "tunnel" if key == "cloud.mode" else default

    monkeypatch.setattr("navig.core.Config", lambda *a, **k: _Cfg())


def _rows(monkeypatch, verdict):
    monkeypatch.setattr(mini, "miniapp_button_health", lambda **kw: verdict)
    return d.check_reachability()


def test_button_row_is_present_outside_lighthouse_mode(monkeypatch):
    assert d._webhook_tenant_rows() == []  # precondition: the lighthouse rows opted out
    rows = _rows(monkeypatch, (False, "button URL carries no v= cache-bust", False))
    assert [r.label for r in rows] == ["Mini App button"]
    assert rows[0][0] == d._ERR


def test_healthy_button_is_a_green_tick(monkeypatch):
    rows = _rows(monkeypatch, (True, "current bundle (v=abc123)", False))
    assert rows[0][1] is True
    assert rows[0][0] == d._OK


def test_could_not_verify_renders_warn_not_ok(monkeypatch):
    rows = _rows(monkeypatch, (False, "COULD NOT VERIFY (Unauthorized)", True))
    assert rows[0][1] is False
    assert rows[0][0] == d._WARN


def test_no_deck_deployed_contributes_no_row(monkeypatch):
    assert _rows(monkeypatch, None) == []


def test_a_raising_health_check_degrades_to_warn(monkeypatch):
    """doctor must never crash on a check — and must not claim health either."""

    def _boom(**kw):
        raise RuntimeError("telegram exploded")

    monkeypatch.setattr(mini, "miniapp_button_health", _boom)
    rows = d.check_reachability()
    assert len(rows) == 1
    assert rows[0][1] is False
    assert rows[0][0] == d._WARN
    assert "COULD NOT VERIFY" in rows[0].detail
