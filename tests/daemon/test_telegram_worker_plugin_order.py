"""The telegram worker must load plugins BEFORE it registers deck routes.

The worker (``navig.daemon.telegram_worker``) and the dedicated ``gateway`` child
both mount the deck API. Only the gateway loaded entry-point plugins, so the
worker's listener served core routes alone — plugin routes 404'd on whichever
process bound ``gateway.config.port`` first (measured 2026-10-17: worker held
8789, gateway fell back to 8790) — and any callback query the worker handled
would find no plugin button prefixes (``mr:`` …) registered.

The loader itself is patched here: running the real one inside a test process
would register ~30 plugins' hooks globally for the rest of the pytest worker
(process-global pollution), which is exactly what these tests must not do.
"""

from __future__ import annotations

import asyncio
import types
from unittest import mock

import navig.core.plugins
import navig.gateway.deck
import navig.gateway.routes
import navig.platform.accept_guard
from navig.daemon.telegram_worker import _start_gateway_http


def _run_worker_http(order: list[str], *, loader_raises: bool = False) -> None:
    def _load() -> list[str]:
        order.append("load")
        if loader_raises:
            raise RuntimeError("simulated plugin-loader failure")
        return ["fake"]

    def _all(app, gw) -> None:
        order.append("all_routes")

    def _deck(app, **kwargs) -> None:
        order.append("deck_routes")

    class _GW:
        running = False
        config = types.SimpleNamespace(host="127.0.0.1", port=0)
        _runner = None
        _app = None

        async def _cors_middleware(self, request, handler):  # never hit: no requests
            return None

    gw = _GW()
    tg_config = {"bot_token": "x", "allowed_users": [], "require_auth": True}
    deck_cfg = {"enabled": True}

    asyncio.run(_boot(order, gw, tg_config, deck_cfg, _load, _all, _deck))


async def _boot(order, gw, tg_config, deck_cfg, _load, _all, _deck) -> None:
    with mock.patch.object(navig.core.plugins, "load_entry_point_plugins", _load), \
            mock.patch.object(navig.gateway.routes, "register_all_routes", _all), \
            mock.patch.object(navig.gateway.deck, "register_deck_routes", _deck), \
            mock.patch.object(navig.platform.accept_guard, "install", lambda: None):
        try:
            await _start_gateway_http(gw, tg_config, deck_cfg)
        finally:
            if gw._runner is not None:
                await gw._runner.cleanup()


def test_worker_loads_plugins_before_registering_routes() -> None:
    order: list[str] = []
    _run_worker_http(order)
    # The plugin loader runs first, so `gateway:register_routes` (fired inside
    # register_deck_routes) has handlers to call — core routes still mount after.
    assert order == ["load", "all_routes", "deck_routes"]


def test_a_failing_plugin_loader_does_not_block_the_bot() -> None:
    order: list[str] = []
    _run_worker_http(order, loader_raises=True)
    # Best-effort contract: the boot continues past a broken plugin.
    assert "load" in order
    assert order[-2:] == ["all_routes", "deck_routes"]