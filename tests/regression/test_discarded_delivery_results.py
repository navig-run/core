"""A `-> bool` that means "did it actually happen" must not be dropped by a caller
that then behaves as if it succeeded.

Three live sites, each measured before the fix:

* `WhatsAppChannel._handle_incoming_message` awaited `_send_message(...)` twice —
  once for the real reply, once inside the `except` handler — and discarded both.
  `_send_message` returns False for a bridge error, a non-200 or a timeout, so a
  failed reply was indistinguishable from a delivered one: the user got silence
  and nothing recorded that the computed answer never reached them. The error-path
  drop is worse — no answer *and* no apology.
* `TriggerManager._execute_trigger` mutated the trigger (`fire_count`,
  `record_fire`) and then discarded `update_trigger`'s result. That window is what
  `max_fires_per_hour` is enforced from, and it lives only in the saved file — so
  a trigger that cannot persist its fire history can fire without limit after the
  next reload. Note `tests/commands/test_trigger_save_failure_is_reported.py`
  already fixed the *callee* — `update_trigger` returns False on a failed write
  instead of claiming success — which is exactly why this mattered: the honest
  bool existed and the one caller that fires triggers threw it away.
* `TunnelManager.restart_tunnel` discarded `stop_tunnel`. That bool is False for
  two different things — "nothing to stop" and "still alive, could not kill it"
  (AccessDenied) — and the second path deletes the cache entry anyway. So
  `start_tunnel` saw no tunnel, found the expected port occupied by the survivor,
  and quietly picked a *different* port: restart reported success while the
  original ssh process kept running forever, untracked.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

# ── WhatsApp ────────────────────────────────────────────────────────────────


def _whatsapp_channel(send_result: bool):
    from navig.gateway.channels.whatsapp import WhatsAppChannel

    channel = WhatsAppChannel.__new__(WhatsAppChannel)
    channel._bot_number = None
    channel._should_respond = MagicMock(return_value=True)
    channel._build_metadata = MagicMock(return_value={})
    channel._send_message = AsyncMock(return_value=send_result)
    return channel


def _whatsapp_message():
    msg = MagicMock()
    msg.content = "hello"
    msg.is_mentioned = False
    msg.is_group = False
    msg.from_number = "1555"
    msg.group_id = None
    return msg


async def test_a_failed_whatsapp_reply_is_reported(caplog) -> None:
    channel = _whatsapp_channel(send_result=False)
    channel.message_handler = AsyncMock(return_value="the answer")

    with caplog.at_level(logging.ERROR, logger="navig.gateway.channels.whatsapp"):
        await channel._handle_incoming_message(_whatsapp_message())

    assert channel._send_message.await_count == 1
    assert any("NOT delivered" in r.message for r in caplog.records), (
        f"a dropped reply must be visible; got {[r.message for r in caplog.records]}"
    )


async def test_a_failed_whatsapp_error_notice_is_reported(caplog) -> None:
    """The user gets no answer AND no apology — the loudest case."""
    channel = _whatsapp_channel(send_result=False)
    channel.message_handler = AsyncMock(side_effect=RuntimeError("handler exploded"))

    with caplog.at_level(logging.ERROR, logger="navig.gateway.channels.whatsapp"):
        await channel._handle_incoming_message(_whatsapp_message())

    assert channel._send_message.await_count == 1
    assert any("no reply at all" in r.message for r in caplog.records), (
        f"got {[r.message for r in caplog.records]}"
    )


async def test_a_delivered_whatsapp_reply_stays_quiet(caplog) -> None:
    """Anti-vacuity partner: the success path must not log an error."""
    channel = _whatsapp_channel(send_result=True)
    channel.message_handler = AsyncMock(return_value="the answer")

    with caplog.at_level(logging.ERROR, logger="navig.gateway.channels.whatsapp"):
        await channel._handle_incoming_message(_whatsapp_message())

    assert channel._send_message.await_count == 1
    assert not [r for r in caplog.records if "NOT delivered" in r.message]


# ── Triggers ────────────────────────────────────────────────────────────────


def _trigger_manager(save_ok: bool, tmp_path):
    from navig.commands.triggers import TriggerManager

    mgr = TriggerManager.__new__(TriggerManager)
    mgr._triggers = {}
    mgr._loaded = True
    mgr._ensure_loaded = MagicMock()
    mgr._save_triggers = MagicMock(return_value=save_ok)
    return mgr


def _trigger():
    from navig.commands.triggers import Trigger, TriggerType

    return Trigger(id="t1", name="probe", type=TriggerType.MANUAL)


def test_a_trigger_whose_fire_state_cannot_be_saved_is_reported(caplog, tmp_path) -> None:
    from navig.commands.triggers import TriggerEvent, TriggerType

    mgr = _trigger_manager(save_ok=False, tmp_path=tmp_path)
    trigger = _trigger()
    mgr._triggers[trigger.id] = trigger
    trigger.actions = []

    with caplog.at_level(logging.ERROR, logger="navig.commands.triggers"):
        mgr._execute_trigger(trigger, TriggerEvent(type=TriggerType.MANUAL, source="test", data={}))

    assert trigger.fire_count == 1, "the in-memory trigger was mutated"
    assert any("could not be saved" in r.message for r in caplog.records), (
        "a lost fire window silently disables max_fires_per_hour across restarts; "
        f"got {[r.message for r in caplog.records]}"
    )


def test_a_trigger_that_saves_stays_quiet(caplog, tmp_path) -> None:
    """Anti-vacuity partner."""
    from navig.commands.triggers import TriggerEvent, TriggerType

    mgr = _trigger_manager(save_ok=True, tmp_path=tmp_path)
    trigger = _trigger()
    mgr._triggers[trigger.id] = trigger
    trigger.actions = []

    with caplog.at_level(logging.ERROR, logger="navig.commands.triggers"):
        mgr._execute_trigger(trigger, TriggerEvent(type=TriggerType.MANUAL, source="test", data={}))

    assert not [r for r in caplog.records if "could not be saved" in r.message]


# ── Tunnel ──────────────────────────────────────────────────────────────────


def _tunnel_manager(status, stop_result: bool):
    from navig.tunnel import TunnelManager

    mgr = TunnelManager.__new__(TunnelManager)
    mgr.config = MagicMock()
    mgr._log = MagicMock()
    mgr.get_tunnel_status = MagicMock(return_value=status)
    mgr.stop_tunnel = MagicMock(return_value=stop_result)
    mgr.start_tunnel = MagicMock(return_value={"local_port": 3307})
    return mgr


def test_restart_refuses_when_the_running_tunnel_could_not_be_stopped() -> None:
    mgr = _tunnel_manager(status={"pid": 4242, "local_port": 3307}, stop_result=False)

    with pytest.raises(RuntimeError, match="Could not stop the running tunnel"):
        mgr.restart_tunnel("prod")

    assert mgr.start_tunnel.call_count == 0, (
        "starting a second tunnel over a survivor orphans the first on a port "
        "nobody is tracking"
    )


def test_restart_proceeds_when_there_was_nothing_to_stop() -> None:
    """The other meaning of False. `stop_tunnel` returns False for 'already
    stopped' too, so keying only on the bool would break the normal restart."""
    mgr = _tunnel_manager(status=None, stop_result=False)

    result = mgr.restart_tunnel("prod")

    assert result == {"local_port": 3307}
    assert mgr.start_tunnel.call_count == 1


def test_restart_proceeds_on_a_clean_stop() -> None:
    mgr = _tunnel_manager(status={"pid": 4242, "local_port": 3307}, stop_result=True)

    result = mgr.restart_tunnel("prod")

    assert result == {"local_port": 3307}
    assert mgr.start_tunnel.call_count == 1
