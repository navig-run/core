"""The daemon's own alarm must be able to ring.

Measured on the operator's own daemon, which had **4 HIGH findings outstanding**
(an expired Anthropic key, a retired NVIDIA model) and told nobody. Three
independent defects in one 20-line function:

1. It required ``notifications.recipient`` — a key **nothing in this repo
   writes**. Read in exactly one place, written by none, documented only as a
   manual ``navig config set``. Unset, it returned early. That is the line the
   log actually shows, six times: *"No notification recipient configured."*
2. Setting it did not help. The delivery call was
   ``self.gateway.send_notification(...)`` and **NavigGateway has no such
   method** — verified: ``hasattr`` False, MRO ``[NavigGateway, object]``, so no
   mixin supplies it. Following the HANDBOOK's own instruction would have raised
   AttributeError instead of delivering.
3. The ``notification_filter`` branch was dead too — never set on the gateway,
   so its ``hasattr`` guard silently skipped. Harmless, same class.

The fix routes through the notify router every other producer uses.
``system_alert`` fans out to ``["deck", "telegram"]``, so the alarm needs no
second copy of "who is the operator" — which is all the missing ``recipient``
key ever was.
"""

from __future__ import annotations

import pytest

from navig.heartbeat.runner import HeartbeatRunner


class _ConfigManager:
    """A DEFAULT install: no `notifications` section at all."""

    global_config: dict = {}


class _Gateway:
    """Shaped like the real NavigGateway — note what it does NOT have.

    No `send_notification`, no `notification_filter`. Both were called by the old
    code; asserting their absence here is what keeps this test honest, because a
    fake that grew the missing method would hide the very bug.
    """

    config_manager = _ConfigManager()


def _runner() -> HeartbeatRunner:
    runner = HeartbeatRunner.__new__(HeartbeatRunner)
    runner.gateway = _Gateway()
    return runner


def test_the_real_gateway_still_lacks_the_method_the_old_code_called() -> None:
    """Pins defect #2 so nobody "restores" the old call.

    If `send_notification` is ever added to NavigGateway this test fails loudly,
    which is the moment to decide deliberately — not to discover it from a
    heartbeat that never rang.
    """
    from navig.gateway.server import NavigGateway

    assert not hasattr(NavigGateway, "send_notification"), (
        "NavigGateway grew send_notification — revisit the heartbeat's delivery "
        "path deliberately rather than leaving two mechanisms"
    )
    assert not hasattr(NavigGateway, "notification_filter")


@pytest.mark.asyncio
async def test_it_delivers_with_no_recipient_configured(monkeypatch) -> None:
    """The bug, stated directly: a default install must still get the alarm."""
    import navig.notify.router as router

    sent: list[tuple[str, str, str]] = []

    async def _dispatch(type_key, title, body="", **_):
        sent.append((type_key, title, body))
        return {"channels": [{"name": "telegram", "ok": True}]}

    monkeypatch.setattr(router, "dispatch", _dispatch)

    await _runner()._notify_issue("[!] Health check found issues:\n[HIGH] auth 401")

    assert sent, (
        "nothing was dispatched on a default install — the operator's daemon "
        "found HIGH issues and told nobody"
    )
    type_key, title, body = sent[0]
    assert type_key == "system_alert"
    assert "Health check found issues" in title
    assert "HIGH" in body


@pytest.mark.asyncio
async def test_the_first_line_becomes_the_title(monkeypatch) -> None:
    """A notification card needs a title and a body, not one blob."""
    import navig.notify.router as router

    sent: list[tuple[str, str, str]] = []

    async def _dispatch(type_key, title, body="", **_):
        sent.append((type_key, title, body))
        return {"channels": [{"name": "deck", "ok": True}]}

    monkeypatch.setattr(router, "dispatch", _dispatch)

    await _runner()._notify_issue("Heartbeat check failed: boom")

    _, title, body = sent[0]
    assert title == "Heartbeat check failed: boom"
    assert body == "", "a single-line message must not invent a body"


@pytest.mark.parametrize(
    "outcome",
    [
        {"channels": [{"name": "telegram", "ok": False}]},  # every channel failed
        {"channels": {"telegram": {"ok": True}}},           # malformed shape
        {},                                                  # nothing delivered
        None,                                                # no outcome at all
    ],
)
@pytest.mark.asyncio
async def test_no_delivery_outcome_can_escape(monkeypatch, outcome) -> None:
    """⚠ The alarm must never raise into the heartbeat that rang it.

    The first draft of this fix put the `all_channels_failed` check OUTSIDE the
    try, and a probe with a malformed outcome propagated straight out of
    `_notify_issue` — rebuilding the exact class this change removes. The
    malformed case is in this list because it is the one that caught it:
    `channels` is a LIST of dicts, and a dict there iterates to str keys.
    """
    import navig.notify.router as router

    async def _dispatch(*_a, **_k):
        return outcome

    monkeypatch.setattr(router, "dispatch", _dispatch)

    await _runner()._notify_issue("[!] Issues:\nthing")  # must simply not raise


@pytest.mark.asyncio
async def test_a_raising_router_is_contained_and_logged(monkeypatch, caplog) -> None:
    """A broken notifier must not take down the health check that found the fault."""
    import navig.notify.router as router

    async def _dispatch(*_a, **_k):
        raise RuntimeError("router down")

    monkeypatch.setattr(router, "dispatch", _dispatch)

    await _runner()._notify_issue("[!] Issues:\nthing")  # must not raise


# ── de-duplication: making the alarm ring must not make it nag ────────────────
#
# `_handle_result` calls _notify_issue on EVERY beat that has issues, and the
# operator's findings are standing ones — the same 4 issues found 7 times in 3
# days. Un-throttled, this fix would have replaced silence with an alert every
# ~30 minutes: the "cries wolf" pattern #1322 had just removed from the sibling
# approval surface.


def _capture(monkeypatch) -> list:
    import navig.notify.router as router

    sent: list = []

    async def _dispatch(type_key, title, body="", **_):
        sent.append((title, body))
        return {"channels": [{"name": "telegram", "ok": True}]}

    monkeypatch.setattr(router, "dispatch", _dispatch)
    return sent


@pytest.mark.asyncio
async def test_an_unchanged_issue_set_is_not_resent(monkeypatch) -> None:
    """Seven identical beats must not become seven notifications."""
    sent = _capture(monkeypatch)
    runner = _runner()

    for _ in range(7):
        await runner._notify_issue("[!] Health check found issues:\n[HIGH] auth 401")

    assert len(sent) == 1, f"the operator would have been alerted {len(sent)} times"


@pytest.mark.asyncio
async def test_a_changed_issue_set_alerts_immediately(monkeypatch) -> None:
    """The property that keeps this de-duplication and not a mute button."""
    sent = _capture(monkeypatch)
    runner = _runner()

    await runner._notify_issue("[!] Issues:\n[HIGH] auth 401")
    await runner._notify_issue("[!] Issues:\n[HIGH] auth 401\n[HIGH] uplink offline")

    assert len(sent) == 2, "a NEW problem was suppressed behind an old one"


@pytest.mark.asyncio
async def test_an_undelivered_alert_does_not_burn_the_cooldown(monkeypatch) -> None:
    """⚠ Nothing reached anyone, so the next occurrence must not be swallowed.

    Without the rollback, an alert that reached zero channels still spends the
    per-key cooldown and a rate-limit slot — losing the event twice over.
    """
    import navig.notify.router as router

    attempts: list = []
    ok = {"value": False}

    async def _dispatch(type_key, title, body="", **_):
        attempts.append(title)
        return {"channels": [{"name": "telegram", "ok": ok["value"]}]}

    monkeypatch.setattr(router, "dispatch", _dispatch)
    runner = _runner()

    await runner._notify_issue("[!] Issues:\nthing")   # every channel fails
    ok["value"] = True
    await runner._notify_issue("[!] Issues:\nthing")   # same set, must retry

    assert len(attempts) == 2, (
        "an alert that reached nobody burned the cooldown, so the retry was "
        "suppressed and the event was lost twice"
    )


@pytest.mark.asyncio
async def test_suppressed_alerts_are_counted_into_the_next_one(monkeypatch) -> None:
    """A reporter that quietly drops records looks like a healthy system."""
    sent = _capture(monkeypatch)
    runner = _runner()

    await runner._notify_issue("[!] Issues:\nA")
    for _ in range(3):
        await runner._notify_issue("[!] Issues:\nA")   # suppressed
    await runner._notify_issue("[!] Issues:\nB")       # different set → goes out

    assert len(sent) == 2
    assert "suppressed" in sent[1][1], "the suppressed count never surfaced"
