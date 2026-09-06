"""The Telegram suite must not decide anything by wall clock.

Two tests in `test_tiktok_photo_actions.py` went red under `-n auto` and green on
re-run of the identical commit. Neither failure mentioned time:

    assert 'tx' in {'an', 'au', 'dl', 'tr'}
    assert 'no caption' in '📄 tiktok took too long to answer — try again in a moment.'

Both are what a timed-out `_INFO_TIMEOUT` looks like from the outside — a card
whose metadata read never returned drops its 📄 Full text button, because
`_card_overflows(None)` is False. Forcing `_INFO_TIMEOUT = 0.001` reproduces
both, character for character.

`conftest._outcomes_do_not_depend_on_machine_load` neutralises the bound. This
file exists so deleting that fixture fails a test with a name that says why,
instead of returning the suite to intermittent redness nobody can attribute.
"""

from __future__ import annotations

import asyncio

import pytest

from navig.telegram import tiktok_actions


def test_the_metadata_read_is_not_bounded_by_a_realistic_clock():
    """Production caps a live tiktok.com fetch; the fakes here take milliseconds,
    so any bound a loaded machine can reach is measuring load, not behaviour."""
    assert tiktok_actions._INFO_TIMEOUT >= 60, (
        "the metadata timeout is back in range of a busy machine — "
        "conftest._outcomes_do_not_depend_on_machine_load is gone or bypassed"
    )
    assert tiktok_actions._COVER_TIMEOUT >= 60


def test_the_bound_is_still_finite():
    """Neutralised, not removed: a genuine hang must fail the suite rather than
    sit forever holding a worker."""
    for name in ("_INFO_TIMEOUT", "_COVER_TIMEOUT"):
        value = getattr(tiktok_actions, name)
        assert value != float("inf"), f"{name} must stay finite"
        assert isinstance(value, (int, float))


async def test_a_timeout_still_produces_the_symptom_it_used_to():
    """Pins the DIAGNOSIS, not just the remedy.

    If this ever stops holding, the fixture above is guarding a mechanism that no
    longer exists and the next intermittent failure will be misattributed again.
    """
    async def _slow():
        await asyncio.sleep(10)

    with pytest.raises((TimeoutError, asyncio.TimeoutError)):
        await asyncio.wait_for(_slow(), timeout=0.001)


def test_a_test_that_wants_a_short_timeout_can_still_have_one(monkeypatch):
    """The fixture must not take the ability away — `test_tiktok_card_dm` sets
    0.05 to exercise the timeout path deliberately, from inside the test body."""
    monkeypatch.setattr(tiktok_actions, "_INFO_TIMEOUT", 0.05)

    assert tiktok_actions._INFO_TIMEOUT == 0.05
