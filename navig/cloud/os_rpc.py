"""Synchronous RPC to a running NAVIG OS server over its loopback WebSocket.

The OS server announces itself at ``<config_dir>/run/os-server.json`` (see
``navig.cloud.os_discovery``); this module dials that endpoint and speaks the
NAVIG OS envelope protocol (``apps/os/packages/shared/src/protocol/types.ts``):

    → {"id": <uuid>, "type": "handshake", "protocolVersion": "1.0", "token": ...}
    ← {"id": ..., "type": "handshake_ack", "clientId": ...}   (or "error" + close)
    → {"id": <uuid>, "type": "request", "channel": "sessions:get", "args": [...]}
    ← {"id": ..., "type": "response", "result": ...}          (or "error")

Frames are plain JSON text; ``event``/``sequence_ack`` frames arriving between a
request and its response are skipped. Everything here is loopback-only —
``read_descriptor`` refuses any non-loopback URL before we ever connect.

CLI-facing failures raise :class:`OsRpcError` with a message the operator can act
on; callers translate it to an honest ``ch.error`` + exit 1.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "1.0"
HANDSHAKE_TIMEOUT_S = 5.0
DEFAULT_CALL_TIMEOUT_S = 10.0


class OsRpcError(RuntimeError):
    """A reachable, well-formed failure talking to the OS server."""

    def __init__(self, message: str, *, code: str = "OS_RPC") -> None:
        super().__init__(message)
        self.code = code


def _connect(url: str, *, open_timeout: float):
    try:
        from websockets.sync.client import connect
    except ImportError as exc:  # pragma: no cover — dependency declared in pyproject
        raise OsRpcError("websockets is not installed — pip install websockets", code="NO_WEBSOCKETS") from exc
    try:
        return connect(url, open_timeout=open_timeout, max_size=16 * 1024 * 1024)
    except OSError as exc:
        raise OsRpcError(
            f"cannot reach the NAVIG OS server at {url} — it may have just restarted",
            code="UNREACHABLE",
        ) from exc


def _recv_until(ws, want_id: str, *, timeout: float, want_types: tuple[str, ...]) -> dict[str, Any]:
    """Read frames until one whose ``id`` matches and whose type is in *want_types*.

    Events and sequence acks pushed by the server are ignored; a closed socket
    while waiting means the server went away mid-call.
    """
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OsRpcError(f"timed out after {timeout:.0f}s waiting for the OS server", code="TIMEOUT")
        try:
            raw = ws.recv(timeout=remaining)
        except TimeoutError as exc:
            raise OsRpcError(f"timed out after {timeout:.0f}s waiting for the OS server", code="TIMEOUT") from exc
        except Exception as exc:  # ConnectionClosed and friends
            raise OsRpcError("the NAVIG OS server closed the connection", code="DISCONNECTED") from exc
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        try:
            envelope = json.loads(raw)
        except ValueError:
            logger.debug("os_rpc: skipping unparseable frame")
            continue
        if not isinstance(envelope, dict):
            continue
        if envelope.get("type") == "error" and envelope.get("id") in (want_id, None):
            err = envelope.get("error") or {}
            raise OsRpcError(
                str(err.get("message") or "OS server rejected the connection"),
                code=str(err.get("code") or "ERROR"),
            )
        if envelope.get("id") == want_id and envelope.get("type") in want_types:
            return envelope
        # events / sequence_ack / unrelated frames — keep waiting


def _raise_wire_error(err: Any) -> None:
    if not isinstance(err, dict):
        raise OsRpcError("OS server returned a malformed error", code="MALFORMED")
    code = str(err.get("code") or "HANDLER_ERROR")
    message = str(err.get("message") or "unknown error")
    if code == "AUTH_FAILED":
        raise OsRpcError(
            "stale OS token — restart the NAVIG OS app (or `navig os serve`) and retry",
            code=code,
        )
    if code == "CHANNEL_NOT_FOUND":
        raise OsRpcError(f"NAVIG OS is too old for this command ({message})", code=code)
    if code == "PROTOCOL_VERSION_UNSUPPORTED":
        raise OsRpcError(f"NAVIG OS protocol mismatch — {message}", code=code)
    raise OsRpcError(message, code=code)


def call(channel: str, *args: Any, timeout: float = DEFAULT_CALL_TIMEOUT_S) -> Any:
    """One request/response round-trip against the running NAVIG OS server.

    Raises :class:`OsRpcError` — ``code="NO_SERVER"`` when no descriptor exists,
    otherwise the wire error code with an operator-actionable message.
    """
    from navig.cloud.os_discovery import read_descriptor

    info = read_descriptor()
    if not info:
        raise OsRpcError(
            "NAVIG OS is not running (no server descriptor) — open NAVIG OS, "
            "or start it announced with `navig os serve`",
            code="NO_SERVER",
        )

    url = str(info.get("ws_url") or "")
    token = str(info.get("token") or "")
    req_id = uuid.uuid4().hex

    with _connect(url, open_timeout=HANDSHAKE_TIMEOUT_S) as ws:
        ws.send(json.dumps({
            "id": uuid.uuid4().hex,
            "type": "handshake",
            "protocolVersion": PROTOCOL_VERSION,
            "token": token,
        }, separators=(",", ":")))
        ack = _recv_handshake(ws, timeout=HANDSHAKE_TIMEOUT_S)
        if ack is None:
            raise OsRpcError("NAVIG OS rejected the handshake", code="HANDSHAKE")

        ws.send(json.dumps({
            "id": req_id,
            "type": "request",
            "channel": channel,
            "args": list(args),
        }, separators=(",", ":")))
        resp = _recv_until(ws, req_id, timeout=timeout, want_types=("response",))
        if "error" in resp and resp["error"] is not None:
            _raise_wire_error(resp["error"])
        return resp.get("result")


def _recv_handshake(ws, *, timeout: float) -> dict[str, Any] | None:
    """Wait for ``handshake_ack``; an ``error`` frame raises via :class:`OsRpcError`."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OsRpcError(
                "NAVIG OS did not answer the handshake in time", code="HANDSHAKE_TIMEOUT"
            )
        try:
            raw = ws.recv(timeout=remaining)
        except TimeoutError as exc:
            raise OsRpcError(
                "NAVIG OS did not answer the handshake in time", code="HANDSHAKE_TIMEOUT"
            ) from exc
        except Exception as exc:
            raise OsRpcError(
                "the NAVIG OS server closed the connection during handshake — it may be restarting",
                code="DISCONNECTED",
            ) from exc
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8", errors="replace")
        try:
            envelope = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(envelope, dict):
            continue
        etype = envelope.get("type")
        if etype == "handshake_ack":
            return envelope
        if etype == "error":
            err = envelope.get("error") or {}
            _raise_wire_error(err)
            return None


def try_call(channel: str, *args: Any, timeout: float = DEFAULT_CALL_TIMEOUT_S) -> Any:
    """:func:`call`, but returns ``None`` when the OS server simply isn't running.

    Use for read-only affordances (a CLI that degrades to "no sessions" instead of
    failing); never for mutations that must not silently no-op.
    """
    try:
        return call(channel, *args, timeout=timeout)
    except OsRpcError as exc:
        if exc.code in ("NO_SERVER", "UNREACHABLE"):
            return None
        raise
