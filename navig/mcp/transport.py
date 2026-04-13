"""MCP transport implementations: stdio, SSE, and WebSocket."""

from __future__ import annotations

import asyncio
import json
import os
from abc import ABC, abstractmethod
from typing import Any

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()

# Default timeout (seconds) for a single request round-trip.
_REQUEST_TIMEOUT = 30.0


class MCPTransport(ABC):
    """Abstract base for MCP transports."""

    @abstractmethod
    async def connect(self) -> None:
        """Establish the connection."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close the connection and release all resources."""

    @abstractmethod
    async def send(self, data: str) -> str | None:
        """Send a request and return the response body, or ``None`` for notifications."""

    @abstractmethod
    async def send_notification(self, data: str) -> None:
        """Send a fire-and-forget notification (no response expected)."""

    @abstractmethod
    def is_connected(self) -> bool:
        """Return ``True`` if the transport is currently connected."""


class StdioTransport(MCPTransport):
    """Stdio transport — spawns a subprocess and communicates over stdin/stdout.

    The wire format is newline-delimited JSON (ndjson).
    """

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        self.command = command
        self.args: list[str] = args or []
        self.env = env
        self.cwd = cwd

        self._process: asyncio.subprocess.Process | None = None
        self._pending: dict[Any, asyncio.Future[str]] = {}
        self._reader_task: asyncio.Task[None] | None = None
        self._stderr_task: asyncio.Task[None] | None = None
        self._write_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Start the subprocess and background reader tasks."""
        full_env = os.environ.copy()
        if self.env:
            for key, value in self.env.items():
                # Expand simple ${VAR} references from the host environment.
                if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                    value = os.environ.get(value[2:-1], "")
                full_env[key] = value

        logger.debug("Starting MCP server: %s %s", self.command, " ".join(self.args))

        try:
            self._process = await asyncio.create_subprocess_exec(
                self.command,
                *self.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=full_env,
                cwd=self.cwd,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                f"MCP server command not found: {self.command!r}"
            ) from exc
        except Exception as exc:
            raise RuntimeError(f"Failed to start MCP server: {exc}") from exc

        self._reader_task = asyncio.create_task(self._read_loop(), name="mcp-stdio-reader")
        self._stderr_task = asyncio.create_task(self._read_stderr(), name="mcp-stdio-stderr")
        logger.info("MCP stdio transport connected: %s", self.command)

    async def disconnect(self) -> None:
        """Terminate the subprocess and cancel all background tasks."""
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self._reader_task = None
        self._stderr_task = None

        if self._process is not None:
            try:
                if self._process.returncode is None:
                    self._process.terminate()
                    try:
                        await asyncio.wait_for(self._process.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        self._process.kill()
                        await self._process.wait()
            except ProcessLookupError:
                pass  # Process already gone
            self._process = None

        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()

        logger.info("MCP stdio transport disconnected")

    async def send(self, data: str) -> str | None:
        """Write a JSON-RPC request and await its response."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("StdioTransport not connected")

        parsed = json.loads(data)
        request_id = parsed.get("id")

        future: asyncio.Future[str] | None = None
        if request_id is not None:
            future = asyncio.get_running_loop().create_future()
            self._pending[request_id] = future

        async with self._write_lock:
            self._process.stdin.write((data + "\n").encode())
            await self._process.stdin.drain()

        if future is None:
            return None

        try:
            return await asyncio.wait_for(future, timeout=_REQUEST_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(f"Request timeout: id={request_id}") from exc
        finally:
            self._pending.pop(request_id, None)

    async def send_notification(self, data: str) -> None:
        """Write a JSON-RPC notification (no response awaited)."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("StdioTransport not connected")

        async with self._write_lock:
            self._process.stdin.write((data + "\n").encode())
            await self._process.stdin.drain()

    def is_connected(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def _read_loop(self) -> None:
        """Continuously read stdout and resolve pending request futures."""
        while self._process and self._process.stdout:
            try:
                line = await self._process.stdout.readline()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("MCP stdio reader error: %s", exc)
                break

            if not line:
                logger.warning("MCP server stdout closed")
                break

            text = line.decode(errors="replace").strip()
            if not text:
                continue

            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON from MCP server: %.100s", text)
                continue

            req_id = payload.get("id")
            if req_id is not None and req_id in self._pending:
                fut = self._pending[req_id]
                if not fut.done():
                    fut.set_result(text)
            else:
                logger.debug("MCP server notification: %.100s", text)

    async def _read_stderr(self) -> None:
        """Read stderr and emit debug log lines."""
        while self._process and self._process.stderr:
            try:
                line = await self._process.stderr.readline()
            except asyncio.CancelledError:
                break
            except Exception:
                break

            if not line:
                break

            text = line.decode(errors="replace").strip()
            if text:
                logger.debug("MCP stderr: %s", text)


class SSETransport(MCPTransport):
    """SSE (Server-Sent Events) transport for HTTP-based MCP servers.

    Sends requests via HTTP POST and receives responses via a persistent
    SSE event stream (``text/event-stream``).  The SSE listener runs in a
    background task and resolves pending request futures.
    """

    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self.url = url
        self.headers: dict[str, str] = headers or {}
        # post_url may be overridden by an ``endpoint:`` SSE event from the server.
        self.post_url = url

        self._session: Any = None  # aiohttp.ClientSession
        self._sse_task: asyncio.Task[None] | None = None
        self._pending: dict[Any, asyncio.Future[str]] = {}

    async def connect(self) -> None:
        """Open an aiohttp session and start the SSE listener background task."""
        try:
            import aiohttp
        except ImportError as exc:
            raise ImportError(
                "aiohttp is required for SSE transport: pip install aiohttp"
            ) from exc

        self._session = aiohttp.ClientSession(headers=self.headers)
        self._sse_task = asyncio.create_task(
            self._sse_listen_loop(), name="mcp-sse-listener"
        )
        logger.info("MCP SSE transport connected: %s", self.url)

    async def _sse_listen_loop(self) -> None:
        """Read the SSE event stream and resolve pending request futures."""
        from urllib.parse import urljoin

        try:
            async with self._session.get(
                self.url, headers={"Accept": "text/event-stream"}
            ) as response:
                if response.status != 200:
                    logger.warning(
                        "SSE listener received HTTP %d — cannot receive responses",
                        response.status,
                    )
                    return

                async for raw_line in response.content:
                    line = raw_line.decode("utf-8", errors="replace").strip()

                    if line.startswith("endpoint: "):
                        # Server advertises a dedicated POST endpoint.
                        self.post_url = urljoin(self.url, line[10:].strip())
                        continue

                    if not line.startswith("data: "):
                        continue

                    data_str = line[6:].strip()
                    if not data_str:
                        continue

                    try:
                        payload = json.loads(data_str)
                    except json.JSONDecodeError as exc:
                        logger.debug("SSE payload JSON error: %s", exc)
                        continue

                    req_id = payload.get("id")
                    if req_id is not None and req_id in self._pending:
                        future = self._pending.pop(req_id)
                        if not future.done():
                            future.set_result(data_str)

        except asyncio.CancelledError:
            pass
        except Exception as exc:
            if self._session and not self._session.closed:
                logger.warning("SSE listen loop terminated: %s", exc)

    async def disconnect(self) -> None:
        """Cancel the SSE listener and close the HTTP session."""
        if self._sse_task is not None:
            self._sse_task.cancel()
            try:
                await self._sse_task
            except asyncio.CancelledError:
                pass
            self._sse_task = None

        if self._session is not None:
            await self._session.close()
            self._session = None

        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()

        logger.info("MCP SSE transport disconnected")

    async def send(self, data: str) -> str | None:
        """POST a request; return inline body when available or await SSE response."""
        if self._session is None:
            raise RuntimeError("SSETransport not connected")

        req_id: Any = None
        try:
            req_id = json.loads(data).get("id")
        except json.JSONDecodeError:
            pass

        future: asyncio.Future[str] | None = None
        if req_id is not None:
            future = asyncio.get_running_loop().create_future()
            self._pending[req_id] = future

        async with self._session.post(
            self.post_url,
            data=data,
            headers={"Content-Type": "application/json"},
        ) as response:
            if response.status not in (200, 202):
                body = await response.text()
                self._pending.pop(req_id, None)
                if future is not None and not future.done():
                    future.cancel()
                raise RuntimeError(f"MCP POST failed: HTTP {response.status} — {body}")

            body = await response.text()
            if body and body.strip():
                # Server returned an inline response — no need to wait for SSE.
                self._pending.pop(req_id, None)
                if future is not None and not future.done():
                    future.cancel()
                return body

        if future is None:
            return None

        try:
            return await asyncio.wait_for(future, timeout=_REQUEST_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(
                f"MCP SSE request timed out (req_id={req_id})"
            ) from exc
        finally:
            self._pending.pop(req_id, None)

    async def send_notification(self, data: str) -> None:
        """Send a notification via POST (no response expected)."""
        await self.send(data)

    def is_connected(self) -> bool:
        return self._session is not None and not self._session.closed


class WebSocketTransport(MCPTransport):
    """WebSocket transport — single persistent connection, response routed by JSON-RPC id."""

    def __init__(self, url: str, headers: dict[str, str] | None = None) -> None:
        self.url = url
        self.headers: dict[str, str] = headers or {}
        self._ws: Any = None
        self._reader_task: asyncio.Task[None] | None = None
        self._pending: dict[Any, asyncio.Future[str]] = {}
        self._write_lock = asyncio.Lock()

    async def connect(self) -> None:
        try:
            import websockets
        except ImportError as exc:
            raise ImportError(
                "websockets is required for WebSocket transport: pip install websockets"
            ) from exc

        self._ws = await websockets.connect(
            self.url, additional_headers=self.headers
        )
        self._reader_task = asyncio.create_task(
            self._read_loop(), name="mcp-ws-reader"
        )
        logger.info("MCP WebSocket transport connected: %s", self.url)

    async def disconnect(self) -> None:
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
            self._reader_task = None

        if self._ws is not None:
            await self._ws.close()
            self._ws = None

        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()

    async def send(self, data: str) -> str | None:
        if self._ws is None:
            raise RuntimeError("WebSocketTransport not connected")

        req_id: Any = None
        try:
            req_id = json.loads(data).get("id")
        except json.JSONDecodeError:
            pass

        future: asyncio.Future[str] | None = None
        if req_id is not None:
            future = asyncio.get_running_loop().create_future()
            self._pending[req_id] = future

        async with self._write_lock:
            await self._ws.send(data)

        if future is None:
            return None

        try:
            return await asyncio.wait_for(future, timeout=_REQUEST_TIMEOUT)
        except asyncio.TimeoutError as exc:
            raise RuntimeError(f"WebSocket request timeout: id={req_id}") from exc
        finally:
            self._pending.pop(req_id, None)

    async def send_notification(self, data: str) -> None:
        if self._ws is None:
            raise RuntimeError("WebSocketTransport not connected")
        async with self._write_lock:
            await self._ws.send(data)

    def is_connected(self) -> bool:
        ws = self._ws
        return ws is not None and not getattr(ws, "closed", True)

    async def _read_loop(self) -> None:
        while self._ws is not None:
            try:
                message = await self._ws.recv()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("MCP WebSocket read loop ended: %s", exc)
                break

            if not message:
                continue

            try:
                payload = json.loads(message)
            except json.JSONDecodeError:
                logger.warning(
                    "Invalid JSON from MCP WebSocket: %.100s", str(message)
                )
                continue

            req_id = payload.get("id")
            if req_id is not None and req_id in self._pending:
                fut = self._pending[req_id]
                if not fut.done():
                    fut.set_result(message)
            else:
                logger.debug("MCP WebSocket notification: %.100s", str(message))
