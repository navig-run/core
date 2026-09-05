"""A transient/unreadable read of the media-engine budget file must NOT wipe the spend
history. `BudgetGuard.charge()` reads the mutating copy via json_io's `load_json_for_update`
(which RAISES on a file that exists-with-content but is transiently unreadable — a Windows
AV/backup lock) and, on that raise, skips billing the one call instead of saving
``{month: cost}`` over everything.

The old code did ``data = self._load()`` where ``_load`` returned ``{}`` on any failure,
then saved ``{month: cost}`` — which BOTH lost the accrued history AND reset the guard to
$0 spent, silently re-opening the monthly limit until the file was rewritten.
"""

from __future__ import annotations

import pytest

import navig.core.json_io as jio
from navig.gateway.channels.media_engine.budget import BudgetExceeded, BudgetGuard

pytestmark = pytest.mark.integration


def test_charge_round_trips_and_missing_is_zero(tmp_path):
    p = tmp_path / "media_budget.json"
    guard = BudgetGuard(monthly_limit_usd=5.0, budget_file=p)

    assert guard.used() == 0.0  # missing file → $0 spent (a fresh install)
    guard.charge("audd", 0.002)
    guard.charge("openai_vision", 0.015)
    assert round(guard.used(), 6) == 0.017  # accrues across calls
    # A fresh guard over the same file sees the persisted total (real round-trip).
    assert round(BudgetGuard(budget_file=p).used(), 6) == 0.017


def test_charge_over_limit_raises_without_writing(tmp_path):
    p = tmp_path / "media_budget.json"
    guard = BudgetGuard(monthly_limit_usd=0.01, budget_file=p)
    guard.charge("audd", 0.008)
    with pytest.raises(BudgetExceeded):
        guard.charge("openai_vision", 0.015)  # 0.008 + 0.015 > 0.01
    # The rejected charge left the recorded total untouched.
    assert round(guard.used(), 6) == 0.008


def test_charge_skips_rather_than_wiping_on_transient_unreadable(monkeypatch, tmp_path):
    p = tmp_path / "media_budget.json"
    # Seed a month of real spend, close to the limit.
    guard = BudgetGuard(monthly_limit_usd=5.0, budget_file=p)
    guard.charge("openai_vision", 4.9)
    before = p.read_text(encoding="utf-8")
    assert "4.9" in before

    def _locked(*_a, **_k):
        raise OSError("file is locked")  # a lock that survived json_io's retries

    monkeypatch.setattr(jio, "read_text_retrying", _locked)

    # charge() must swallow the JsonReadError (skip billing this ONE call) — NOT raise,
    # and NOT save {month: cost} over the history.
    guard.charge("audd", 0.002)  # returns quietly

    monkeypatch.undo()  # lock lifted — the file is readable again
    # The prior spend survived: history intact AND the guard did not reset to $0.
    assert p.read_text(encoding="utf-8") == before
    assert round(guard.used(), 6) == 4.9
    # And the guard is still biting: a fresh charge that would exceed 5.0 still raises.
    with pytest.raises(BudgetExceeded):
        guard.charge("openai_vision", 0.2)  # 4.9 + 0.2 > 5.0


def test_used_degrades_to_zero_on_unreadable_without_crashing(monkeypatch, tmp_path):
    p = tmp_path / "media_budget.json"
    guard = BudgetGuard(budget_file=p)
    guard.charge("audd", 0.5)

    def _locked(*_a, **_k):
        raise OSError("file is locked")

    monkeypatch.setattr(jio, "read_text_retrying", _locked)
    # The read-only status path must never crash a caller — it degrades to $0.
    assert guard.used() == 0.0
    assert guard.remaining() == guard._limit
