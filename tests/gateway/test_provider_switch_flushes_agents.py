"""Switching provider/model must flush the cached ConversationalAgent.

`_refresh_ai_runtime_after_router_update` resets the routers and then flushes the agent cache
so the NEXT message resolves through the new provider. On a TelegramChannel that flush found
nothing to call, through two independent defects in the same three lines:

    flush_fn = getattr(self, "flush_conv_agents", None)   # lives on ChannelRouter, not here
    if flush_fn is None:
        gw = getattr(self, "gateway", None)               # the channel never stored one
        flush_fn = getattr(getattr(gw, "channel_router", None), ...)   # it is `router`

Both lookups default to None and the result is only ever `if callable(flush_fn)`, so the
whole thing was a silent no-op: the user changed model and the next reply still came from the
old one.

`create_telegram_channel` had the gateway all along -- it captured it in the `handle_message`
closure and never put it on the object.
"""

from __future__ import annotations

import pytest

from navig.gateway.channels.telegram import TelegramChannel, create_telegram_channel

pytestmark = pytest.mark.integration


class _Router:
    def __init__(self):
        self.flushed = 0

    def flush_conv_agents(self):
        self.flushed += 1


class _Gateway:
    """Shaped like NavigGateway: the ChannelRouter lives on `.router`."""

    def __init__(self):
        self.router = _Router()


def test_the_channel_keeps_a_reference_to_its_gateway():
    """It was only ever in a closure, so the object could not reach it."""
    gw = _Gateway()
    ch = create_telegram_channel(gw, {"bot_token": "123:ABC"})
    assert ch is not None
    assert getattr(ch, "gateway", None) is gw, (
        "without this the documented 'self is not a ChannelRouter subclass' fallback "
        "resolves to None and the agent cache is never flushed"
    )


def test_the_fallback_reads_the_attribute_that_exists():
    """`gateway.router`, not `gateway.channel_router`.

    NavigGateway does `self.router = ChannelRouter(self)`. The name "channel_router" appeared
    as an attribute in exactly one line of the tree -- the broken lookup itself.
    """
    gw = _Gateway()
    assert hasattr(gw, "router") and not hasattr(gw, "channel_router")
    ch = create_telegram_channel(gw, {"bot_token": "123:ABC"})
    ch._refresh_ai_runtime_after_router_update()
    assert gw.router.flushed == 1, (
        "the provider switch did not reach flush_conv_agents; the next message would still "
        "be answered by the previous provider"
    )


def test_a_channel_with_no_gateway_still_does_not_raise():
    """The path is best-effort by design -- it must degrade, not crash a settings change."""
    ch = TelegramChannel.__new__(TelegramChannel)
    ch._refresh_ai_runtime_after_router_update()   # must not raise


def test_a_direct_flush_conv_agents_still_wins():
    """When self IS the router, the first branch is used and the gateway is not consulted."""
    ch = TelegramChannel.__new__(TelegramChannel)
    calls = []
    ch.flush_conv_agents = lambda: calls.append(1)
    ch.gateway = _Gateway()
    ch._refresh_ai_runtime_after_router_update()
    assert calls == [1], "the direct method must be preferred"
    assert ch.gateway.router.flushed == 0, "and the fallback must not also fire"
