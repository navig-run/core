from __future__ import annotations

import asyncio
import signal

from navig.gateway.server import GatewayConfig, NavigGateway


def run_api_server(host: str = "127.0.0.1", port: int = 7002) -> None:
    """Start gateway-backed API server with explicit host/port."""
    cfg = GatewayConfig({"gateway": {"host": host, "port": port, "enabled": True}})
    gateway = NavigGateway(cfg)

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _signal_handler() -> None:
        loop.create_task(gateway.stop())

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
