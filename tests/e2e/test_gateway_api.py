import asyncio

import aiohttp
import pytest

from navig.gateway.server import GatewayConfig, NavigGateway

#: Every test here boots a REAL gateway — Telegram connection, cloud tunnel and all.
#: Under ``-n auto`` the four of them boot simultaneously and interfere: two report the
#: DEFAULT port and an unauthenticated ``/status``, so the suite fails on a clean tree
#: (verified on main) whenever the pre-push gate selects it. ``xdist_group`` keeps the
#: module on ONE worker, which is the isolation these tests always assumed.
pytestmark = [pytest.mark.slow, pytest.mark.xdist_group("gateway_e2e")]

#: This fixture boots a REAL gateway, and a gateway that starts without a token mints
#: one and persists it. This module does not isolate the config dir, so that write would
#: land in the operator's own ~/.navig/config.yaml. Owning the credential keeps the test
#: off their machine's state — and makes it exercise the authenticated path rather than
#: the "auth is effectively off" one it used to pass through.
GATEWAY_TEST_TOKEN = "e2e-gateway-test-token"


@pytest.fixture
async def test_gateway():
    """Setup a test gateway bound to a random port.

    Genuinely random, as the docstring always claimed. It used to pin 8791, which is
    not isolation at all: the gateway's bind SELF-HEALS onto a neighbouring port when
    the preferred one is taken (`_bind_candidates` → preferred+5, then the sticky
    last-bound port, then 0). Under `-n 4` two gateway tests collide, each lands on a
    port the other is asserting against, and the failure reads as nonsense —
    `assert 8789 == 8791` for a request that got HTTP 200.

    The false FAILURE is the harmless half. `test_gateway_health_endpoint` only checks
    a generic health payload, so a foreign gateway answering on that port passes it —
    a silent false PASS that proves nothing about the gateway this test started.

    Port 0 asks the OS for a free port, and `NavigGateway` writes the one it actually
    bound back to `config.port`, so tests read it from there instead of assuming.
    """
    config = GatewayConfig()
    config.host = "127.0.0.1"
    config.port = 0  # ephemeral — the OS picks a port nothing else holds
    config.enabled = True
    config.heartbeat_enabled = False
    # A gateway that starts without a token MINTS one and persists it — and this
    # fixture does not isolate the config dir, so the mint would land in the
    # operator's real ~/.navig/config.yaml. Owning the credential keeps the test
    # off their machine's state and makes it exercise the authenticated path
    # rather than the "auth is effectively off" one it used to.
    config.auth_token = GATEWAY_TEST_TOKEN

    gateway = NavigGateway(config)

    # Run gateway in background task
    task = asyncio.create_task(gateway.start())

    # Wait for server to boot
    await asyncio.sleep(1)

    yield gateway

    # Shutdown
    await gateway.stop()
    await task


async def test_gateway_health_endpoint(test_gateway):
    """Test that the core API boots and serves health checks."""
    port = test_gateway.config.port
    assert port, "the gateway must report the port it actually bound"
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{port}/health") as response:
            assert response.status == 200
            data = await response.json()
            assert data.get("ok") is True
            assert data["data"]["status"] == "ok"
            assert "timestamp" in data["data"]


async def test_gateway_status_endpoint(test_gateway):
    """Test that the core API boots and serves status checks."""
    port = test_gateway.config.port
    assert port, "the gateway must report the port it actually bound"
    async with aiohttp.ClientSession() as session:
        headers = {"Authorization": f"Bearer {GATEWAY_TEST_TOKEN}"}
        async with session.get(
            f"http://127.0.0.1:{port}/status", headers=headers
        ) as response:
            assert response.status == 200
            data = await response.json()
            assert data.get("ok") is True
            assert data["data"]["status"] == "running"
            # Same port the fixture read back — this is what makes the response
            # attributable to THIS gateway rather than whatever else is listening.
            assert data["data"]["config"]["port"] == port


async def test_status_requires_authentication(test_gateway):
    """`/status` is an authenticated route; `/health` is the anonymous liveness probe.

    That split was always the design — `_health` calls no auth helper and `_status`
    does — but with no token configured `require_bearer_auth` returned "open access",
    so the distinction never took effect and this suite passed without a credential.
    """
    port = test_gateway.config.port
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{port}/status") as response:
            assert response.status == 401, (
                "an unauthenticated caller read the gateway's status; the same open "
                "door lets one answer the agent's pending approvals"
            )

        async with session.get(
            f"http://127.0.0.1:{port}/status",
            headers={"Authorization": "Bearer wrong-token"},
        ) as response:
            assert response.status == 401


async def test_health_stays_anonymous(test_gateway):
    """Liveness must not need a credential — probes and supervisors rely on it."""
    port = test_gateway.config.port
    async with aiohttp.ClientSession() as session:
        async with session.get(f"http://127.0.0.1:{port}/health") as response:
            assert response.status == 200
