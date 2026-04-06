"""MCP transport implementations (stdio and SSE)."""

import asyncio
import json
import os
from abc import ABC, abstractmethod
from typing import Any

from navig.debug_logger import get_debug_logger

logger = get_debug_logger()


class MCPTransport(ABC):
    """Abstract base for MCP transports."""

    @abstractmethod
    async def connect(self):
        """Establish connection."""
        pass

    @abstractmethod
    async def disconnect(self):
        """Close connection."""
        pass

    @abstractmethod
    async def send(self, data: str) -> str | None:
        """Send request and wait for response (if request has ID)."""
        pass

    @abstractmethod
    async def send_notification(self, data: str):
        """Send notification (no response expected)."""
        pass

    @abstractmethod
    def is_connected(self) -> bool:
        """Check if transport is connected."""
        pass


class StdioTransport(MCPTransport):
    """
    Stdio transport for MCP servers.

    Spawns subprocess and communicates via stdin/stdout.
    Uses JSON-RPC over newline-delimited JSON.
    """

    def __init__(
        self,
        command: str,
        args: list = None,
        env: dict = None,
        cwd: str = None,
    ):
        self.command = command
        self.args = args or []
        self.env = env
        self.cwd = cwd

        self._process: asyncio.subprocess.Process | None = None
        self._pending: dict[Any, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def connect(self):
        """Start subprocess."""
        # Merge environment
        full_env = os.environ.copy()
        if self.env:
            # Resolve environment variable references
            for key, value in self.env.items():
                if isinstance(value, str) and value.startswith("${") and value.endswith("}"):
                    env_var = value[2:-1]
                    value = os.environ.get(env_var, "")
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

            # Start reader task
            self._reader_task = asyncio.create_task(self._read_loop())

            # Start stderr reader for debugging
            self._stderr_task = asyncio.create_task(self._read_stderr())

            logger.info("MCP stdio transport connected: %s", self.command)

        except FileNotFoundError as _exc:
            raise RuntimeError(f"MCP server command not found: {self.command}") from _exc
        except Exception as e:
            raise RuntimeError(f"Failed to start MCP server: {e}") from e

    async def disconnect(self):
        """Stop subprocess."""
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown

        if self._stderr_task:
            self._stderr_task.cancel()
            try:
                await self._stderr_task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown

        if self._process:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except asyncio.TimeoutError:
                self._process.kill()
                await self._process.wait()

        # Cancel pending futures
        for future in self._pending.values():
            if not future.done():
                future.cancel()

        self._pending.clear()
        self._process = None
        logger.info("MCP stdio transport disconnected")

    async def send(self, data: str) -> str | None:
        """Send request and wait for response."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("Transport not connected")

        # Parse to get request ID
        parsed = json.loads(data)
        request_id = parsed.get("id")

        async with self._lock:
            # Create future for response if this is a request (has ID)
            if request_id is not None:
                loop = asyncio.get_running_loop()
                future = loop.create_future()
                self._pending[request_id] = future

            # Send request
            message = data + "\n"
            self._process.stdin.write(message.encode())
            await self._process.stdin.drain()

        # Wait for response if request
        if request_id is not None:
            try:
                response = await asyncio.wait_for(future, timeout=30)
                return response
            except asyncio.TimeoutError as _exc:
                self._pending.pop(request_id, None)
                raise RuntimeError(f"Request timeout: {request_id}") from _exc
            finally:
                self._pending.pop(request_id, None)

        return None

    async def send_notification(self, data: str):
        """Send notification (no response expected)."""
        if not self._process or not self._process.stdin:
            raise RuntimeError("Transport not connected")

        async with self._lock:
            message = data + "\n"
            self._process.stdin.write(message.encode())
            await self._process.stdin.drain()

    def is_connected(self) -> bool:
        """Check if process is running."""
        return self._process is not None and self._process.returncode is None

    async def _read_loop(self):
        """Read responses from stdout."""
        while self._process and self._process.stdout:
            try:
                line = await self._process.stdout.readline()
                if not line:
                    logger.warning("MCP server stdout closed")
                    break

                data = line.decode().strip()
                if not data:
                    continue

                try:
                    parsed = json.loads(data)
                    request_id = parsed.get("id")

                    if request_id is not None and request_id in self._pending:
                        future = self._pending[request_id]
                        if not future.done():
                            future.set_result(data)
                    else:
                        # Notification from server
                        logger.debug("MCP notification: %s", data[:100])

                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from MCP server: %s", data[:100])

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("MCP reader error: %s", e)
                break

    async def _read_stderr(self):
        """Read and log stderr for debugging."""
        while self._process and self._process.stderr:
            try:
                line = await self._process.stderr.readline()
                if not line:
                    break

                text = line.decode().strip()
                if text:
                    logger.debug("MCP stderr: %s", text)

            except asyncio.CancelledError:
                break
            except Exception:
                break


class SSETransport(MCPTransport):
    """
    SSE (Server-Sent Events) transport for MCP servers.

    Connects to HTTP endpoint for sending and receives via SSE.

    .. warning::
        The SSE *receive* path is not yet implemented.  The transport can
        *send* requests via HTTP POST but cannot read server-pushed SSE
        events.  ``_sse_task`` was declared but never started, meaning any
        MCP server that responds via SSE (per the HTTP+SSE spec) will be
        silently ignored and every `send()` call will hang for 30 s before
        timing out.

        To use this transport, implement ``_sse_listen_loop()`` that reads
        ``text/event-stream`` lines and resolves pending futures.
        Track issue: NAVIG-BUG-004.
    """

    def __init__(
        self,
        url: str,
        headers: dict = None,
    ):
        self.url = url
        self.headers = headers or {}
        self.post_url = url

        self._session = None
        self._sse_task: asyncio.Task | None = None
        self._pending: dict[Any, asyncio.Future] = {}

    async def connect(self):
        """Create HTTP session and start SSE listener."""
        try:
            import aiohttp
        except ImportError as _exc:
            raise ImportError("aiohttp required for SSE transport: pip install aiohttp") from _exc

        self._session = aiohttp.ClientSession(headers=self.headers)
        logger.info("MCP SSE transport connected: %s", self.url)

        self._sse_task = asyncio.create_task(self._sse_listen_loop())

    async def _sse_listen_loop(self):
        """Listen for SSE events and resolve pending requests."""
        import json
        from urllib.parse import urljoin

        try:
            async with self._session.get(
                self.url, headers={"Accept": "text/event-stream"}
            ) as response:
                if response.status != 200:
                    logger.warning("SSE listener failed to connect: %s", response.status)
                    return

                async for line in response.content:
                    line_text = line.decode("utf-8", errors="replace").strip()
                    if line_text.startswith("endpoint: "):
                        endpoint_path = line_text[10:].strip()
                        self.post_url = urljoin(self.url, endpoint_path)
                    elif line_text.startswith("data: "):
                        data_str = line_text[6:].strip()
                        if data_str:
                            try:
                                payload = json.loads(data_str)
                                req_id = payload.get("id")
                                if req_id is not None and req_id in self._pending:
                                    future = self._pending.pop(req_id)
                                    if not future.done():
                                        future.set_result(data_str)
                            except Exception as e:
                                logger.debug("SSE payload parse error: %s", e)
        except asyncio.CancelledError:
            pass
        except Exception as e:
            if self._session and not self._session.closed:
                logger.warning("SSE listen loop error: %s", e)

    async def disconnect(self):
        """Close HTTP session."""
        if self._sse_task:
            self._sse_task.cancel()
            try:
                await self._sse_task
            except asyncio.CancelledError:
                pass  # task cancelled; expected during shutdown

        if self._session:
            await self._session.close()
            self._session = None

        for future in self._pending.values():
            if not future.done():
                future.cancel()

        self._pending.clear()
        logger.info("MCP SSE transport disconnected")

    async def send(self, data: str) -> str | None:
        """
        Send request via HTTP POST. Return synchronous response if available,
        otherwise await the delayed response via the SSE listener.
        """
        if not self._session:
            raise RuntimeError("Transport not connected")

        import json

        req_id = None
        try:
            payload = json.loads(data)
            req_id = payload.get("id")
        except Exception:
            pass

        future = None
        if req_id is not None:
            future = asyncio.get_running_loop().create_future()
            self._pending[req_id] = future

        async with self._session.post(
            self.post_url, data=data, headers={"Content-Type": "application/json"}
        ) as response:
            if response.status not in (200, 202):
                error = await response.text()
                if req_id is not None:
                    self._pending.pop(req_id, None)
                raise RuntimeError(f"MCP request failed: {response.status} {error}")

            resp_text = await response.text()
            if resp_text and resp_text.strip():
                if req_id is not None:
                    self._pending.pop(req_id, None)
                return resp_text

        if future is not None:
            try:
                return await asyncio.wait_for(future, timeout=30)
            except asyncio.TimeoutError:
                raise RuntimeError(  # noqa: B904
                    f"MCP SSE request timed out (no response for req_id={req_id})"
                ) from None
            finally:
                self._pending.pop(req_id, None)

        return None

    async def send_notification(self, data: str):
        """Send notification via HTTP POST."""
        await self.send(data)

    def is_connected(self) -> bool:
        """Check if session is active."""
        return self._session is not None and not self._session.closed
