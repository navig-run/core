"""The durable per-window claim behind "never twice in a day".

Every case is a behaviour the duplicate greeting made expensive: the same day is
suppressed, the next day is allowed, two notices do not collide, a rolling
window paces an hourly alert, and a corrupt or unreadable ledger neither raises
nor turns the guard into a duplicate.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

from navig.core.json_io import JsonReadError
from navig.telegram import daily_once as claim


@pytest.fixture(autouse=True)
def _isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    yield
    claim.clear("briefing:morning")


def _at(day: int, hour: int = 7, minute: int = 13) -> datetime:
    return datetime(2026, 10, day, hour, minute)


def test_first_claim_wins_and_the_same_day_is_suppressed():
    assert claim.daily_once("briefing:morning", _at(21)) is True
    assert claim.daily_once("briefing:morning", _at(21, 7, 17)) is False


def test_a_later_day_is_allowed_again():
    assert claim.daily_once("briefing:morning", _at(21)) is True
    assert claim.daily_once("briefing:morning", _at(22)) is True


def test_different_keys_are_independent():
    assert claim.daily_once("briefing:morning", _at(21)) is True
    assert claim.daily_once("tip:daily", _at(21)) is True
    assert claim.daily_once("briefing:morning", _at(21)) is False
    assert claim.daily_once("tip:daily", _at(21)) is False


def test_a_rolling_window_paces_an_hourly_notice():
    start = _at(21, 9, 0)
    assert claim.daily_once("outpost:lighthouse", start, window_hours=6) is True
    assert claim.daily_once(
        "outpost:lighthouse", start + timedelta(hours=5), window_hours=6
    ) is False
    assert claim.daily_once(
        "outpost:lighthouse", start + timedelta(hours=7), window_hours=6
    ) is True


def test_a_corrupt_ledger_is_quarantined_not_fatal():
    path = claim.state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")

    assert claim.daily_once("tip:daily", _at(21)) is True
    assert path.with_name(path.name + ".corrupt").exists()
    assert claim.daily_once("tip:daily", _at(21)) is False
    json.loads(path.read_text(encoding="utf-8"))


def test_an_unreadable_ledger_suppresses_instead_of_repeating(monkeypatch):
    def boom(*_args, **_kwargs):
        raise JsonReadError("held open by another process")

    monkeypatch.setattr(claim, "load_json_for_update", boom)

    assert claim.daily_once("briefing:morning", _at(21)) is False


def test_a_failed_write_still_allows_this_send(monkeypatch):
    def boom(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(claim, "atomic_write_json", boom)

    assert claim.daily_once("tip:daily", _at(21)) is True
    assert not list(claim.state_path().parent.glob("*.tmp"))


def test_an_empty_key_is_a_programming_error():
    with pytest.raises(ValueError, match="non-empty key"):
        claim.daily_once("   ")


def test_the_ledger_is_small_and_readable():
    assert claim.daily_once("briefing:morning", _at(21)) is True
    data = json.loads(claim.state_path().read_text(encoding="utf-8"))
    assert data["version"] == 1
    assert list(data["entries"]) == ["briefing:morning"]


def test_last_claim_and_clear_expose_the_ledger():
    assert claim.last_claim("tip:daily") is None
    assert claim.daily_once("tip:daily", _at(21)) is True
    assert claim.last_claim("tip:daily") == _at(21)
    assert claim.clear("tip:daily") is True
    assert claim.last_claim("tip:daily") is None
    assert claim.clear("tip:daily") is False


def test_an_aware_now_is_compared_with_naive_claims():
    from datetime import timezone

    assert claim.daily_once("tip:daily", _at(21).replace(tzinfo=timezone.utc)) is True
    assert claim.daily_once("tip:daily", _at(21, 8)) is False


def test_the_state_path_follows_the_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path))
    path = claim.state_path()
    assert path == tmp_path / "telegram" / "daily_once.json"
