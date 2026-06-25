"""NAVIG Cloud package.

In-daemon Cloudflare quick-tunnel manager + broker client. When
``config.cloud.enabled`` is true, the gateway spawns a managed
``cloudflared tunnel --url http://127.0.0.1:<port>`` child, scrapes the
resulting ``https://*.trycloudflare.com`` URL, and registers it with the
hosted relay at ``relay.navig.run`` so it can resolve "where is my
daemon" by api_key or Telegram user_id — over cloudflared + outbound uplink,
no VPS.

Public surface:

- ``CloudManager``    -- lifecycle owner, wired into the gateway lifespan.
- ``BrokerClient``    -- async HTTP client for ``/api/cloud/*``.
- ``UplinkClient``    -- outbound WebSocket to a self-hosted Lighthouse edge
  (``cloud.mode=lighthouse``), the zero-tunnel transport.
- ``ensure_cloudflared`` -- platform-detect + checksum-verified installer.
"""

from navig.cloud.broker_client import BrokerClient, BrokerError
from navig.cloud.installer import ensure_cloudflared, InstallerError
from navig.cloud.manager import CloudManager, CloudStatus
from navig.cloud.uplink import UplinkClient, api_key_hash

__all__ = [
    "BrokerClient",
    "BrokerError",
    "CloudManager",
    "CloudStatus",
    "InstallerError",
    "UplinkClient",
    "api_key_hash",
    "ensure_cloudflared",
]
