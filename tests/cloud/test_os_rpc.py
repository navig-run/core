"""`navig.cloud.os_rpc` — the sync envelope client the session-goal CLI rides on.

A real ``websockets.sync.server`` stands in for the NAVIG OS server, speaking the
same JSON envelope protocol the TypeScript transport speaks (handshake →
handshake_ack → request → response). What is pinned:

  * a successful round-trip returns ``result`` and skips interleaved event frames;
  * a bad token maps to an operator-actionable AUTH_FAILED message;
  * no descriptor (OS not running) is ``NO_SERVER``, never a stack trace;
  * a server that accepts but never answers hits the call timeout;
  * ``try_call`` swallows only "not running", never real failures.
"""

from __future__ import annotations

import json
import socket
import threading
import time

import pytest

websockets = pytest.importorskip("websockets")
from websockets.sync.server import serve  # noqa: E402

from navig.cloud import os_discovery  # noqa: E402
from navig.cloud.os_rpc import OsRpcError, call, try_call  # noqa: E402

OS_TOKEN = "os-token-0123456789abcdef"


class FakeOs:
    """A NAVIG OS server: handshake, then a scripted response per request."""

    def __init__(self, *, token: str = OS_TOKEN, responder=None) -> None:
        self.token = token
        self.responder = responder  # env -> list of frames to send (default: echo result)
        self.handshakes: list[dict] = []
        self.requests: list[dict] = []

    def handler(self, conn) -> None:
        try:
            raw = conn.recv(timeout=5)
        except Exception:  # noqa: BLE001 — client vanished before handshake
            return
        env = json.loads(raw)
        self.handshakes.append(env)
        if env.get("type") != "handshake" or env.get("token") != self.token:
            conn.send(json.dumps({
                "id": env.get("id", ""), "type": "error",
                "error": {"code": "AUTH_FAILED", "message": "Invalid token"},
            }))
            return
        conn.send(json.dumps({
            "id": env.get("id", ""), "type": "handshake_ack", "clientId": "c-test",
        }))
        while True:
            try:
                raw = conn.recv(timeout=10)
            except Exception:  # noqa: BLE001 — client closed or idle timeout
                return
            try:
                env = json.loads(raw)
            except ValueError:
                continue
            if env.get("type") != "request":
                continue
            self.requests.append(env)
            if self.responder is None:
                frames = [{
                    "id": env.get("id"), "type": "response",
                    "result": {"channel": env.get("channel"), "args": env.get("args")},
                }]
            else:
                frames = self.responder(env)
            for frame in frames:
                conn.send(json.dumps(frame))


@pytest.fixture
def os_server(monkeypatch):
    """Start a stub OS server on an ephemeral loopback port; point the descriptor at it."""
    state: dict = {"os": None, "port": None, "shutdown": None, "thread": None}

    def start(os: FakeOs):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        sock.listen(8)
        port = sock.getsockname()[1]
        server = serve(os.handler, sock=sock)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        state.update(os=os, port=port, shutdown=server, thread=thread)
        monkeypatch.setattr(
            os_discovery, "read_descriptor",
            lambda path=None: {"ws_url": f"ws://127.0.0.1:{port}", "token": OS_TOKEN,
                               "pid": 4242, "kind": "desktop", "protocol": "1.0"},
        )
        return port

    state["start"] = start
    yield state
    if state["shutdown"] is not None:
        state["shutdown"].shutdown()
        state["thread"].join(timeout=5)


def test_roundtrip_returns_result_and_skips_events(os_server):
    def responder(env):
        # An event and a sequence ack pushed BEFORE the response must be ignored.
        return [
            {"id": "e1", "type": "event", "channel": "sessions:goal_changed", "args": [{}]},
            {"id": "e1", "type": "sequence_ack", "seq": 1},
            {"id": env["id"], "type": "response", "result": {"ok": True, "n": 7}},
        ]

    os_server["start"](FakeOs(responder=responder))
    assert call("sessions:getGoalConfig") == {"ok": True, "n": 7}


def test_request_args_are_forwarded(os_server):
    os_server["start"](FakeOs())
    result = call("sessions:command", "sess-1", {"type": "setGoal", "objective": "ship it"})
    assert result == {
        "channel": "sessions:command",
        "args": ["sess-1", {"type": "setGoal", "objective": "ship it"}],
    }


def test_handshake_carries_protocol_and_token(os_server):
    fake = FakeOs()
    os_server["start"](fake)
    call("sessions:get")
    assert len(fake.handshakes) == 1
    assert fake.handshakes[0]["type"] == "handshake"
    assert fake.handshakes[0]["protocolVersion"] == "1.0"
    assert fake.handshakes[0]["token"] == OS_TOKEN


def test_bad_token_maps_to_stale_os_token_message(os_server):
    # Descriptor advertises a token the server no longer accepts (restart happened).
    os_server["start"](FakeOs(token="rotated-token-9999999999999999999"))
    with pytest.raises(OsRpcError) as exc:
        call("sessions:get")
    assert exc.value.code == "AUTH_FAILED"
    assert "stale OS token" in str(exc.value)


def test_no_descriptor_is_no_server(os_server, monkeypatch):
    monkeypatch.setattr(os_discovery, "read_descriptor", lambda path=None: None)
    with pytest.raises(OsRpcError) as exc:
        call("sessions:get")
    assert exc.value.code == "NO_SERVER"
    assert "NAVIG OS is not running" in str(exc.value)


def test_unreachable_port_is_unreachable(os_server, monkeypatch):
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    dead_port = sock.getsockname()[1]
    sock.close()  # nothing listening now
    monkeypatch.setattr(
        os_discovery, "read_descriptor",
        lambda path=None: {"ws_url": f"ws://127.0.0.1:{dead_port}", "token": OS_TOKEN},
    )
    with pytest.raises(OsRpcError) as exc:
        call("sessions:get")
    assert exc.value.code == "UNREACHABLE"


def test_handler_error_maps_channel_not_found(os_server):
    def responder(env):
        return [{
            "id": env["id"], "type": "response",
            "error": {"code": "CHANNEL_NOT_FOUND", "message": "sessions:getGoalConfig"},
        }]

    os_server["start"](FakeOs(responder=responder))
    with pytest.raises(OsRpcError) as exc:
        call("sessions:getGoalConfig")
    assert exc.value.code == "CHANNEL_NOT_FOUND"
    assert "too old" in str(exc.value)


def test_silent_server_times_out(os_server):
    os_server["start"](FakeOs(responder=lambda env: []))  # handshake ok, never answers
    started = time.monotonic()
    with pytest.raises(OsRpcError) as exc:
        call("sessions:get", timeout=0.4)
    assert exc.value.code == "TIMEOUT"
    assert time.monotonic() - started < 5


def test_try_call_returns_none_only_when_not_running(os_server, monkeypatch):
    monkeypatch.setattr(os_discovery, "read_descriptor", lambda path=None: None)
    assert try_call("sessions:get") is None

    def responder(env):
        return [{
            "id": env["id"], "type": "response",
            "error": {"code": "HANDLER_ERROR", "message": "boom"},
        }]

    os_server["start"](FakeOs(responder=responder))
    with pytest.raises(OsRpcError) as exc:  # real failures must NOT be swallowed
        try_call("sessions:command", "s", {"type": "nope"})
    assert exc.value.code == "HANDLER_ERROR"
