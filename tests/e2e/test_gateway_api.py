import asyncio
import pytest
import aiohttp
from navig.gateway.server import NavigGateway, GatewayConfig

@pytest.fixture
async def test_gateway():
    """Setup a test gateway bound to a random port."""
    config = GatewayConfig()
    config.host = "127.0.0.1"
    config.port = 8791 # isolated port
    config.enabled = True
    config.heartbeat_enabled = False
    
    gateway = NavigGateway(config)
    
    # Run gateway in background task
    task = asyncio.create_task(gateway.start())
    
    # Wait for server to boot
    await asyncio.sleep(1)
    
    yield gateway
    
    # Shutdown
    await gateway.stop()
    await task

@pytest.mark.asyncio
async def test_gateway_health_endpoint(test_gateway):
    """Test that the core API boots and serves health checks."""
    async with aiohttp.ClientSession() as session:
        async with session.get("http://127.0.0.1:8791/health") as response:
            assert response.status == 200
            data = await response.json()
            assert data.get("ok") is True
            assert data["data"]["status"] == "ok"
            assert "timestamp" in data["data"]

@pytest.mark.asyncio
async def test_gateway_status_endpoint(test_gateway):
    """Test that the core API boots and serves status checks."""
    async with aiohttp.ClientSession() as session:
        async with session.get("http://127.0.0.1:8791/status") as response:
            assert response.status == 200
            data = await response.json()
            assert data.get("ok") is True
            assert data["data"]["status"] == "running"
            assert data["data"]["config"]["port"] == 8791
