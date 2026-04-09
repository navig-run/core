"""
Mock LLM server for integration tests.

Uses stdlib ``http.server.ThreadingHTTPServer`` (no aiohttp — avoids the
Windows/Python 3.14 import-time hang) to serve OpenAI-compatible and
Anthropic-compatible endpoints.

Usage in tests::

    from tests.fixtures.mock_llm_server import MockLLMServer

    @pytest.fixture()
    def llm_server():
        with MockLLMServer(canned_content="Hello world") as srv:
            yield srv
        # server is stopped automatically

    def test_something(llm_server):
        # Use llm_server.base_url as the API endpoint
        ...
"""

from __future__ import annotations

import json
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any


class _MockHandler(BaseHTTPRequestHandler):
    """Handler that simulates OpenAI and Anthropic chat completion endpoints."""

    # Suppress stderr logging from the HTTP server
    def log_message(self, format, *args):  # noqa: A002
        pass

    def do_POST(self):  # noqa: N802
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length)

        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            data = {}

        stream = data.get("stream", False)
        model = data.get("model", "mock-model")

        # Record request on server instance
        self.server.last_request = data  # type: ignore[attr-defined]
        self.server.request_count += 1  # type: ignore[attr-defined]

        canned: str = self.server.canned_content  # type: ignore[attr-defined]
        status_override: int | None = self.server.status_override  # type: ignore[attr-defined]

        if status_override is not None:
            self._send_error(status_override)
            return

        if self.path.rstrip("/") in ("/chat/completions", "/v1/chat/completions"):
            if stream:
                self._handle_openai_stream(canned, model)
            else:
                self._handle_openai(canned, model)
        elif self.path.rstrip("/") == "/v1/messages":
            if stream:
                self._handle_anthropic_stream(canned, model)
            else:
                self._handle_anthropic(canned, model)
        else:
            self._send_error(404)

    # ── OpenAI ──────────────────────────────────────────────

    def _handle_openai(self, content: str, model: str) -> None:
        response = {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": len(content.split()),
                "total_tokens": 10 + len(content.split()),
            },
        }
        self._send_json(response)

    def _handle_openai_stream(self, content: str, model: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        # Send content in word-sized chunks
        words = content.split(" ")
        for i, word in enumerate(words):
            token = word if i == 0 else f" {word}"
            chunk = {
                "id": "chatcmpl-mock",
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "delta": {"content": token},
                        "finish_reason": None,
                    }
                ],
            }
            self.wfile.write(f"data: {json.dumps(chunk)}\n\n".encode())
            self.wfile.flush()

        # Final chunk with finish_reason
        final = {
            "id": "chatcmpl-mock",
            "object": "chat.completion.chunk",
            "model": model,
            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": len(words),
                "total_tokens": 10 + len(words),
            },
        }
        self.wfile.write(f"data: {json.dumps(final)}\n\n".encode())
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()

    # ── Anthropic ────────────────────────────────────────────

    def _handle_anthropic(self, content: str, model: str) -> None:
        response = {
            "id": "msg_mock",
            "type": "message",
            "role": "assistant",
            "model": model,
            "content": [{"type": "text", "text": content}],
            "stop_reason": "end_turn",
            "usage": {
                "input_tokens": 10,
                "output_tokens": len(content.split()),
            },
        }
        self._send_json(response)

    def _handle_anthropic_stream(self, content: str, model: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        # message_start
        msg_start = {
            "type": "message_start",
            "message": {
                "id": "msg_mock",
                "type": "message",
                "role": "assistant",
                "model": model,
                "usage": {"input_tokens": 10, "output_tokens": 0},
            },
        }
        self._write_sse(msg_start)

        # content_block_start
        self._write_sse(
            {
                "type": "content_block_start",
                "index": 0,
                "content_block": {"type": "text", "text": ""},
            }
        )

        # content_block_delta — word-by-word
        words = content.split(" ")
        for i, word in enumerate(words):
            token = word if i == 0 else f" {word}"
            delta_event = {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "text_delta", "text": token},
            }
            self._write_sse(delta_event)

        # content_block_stop
        self._write_sse({"type": "content_block_stop", "index": 0})

        # message_delta
        msg_delta = {
            "type": "message_delta",
            "delta": {"stop_reason": "end_turn"},
            "usage": {"output_tokens": len(words)},
        }
        self._write_sse(msg_delta)

        # message_stop
        self._write_sse({"type": "message_stop"})

    # ── Helpers ──────────────────────────────────────────────

    def _write_sse(self, data: dict) -> None:
        self.wfile.write(f"data: {json.dumps(data)}\n\n".encode())
        self.wfile.flush()

    def _send_json(self, obj: Any, status: int = 200) -> None:
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: int) -> None:
        body = json.dumps(
            {"error": {"message": f"Mock error {status}", "type": "mock_error"}}
        ).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class MockLLMServer:
    """Context-manager wrapper around a thread-hosted HTTP mock.

    Attributes:
        base_url:      ``http://127.0.0.1:<port>`` — use as provider base URL.
        last_request:  The parsed JSON body of the most recent request.
        request_count: Number of requests handled.
    """

    def __init__(
        self,
        canned_content: str = "Mock response",
        host: str = "127.0.0.1",
        port: int = 0,
        status_override: int | None = None,
    ) -> None:
        self.canned_content = canned_content
        self.host = host
        self.port = port
        self.status_override = status_override

        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ── Context manager ─────────────────────────────────────

    def __enter__(self) -> MockLLMServer:
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.stop()

    # ── Lifecycle ────────────────────────────────────────────

    def start(self) -> None:
        self._server = HTTPServer((self.host, self.port), _MockHandler)

        # Attach canned data to the server so the handler can access it
        self._server.canned_content = self.canned_content  # type: ignore[attr-defined]
        self._server.status_override = self.status_override  # type: ignore[attr-defined]
        self._server.last_request = None  # type: ignore[attr-defined]
        self._server.request_count = 0  # type: ignore[attr-defined]

        # Pick the assigned port (in case port=0)
        self.port = self._server.server_address[1]

        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

        # Wait until the server is ready
        time.sleep(0.05)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None

    # ── Properties ──────────────────────────────────────────

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def last_request(self) -> dict | None:
        if self._server is not None:
            return self._server.last_request  # type: ignore[attr-defined]
        return None

    @property
    def request_count(self) -> int:
        if self._server is not None:
            return self._server.request_count  # type: ignore[attr-defined]
        return 0

    def set_canned_content(self, content: str) -> None:
        """Update the canned response content at runtime."""
        self.canned_content = content
        if self._server is not None:
            self._server.canned_content = content  # type: ignore[attr-defined]

    def set_status_override(self, status: int | None) -> None:
        """Force the server to return a specific HTTP status."""
        self.status_override = status
        if self._server is not None:
            self._server.status_override = status  # type: ignore[attr-defined]
