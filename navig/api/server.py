from __future__ import annotations

import asyncio
import signal

from navig.core.background import spawn
from navig.gateway.server import GatewayConfig, NavigGateway


def run_api_server(host: str = "127.0.0.1", port: int = 7002) -> None:
    """Start gateway-backed API server with explicit host/port.

    ⚠ This starts a REAL ``NavigGateway`` (it is what ``navig webdash`` runs), so
    it must read the operator's config for the same reason ``gateway start``
    does. Built from a three-key literal, ``config.auth_token`` was always None,
    so ``start()`` minted a fresh token and PERSISTED it over ``config.yaml``
    every time the dashboard was launched — and ``gateway.policy`` deny-rules
    resolved to the default gate, which fails open.

    host/port are overlaid on top because they are this command's own arguments.
    """
    from navig.config import get_config_manager

    raw = dict(get_config_manager().global_config or {})
    section = dict(raw.get("gateway") or {})
    section.update({"host": host, "port": port, "enabled": True})
    raw["gateway"] = section
    cfg = GatewayConfig(raw)
    gateway = NavigGateway(cfg)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _signal_handler() -> None:
        spawn(gateway.stop())

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _signal_handler)
        except NotImplementedError:
            pass

    try:
        loop.run_until_complete(gateway.start())
    except KeyboardInterrupt:
        loop.run_until_complete(gateway.stop())
    finally:
        loop.close()
