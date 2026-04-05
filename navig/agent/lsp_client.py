"""Low-level JSON-RPC client for the Language Server Protocol.

Manages a single LSP server subprocess — reading/writing Content-Length
framed JSON-RPC messages over stdin/stdout.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ── Data types ────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LspPosition:
    """Zero-indexed line/character position."""

    line: int
    character: int


@dataclass(frozen=True, slots=True)
class LspRange:
    start: LspPosition
    end: LspPosition


@dataclass(frozen=True, slots=True)
class LspDiagnostic:
    range: LspRange
    severity: int  # 1=Error, 2=Warning, 3=Info, 4=Hint
    message: str
    source: str = ""
    code: str = ""

    @property
    def severity_label(self) -> str:
        return {1: "Error", 2: "Warning", 3: "Info", 4: "Hint"}.get(self.severity, "Unknown")


@dataclass(frozen=True, slots=True)
class LspLocation:
    uri: str
    range: LspRange


@dataclass(frozen=True, slots=True)
class LspSymbol:
    name: str
    kind: int
    range: LspRange
    detail: str = ""


# ── Constants ─────────────────────────────────────────────────

LSP_REQUEST_TIMEOUT: float = 10.0
LSP_DIAGNOSTIC_WAIT: float = 0.8  # seconds to wait after didChange for diagnostics
LSP_SHUTDOWN_TIMEOUT: float = 5.0

LSP_SEVERITY_ERROR = 1
LSP_SEVERITY_WARNING = 2


# ── Client ────────────────────────────────────────────────────


@dataclass
class LspClient:
    """Async JSON-RPC client wrapping a single LSP server subprocess."""

    server_cmd: list[str]
    root_uri: str

    _process: asyncio.subprocess.Process | None = field(default=None, init=False, repr=False)
    _request_id: int = field(default=0, init=False, repr=False)
    _pending: dict[int, asyncio.Future[Any]] = field(default_factory=dict, init=False, repr=False)
    _diagnostics: dict[str, list[LspDiagnostic]] = field(
        default_factory=dict, init=False, repr=False
    )
    _notification_handlers: dict[str, Callable[..., Any]] = field(
        default_factory=dict, init=False, repr=False
    )
    _reader_task: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _server_capabilities: dict[str, Any] = field(default_factory=dict, init=False, repr=False)
    _open_documents: set[str] = field(default_factory=set, init=False, repr=False)
    _doc_version: dict[str, int] = field(default_factory=dict, init=False, repr=False)
    _alive: bool = field(default=False, init=False, repr=False)

    # ── lifecycle ─────────────────────────────────────────────

    async def start(self) -> None:
        """Launch the LSP server subprocess and send initialize/initialized."""
        self._process = await asyncio.create_subprocess_exec(
            *self.server_cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._alive = True
        self._reader_task = asyncio.create_task(self._read_loop(), name="lsp-read")
        self._server_capabilities = await self._initialize()
        logger.debug(
            "LSP server started: cmd=%s pid=%s",
            self.server_cmd,
            self._process.pid,
        )

    async def _initialize(self) -> dict[str, Any]:
        """Send ``initialize`` + ``initialized`` handshake."""
        result = await self._request(
            "initialize",
            {
                "processId": None,
                "rootUri": self.root_uri,
                "capabilities": {
                    "textDocument": {
                        "synchronization": {
                            "dynamicRegistration": False,
                            "willSave": False,
                            "didSave": True,
                            "willSaveWaitUntil": False,
                        },
                        "publishDiagnostics": {
                            "relatedInformation": True,
                        },
                        "completion": {
                            "completionItem": {"snippetSupport": False},
                        },
                        "hover": {},
                        "definition": {},
                        "references": {},
                        "documentSymbol": {},
                    },
                },
                "workspaceFolders": [{"uri": self.root_uri, "name": Path(self.root_uri).name}],
            },
        )
        self._notify("initialized", {})
        caps = result.get("capabilities", {}) if isinstance(result, dict) else {}
        return caps

    async def shutdown(self) -> None:
        """Graceful shutdown → exit → terminate."""
        self._alive = False
        if not self._process or self._process.returncode is not None:
            return
        try:
            await asyncio.wait_for(self._request("shutdown", {}), timeout=LSP_SHUTDOWN_TIMEOUT)
            self._notify("exit", {})
        except (TimeoutError, BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            try:
                self._process.terminate()
            except ProcessLookupError:
                pass
            if self._reader_task and not self._reader_task.done():
                self._reader_task.cancel()

    @property
    def alive(self) -> bool:
        return self._alive and self._process is not None and self._process.returncode is None

    # ── JSON-RPC transport ────────────────────────────────────

    async def _request(self, method: str, params: dict[str, Any]) -> Any:
        self._request_id += 1
        rid = self._request_id
        msg: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": rid,
            "method": method,
            "params": params,
        }
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[rid] = future
        self._send(msg)
        try:
            return await asyncio.wait_for(future, timeout=LSP_REQUEST_TIMEOUT)
        except TimeoutError:
            self._pending.pop(rid, None)
            raise

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _send(self, msg: dict[str, Any]) -> None:
        assert self._process and self._process.stdin  # noqa: S101
        body = json.dumps(msg, separators=(",", ":"))
        header = f"Content-Length: {len(body)}\r\n\r\n"
        self._process.stdin.write(header.encode() + body.encode())

    async def _read_loop(self) -> None:  # noqa: C901
        """Read Content-Length framed messages from stdout and dispatch."""
        assert self._process and self._process.stdout  # noqa: S101
        reader = self._process.stdout
        try:
            while self._alive:
                # Read headers until empty line
                content_length: int | None = None
                while True:
                    header_line = await reader.readline()
                    if not header_line:
                        return  # EOF
                    decoded = header_line.decode("utf-8", errors="replace").strip()
                    if not decoded:
                        break  # end of headers
                    if decoded.lower().startswith("content-length:"):
                        content_length = int(decoded.split(":", 1)[1].strip())

                if content_length is None:
                    continue

                body = await reader.readexactly(content_length)
                try:
                    msg = json.loads(body)
                except json.JSONDecodeError:
                    logger.debug("LSP: malformed JSON body: %s", body[:200])
                    continue

                self._dispatch(msg)
        except (asyncio.CancelledError, asyncio.IncompleteReadError):
            pass
        except Exception:
            logger.debug("LSP read loop error", exc_info=True)

    def _dispatch(self, msg: dict[str, Any]) -> None:
        """Route an incoming message to the right handler."""
        # Response to a pending request
        rid = msg.get("id")
        if rid is not None and rid in self._pending:
            future = self._pending.pop(rid)
            if "error" in msg:
                future.set_exception(
                    RuntimeError(
                        f"LSP error {msg['error'].get('code')}: {msg['error'].get('message')}"
                    )
                )
            else:
                future.set_result(msg.get("result"))
            return

        # Server-initiated notification
        method = msg.get("method", "")
        if method == "textDocument/publishDiagnostics":
            self._handle_diagnostics(msg.get("params", {}))
        elif method in self._notification_handlers:
            self._notification_handlers[method](msg.get("params", {}))

    # ── diagnostics ───────────────────────────────────────────

    def _handle_diagnostics(self, params: dict[str, Any]) -> None:
        uri = params.get("uri", "")
        self._diagnostics[uri] = [
            LspDiagnostic(
                range=LspRange(
                    start=LspPosition(**d["range"]["start"]),
                    end=LspPosition(**d["range"]["end"]),
                ),
                severity=d.get("severity", 1),
                message=d.get("message", ""),
                source=d.get("source", ""),
                code=str(d.get("code", "")),
            )
            for d in params.get("diagnostics", [])
        ]

    # ── document sync ─────────────────────────────────────────

    def did_open(self, uri: str, language_id: str, text: str) -> None:
        """Notify the server that a document was opened."""
        self._doc_version[uri] = 1
        self._open_documents.add(uri)
        self._notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": uri,
                    "languageId": language_id,
                    "version": 1,
                    "text": text,
                }
            },
        )

    def did_change(self, uri: str, text: str) -> None:
        """Notify server of file change (full document sync)."""
        ver = self._doc_version.get(uri, 0) + 1
        self._doc_version[uri] = ver
        self._notify(
            "textDocument/didChange",
            {
                "textDocument": {"uri": uri, "version": ver},
                "contentChanges": [{"text": text}],
            },
        )

    def did_close(self, uri: str) -> None:
        """Notify server that a document was closed."""
        self._open_documents.discard(uri)
        self._doc_version.pop(uri, None)
        self._notify(
            "textDocument/didClose",
            {"textDocument": {"uri": uri}},
        )

    # ── queries ───────────────────────────────────────────────

    def get_diagnostics(self, uri: str) -> list[LspDiagnostic]:
        return list(self._diagnostics.get(uri, []))

    def get_errors(self, uri: str) -> list[LspDiagnostic]:
        return [d for d in self.get_diagnostics(uri) if d.severity == LSP_SEVERITY_ERROR]

    def get_warnings(self, uri: str) -> list[LspDiagnostic]:
        return [d for d in self.get_diagnostics(uri) if d.severity == LSP_SEVERITY_WARNING]

    async def goto_definition(self, uri: str, line: int, character: int) -> list[LspLocation]:
        """Request textDocument/definition at (line, character)."""
        result = await self._request(
            "textDocument/definition",
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
            },
        )
        return _parse_locations(result)

    async def find_references(
        self, uri: str, line: int, character: int, *, include_declaration: bool = True
    ) -> list[LspLocation]:
        """Request textDocument/references at (line, character)."""
        result = await self._request(
            "textDocument/references",
            {
                "textDocument": {"uri": uri},
                "position": {"line": line, "character": character},
                "context": {"includeDeclaration": include_declaration},
            },
        )
        return _parse_locations(result)

    async def document_symbols(self, uri: str) -> list[LspSymbol]:
        """Request textDocument/documentSymbol."""
        result = await self._request(
            "textDocument/documentSymbol",
            {"textDocument": {"uri": uri}},
        )
        if not isinstance(result, list):
            return []
        return _parse_symbols(result)


# ── helpers ───────────────────────────────────────────────────


def _parse_locations(raw: Any) -> list[LspLocation]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    locations: list[LspLocation] = []
    for item in raw:
        try:
            locations.append(
                LspLocation(
                    uri=item["uri"],
                    range=LspRange(
                        start=LspPosition(**item["range"]["start"]),
                        end=LspPosition(**item["range"]["end"]),
                    ),
                )
            )
        except (KeyError, TypeError):
            continue
    return locations


def _parse_symbols(raw: list[Any]) -> list[LspSymbol]:
    symbols: list[LspSymbol] = []
    for item in raw:
        try:
            r = item.get("range") or item.get("location", {}).get("range", {})
            symbols.append(
                LspSymbol(
                    name=item["name"],
                    kind=item.get("kind", 0),
                    range=LspRange(
                        start=LspPosition(**r["start"]),
                        end=LspPosition(**r["end"]),
                    ),
                    detail=item.get("detail", ""),
                )
            )
        except (KeyError, TypeError):
            continue
    return symbols


def file_uri(path: str | Path) -> str:
    """Convert a filesystem path to a file:// URI."""
    resolved = Path(path).resolve().as_posix()
    if not resolved.startswith("/"):
        # Windows: C:/foo → /C:/foo
        resolved = "/" + resolved
    return f"file://{resolved}"


def language_id_for_ext(ext: str) -> str:
    """Map file extension to LSP languageId."""
    return {
        ".py": "python",
        ".ts": "typescript",
        ".tsx": "typescriptreact",
        ".js": "javascript",
        ".jsx": "javascriptreact",
        ".go": "go",
        ".rs": "rust",
        ".java": "java",
        ".c": "c",
        ".cpp": "cpp",
        ".h": "c",
        ".hpp": "cpp",
        ".rb": "ruby",
        ".lua": "lua",
    }.get(ext.lower(), ext.lstrip(".").lower())
