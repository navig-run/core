"""`navig notify send` must not flood the operator with the same card.

The 2026-10-22 incident: a looping caller sent the identical daily briefing 12 times in
25 minutes. The CLI is the last shared chokepoint every caller goes through — a cron, a
session, a retry loop — so the duplicate guard lives here: daily types are claimed once
per day, other types by content for a short window.
"""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from navig.commands.notify import notify_app

runner = CliRunner()


@pytest.fixture
def delivered(monkeypatch, tmp_path):
    """Isolated notify store; records dispatches instead of sending."""
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    calls: list[tuple[str, str, str]] = []

    async def fake_dispatch(type_key: str, title: str, body: str):
        calls.append((type_key, title, body))
        return {"channels": [{"channel": "telegram", "ok": True, "detail": "sent"}]}

    monkeypatch.setattr("navig.notify.router.dispatch", fake_dispatch)
    return calls


@pytest.fixture
def replace_dispatch(monkeypatch):
    def _install(outcome):
        async def fake_dispatch(type_key: str, title: str, body: str):
            return outcome

        monkeypatch.setattr("navig.notify.router.dispatch", fake_dispatch)

    return _install


def _send(type_key: str, title: str, body: str = ""):
    return runner.invoke(notify_app, ["send", type_key, title, "--body", body])


def test_a_daily_briefing_is_sent_once_per_day(delivered):
    first = _send("briefing", "Brief 2026-10-22", "same body")
    second = _send("briefing", "Brief 2026-10-22", "same body")

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert len(delivered) == 1, "the second briefing must be suppressed"
    assert "duplicate send suppressed" in second.output


def test_a_different_daily_type_is_not_blocked(delivered):
    _send("briefing", "Brief 2026-10-22")
    _send("goal_digest", "Goals 2026-10-22")

    assert [c[0] for c in delivered] == ["briefing", "goal_digest"]


def test_an_identical_non_daily_message_is_throttled(delivered):
    _send("system_alert", "Disk almost full")
    _send("system_alert", "Disk almost full")

    assert len(delivered) == 1


def test_a_muted_send_gives_the_claim_back(delivered, replace_dispatch):
    replace_dispatch({"channels": []})
    muted = _send("briefing", "Brief 2026-10-22")
    assert muted.exit_code == 0
    assert "delivered to no channel" in muted.output

    # The claim must not outlive a send that reached nothing.
    replace_dispatch({"channels": [{"channel": "telegram", "ok": True, "detail": "sent"}]})
    retried = _send("briefing", "Brief 2026-10-22")
    assert retried.exit_code == 0
    assert "duplicate send suppressed" not in retried.output


def test_a_total_failure_gives_the_claim_back(delivered, replace_dispatch):
    replace_dispatch({"channels": [{"channel": "telegram", "ok": False, "detail": "502"}]})
    failed = _send("briefing", "Brief 2026-10-22")
    assert failed.exit_code == 1

    replace_dispatch({"channels": [{"channel": "telegram", "ok": True, "detail": "sent"}]})
    retried = _send("briefing", "Brief 2026-10-22")
    assert retried.exit_code == 0
    assert "duplicate send suppressed" not in retried.output

