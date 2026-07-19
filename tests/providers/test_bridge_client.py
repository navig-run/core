"""
Phase 4 — bridge_client + PiDriver round-trip against a fake bridge (Python,
no Node needed). Proves the JSON-RPC-2.0-over-stdio contract end-to-end:
handshake, validate, startAuth/authStatus, listModels, and clean shutdown.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from navig.providers.bridge_client import BridgeClient, BridgeError, PiDriver
from navig.providers.connection_types import HealthState

FAKE = str(Path(__file__).parent / "fake_bridge.py")


def _client() -> BridgeClient:
    return BridgeClient(command=sys.executable, args=[FAKE])


async def test_handshake_and_server_info():
    c = _client()
    await c.start()
    try:
        assert c.server_info.get("protocol") == "1.0"
        assert "validate" in c.server_info.get("methods", [])
    finally:
        await c.shutdown()


async def test_pidriver_validate_round_trip():
    c = _client()
    await c.start()
    try:
        d = PiDriver(c)
        res = await d.validate(secret_ref="ref", endpoint=None, model=None)
        assert res.ok and res.health == HealthState.HEALTHY.value
        assert {m.id for m in res.models} == {"gpt-4o", "o4-mini"}
    finally:
        await c.shutdown()


async def test_pidriver_device_auth_flow():
    c = _client()
    await c.start()
    try:
        d = PiDriver(c)
        start = await d.start_auth("codex")
        assert start.flow == "device_code" and start.user_code == "WXYZ-1234"
        status = await d.auth_status(start.handle)
        assert status.state == "connected" and status.secret_ref
    finally:
        await c.shutdown()


async def test_pidriver_list_models_and_refresh():
    c = _client()
    await c.start()
    try:
        d = PiDriver(c)
        models = await d.list_models(secret_ref="ref")
        assert [m.id for m in models] == ["gpt-4o"]
        st = await d.refresh(secret_ref="ref")
        assert st.state == "connected" and st.secret_ref == "ref"
    finally:
        await c.shutdown()


async def test_protocol_mismatch_raises(monkeypatch):
    # Point the client at a bridge that returns a wrong protocol major.
    bad = str(Path(__file__).parent / "fake_bridge_badproto.py")
    Path(bad).write_text(
        "import json,sys\n"
        "for line in sys.stdin:\n"
        "    req=json.loads(line)\n"
        "    print(json.dumps({'jsonrpc':'2.0','id':req.get('id'),'result':{'protocol':'9.0'}}));sys.stdout.flush()\n",
        encoding="utf-8",
    )
    c = BridgeClient(command=sys.executable, args=[bad])
    with pytest.raises(BridgeError):
        await c.start()
    Path(bad).unlink(missing_ok=True)
