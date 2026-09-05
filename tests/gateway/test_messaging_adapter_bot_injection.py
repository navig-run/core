"""The Telegram messaging adapter must actually receive a bot.

`_init_messaging_adapters` reached for the live bot through
``ChannelRegistry.instance()`` — a classmethod that has never existed; the registry
only ever exposed the module-level ``get_channel_registry()``. The call was written
defensively:

    chan_registry = ChannelRegistry.instance() if hasattr(ChannelRegistry, "instance") else None
    if chan_registry:
        ...
        tg_adapter.set_bot(bot)

so `hasattr` was permanently False, the branch never ran, and `set_bot` was never
called once — it has exactly one call site in the whole tree, inside that branch. The
adapter was registered with ``_bot = None`` on every install, and every send through
it returned ``DeliveryReceipt.failure("Telegram bot not initialised")``.

The live ``TelegramChannel`` is the object the adapter was built for: ``_msg_id``
documents that it accepts "a dict (channel) or object (PTB Message)", and the dict is
what this channel returns. `test_channel_satisfies_the_adapter_bot_interface` pins that
correspondence so the two cannot drift apart again silently.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def gateway(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from navig.gateway import server as sv

    config_dir = tmp_path / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    cm = SimpleNamespace(
        global_config={
            "gateway": {"enabled": True, "port": 8789, "host": "127.0.0.1"},
            "heartbeat": {},
            "mesh": {"enabled": False},
            "agents": {},
            "cron": {},
            "adapters": {},  # telegram defaults to enabled; everything else off
        },
        global_config_dir=config_dir,
    )
    monkeypatch.setattr(sv, "get_config_manager", lambda: cm)
    return sv.NavigGateway(), sv


@pytest.fixture
def captured_registry(monkeypatch: pytest.MonkeyPatch):
    """Capture adapters registered during init instead of touching the singleton."""
    from navig.messaging import adapter_registry as ar

    registered: list = []
    fake = MagicMock()
    fake.register = registered.append
    monkeypatch.setattr(ar, "get_adapter_registry", lambda: fake)
    return registered


def _fake_channel():
    """Stands in for the live TelegramChannel."""
    ch = MagicMock()
    ch.send_message = AsyncMock(return_value={"message_id": 42})
    return ch


async def test_telegram_adapter_receives_the_live_channel_as_its_bot(
    gateway, captured_registry
):
    gw, _sv = gateway
    channel = _fake_channel()
    gw.channels = {"telegram": channel}

    await gw._init_messaging_adapters()

    tg = [a for a in captured_registry if getattr(a, "name", "") == "telegram"]
    assert tg, "the telegram adapter must still be registered"
    assert tg[0]._bot is channel, (
        "the adapter was registered without a bot — every send through it returns "
        "DeliveryReceipt.failure('Telegram bot not initialised')"
    )


async def test_a_bot_less_adapter_reports_failure_not_success():
    """Why the missing injection mattered: the adapter does not fail loudly at
    registration, only later, once per send."""
    from navig.messaging.adapters.telegram_adapter import TelegramMessagingAdapter

    adapter = TelegramMessagingAdapter()
    assert adapter._bot is None

    receipt = await adapter.send_message("123", "hello")
    assert not receipt.ok
    assert "not initialised" in (receipt.error or "").lower()


async def test_missing_channel_warns_and_still_registers(
    gateway, captured_registry, navig_log_capture
):
    """Empty state: no telegram channel running. The adapter still registers (it
    resolves targets and ingests webhooks), but the operator is told sends will fail.

    navig_log_capture (not caplog): navig's loggers set propagate=False.
    """
    gw, _sv = gateway
    gw.channels = {}

    await gw._init_messaging_adapters()

    tg = [a for a in captured_registry if getattr(a, "name", "") == "telegram"]
    assert tg and tg[0]._bot is None
    assert any("WITHOUT a bot" in m for m in navig_log_capture), (
        "registering a send-incapable adapter in silence is how this stayed hidden"
    )


def test_channel_satisfies_the_adapter_bot_interface():
    """Pin the correspondence the injection depends on.

    The adapter calls these on whatever `set_bot` was handed. If `TelegramChannel`
    renames a parameter or drops a method, the send fails at RUNTIME inside a
    `try/except` that logs and returns a failure receipt — i.e. quietly. This is the
    cheap place to find out instead.
    """
    from navig.gateway.channels.telegram import TelegramChannel

    # send_message is called entirely by keyword.
    params = inspect.signature(TelegramChannel.send_message).parameters
    for kw in ("chat_id", "text", "parse_mode"):
        assert kw in params, f"adapter calls send_message({kw}=...)"

    # Media: chat_id + payload positionally, the rest by keyword.
    media_kwargs = {
        "send_photo": ("caption",),
        "send_video": ("caption",),
        "send_animation": ("caption",),
        "send_document": ("filename", "caption"),
        "send_voice": (),
    }
    for method, kwargs in media_kwargs.items():
        fn = getattr(TelegramChannel, method, None)
        assert fn is not None, f"adapter dispatches attachments through {method}"
        sig = inspect.signature(fn).parameters
        positional = [
            p
            for p in sig.values()
            if p.name != "self" and p.kind is not inspect.Parameter.KEYWORD_ONLY
        ]
        assert len(positional) >= 2, (
            f"{method} must accept (chat_id, data) positionally — adapter calls it that way"
        )
        for kw in kwargs:
            assert kw in sig, f"adapter calls {method}(..., {kw}=...)"


def test_msg_id_reads_the_channels_dict_return():
    """The channel returns a dict, not a PTB Message object. `_msg_id` handles both —
    that is the documented reason this pairing works at all."""
    from navig.messaging.adapters.telegram_adapter import _msg_id

    assert _msg_id({"message_id": 7}) == "7"
    assert _msg_id(SimpleNamespace(message_id=7)) == "7"
    assert _msg_id(None) == ""
