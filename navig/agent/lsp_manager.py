"""Manage per-language LSP server lifecycles.

LspManager lazily starts one ``LspClient`` per file-extension group,
auto-detects installed language servers, and exposes high-level
methods for diagnostics, definitions, references, and symbols.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from navig.agent.lsp_client import (
    LSP_DIAGNOSTIC_WAIT,
    LSP_SEVERITY_WARNING,
    LspClient,
    LspDiagnostic,
    LspLocation,
    LspSymbol,
    file_uri,
    language_id_for_ext,
)

logger = logging.getLogger(__name__)


# ── Configuration ─────────────────────────────────────────────


@dataclass
class LspServerSpec:
    """How to launch a particular language server."""

    command: list[str]
    extensions: list[str]
    language_ids: dict[str, str] = field(default_factory=dict)


# Built-in server registry (user-extensible via config).
DEFAULT_SERVERS: dict[str, LspServerSpec] = {
    "pylsp": LspServerSpec(
        command=["pylsp"],
        extensions=[".py"],
        language_ids={".py": "python"},
    ),
    "tsserver": LspServerSpec(
        command=["typescript-language-server", "--stdio"],
        extensions=[".ts", ".tsx", ".js", ".jsx"],
        language_ids={
            ".ts": "typescript",
            ".tsx": "typescriptreact",
            ".js": "javascript",
            ".jsx": "javascriptreact",
        },
    ),
    "gopls": LspServerSpec(
        command=["gopls", "serve"],
        extensions=[".go"],
        language_ids={".go": "go"},
    ),
    "rust-analyzer": LspServerSpec(
        command=["rust-analyzer"],
        extensions=[".rs"],
        language_ids={".rs": "rust"},
    ),
}

# Maps file extension → server name.  Populated from DEFAULT_SERVERS.
_EXT_TO_SERVER: dict[str, str] = {}
for _name, _spec in DEFAULT_SERVERS.items():
    for _ext in _spec.extensions:
        _EXT_TO_SERVER.setdefault(_ext, _name)


# ── Manager ───────────────────────────────────────────────────


@dataclass
class LspManager:
    """Manages LSP clients per language, with lazy startup and graceful shutdown."""

    workspace_root: str
    enabled: bool = True
    max_diagnostic_wait: float = LSP_DIAGNOSTIC_WAIT
    _custom_servers: dict[str, LspServerSpec] = field(default_factory=dict, init=False, repr=False)

    _clients: dict[str, LspClient] = field(default_factory=dict, init=False, repr=False)
    _starting: dict[str, asyncio.Task[LspClient | None]] = field(
        default_factory=dict, init=False, repr=False
    )

    # ── server resolution ─────────────────────────────────────

    def register_server(self, name: str, spec: LspServerSpec) -> None:
        """Register (or override) a language server spec at runtime."""
        self._custom_servers[name] = spec
        for ext in spec.extensions:
            _EXT_TO_SERVER[ext] = name

    def _resolve_spec(self, ext: str) -> LspServerSpec | None:
        server_name = _EXT_TO_SERVER.get(ext)
        if not server_name:
            return None
        return self._custom_servers.get(server_name) or DEFAULT_SERVERS.get(server_name)

    # ── client lifecycle ──────────────────────────────────────

    async def get_client(self, ext: str) -> LspClient | None:
        """Return a running LspClient for the given extension, or None."""
        if not self.enabled:
            return None

        spec = self._resolve_spec(ext)
        if spec is None:
            return None

        server_name = _EXT_TO_SERVER[ext]

        # Already running?
        existing = self._clients.get(server_name)
        if existing and existing.alive:
            return existing

        # Start in progress?
        if server_name in self._starting:
            return await self._starting[server_name]

        # Launch
        self._starting[server_name] = asyncio.create_task(self._start_server(server_name, spec))
        try:
            return await self._starting[server_name]
        finally:
            self._starting.pop(server_name, None)

    async def _start_server(self, name: str, spec: LspServerSpec) -> LspClient | None:
        executable = spec.command[0]
        if not shutil.which(executable):
            logger.debug("LSP server %r not installed (executable: %s)", name, executable)
            return None

        root_uri = file_uri(self.workspace_root)
        client = LspClient(server_cmd=list(spec.command), root_uri=root_uri)
        try:
            await client.start()
        except Exception:
            logger.debug("LSP server %r failed to start", name, exc_info=True)
            return None

        self._clients[name] = client
        logger.debug("LSP server %r started for workspace %s", name, self.workspace_root)
        return client

    async def shutdown_all(self) -> None:
        """Gracefully shut down every running LSP server."""
        tasks = [c.shutdown() for c in self._clients.values()]
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._clients.clear()
        logger.debug("All LSP servers shut down")

    # ── high-level API ────────────────────────────────────────

    async def auto_diagnostics(
        self,
        file_path: str,
        content: str,
        *,
        wait: float | None = None,
    ) -> list[LspDiagnostic]:
        """Send didOpen/didChange for *file_path* and return errors after a brief wait.

        This is the main integration point: called after every file write so
        the agent can immediately see syntax/type errors it introduced.
        """
        path = Path(file_path)
        ext = path.suffix
        client = await self.get_client(ext)
        if client is None:
            return []

        uri = file_uri(path)
        lang_id = language_id_for_ext(ext)

        if uri not in client._open_documents:
            client.did_open(uri, lang_id, content)
        else:
            client.did_change(uri, content)

        # Give the server time to compute diagnostics
        await asyncio.sleep(wait if wait is not None else self.max_diagnostic_wait)
        return client.get_errors(uri)

    async def get_all_diagnostics(
        self,
        file_path: str,
        content: str,
        *,
        min_severity: int = LSP_SEVERITY_WARNING,
        wait: float | None = None,
    ) -> list[LspDiagnostic]:
        """Like ``auto_diagnostics`` but returns warnings + errors."""
        path = Path(file_path)
        ext = path.suffix
        client = await self.get_client(ext)
        if client is None:
            return []

        uri = file_uri(path)
        lang_id = language_id_for_ext(ext)

        if uri not in client._open_documents:
            client.did_open(uri, lang_id, content)
        else:
            client.did_change(uri, content)

        await asyncio.sleep(wait if wait is not None else self.max_diagnostic_wait)
        return [d for d in client.get_diagnostics(uri) if d.severity <= min_severity]

    async def goto_definition(self, file_path: str, line: int, character: int) -> list[LspLocation]:
        ext = Path(file_path).suffix
        client = await self.get_client(ext)
        if not client:
            return []
        return await client.goto_definition(file_uri(file_path), line, character)

    async def find_references(self, file_path: str, line: int, character: int) -> list[LspLocation]:
        ext = Path(file_path).suffix
        client = await self.get_client(ext)
        if not client:
            return []
        return await client.find_references(file_uri(file_path), line, character)

    async def document_symbols(self, file_path: str) -> list[LspSymbol]:
        ext = Path(file_path).suffix
        client = await self.get_client(ext)
        if not client:
            return []
        return await client.document_symbols(file_uri(file_path))

    def supported_extensions(self) -> list[str]:
        """Return file extensions that have a known language server."""
        return sorted(_EXT_TO_SERVER.keys())

    @property
    def active_servers(self) -> list[str]:
        return [name for name, c in self._clients.items() if c.alive]


# ── Post-write hook ───────────────────────────────────────────


def format_diagnostic_feedback(
    file_path: str,
    diagnostics: list[LspDiagnostic],
    *,
    max_items: int = 5,
) -> str:
    """Format LSP diagnostics as a compact feedback string for the agent."""
    if not diagnostics:
        return ""

    n = len(diagnostics)
    lines = [f"\n⚠️ Your edit to {file_path} caused {n} error(s):"]
    for d in diagnostics[:max_items]:
        lines.append(f"  Line {d.range.start.line + 1}: [{d.source or 'lsp'}] {d.message}")
    if n > max_items:
        lines.append(f"  ... and {n - max_items} more")
    lines.append("\nPlease fix these errors.")
    return "\n".join(lines)


# ── Singleton accessor ────────────────────────────────────────

_manager_instance: LspManager | None = None


def get_lsp_manager(workspace_root: str | None = None) -> LspManager:
    """Get or create the global LspManager singleton."""
    global _manager_instance  # noqa: PLW0603
    if _manager_instance is None:
        root = workspace_root or str(Path.cwd())
        enabled = _read_config_enabled()
        _manager_instance = LspManager(workspace_root=root, enabled=enabled)
    return _manager_instance


def _read_config_enabled() -> bool:
    """Read agent.lsp.enabled from navig config (default True)."""
    try:
        from navig.config import get_config_manager

        mgr = get_config_manager()
        agent_cfg = mgr.global_config.get("agent", {})
        lsp_cfg = agent_cfg.get("lsp", {})
        return bool(lsp_cfg.get("enabled", True))
    except Exception:
        return True
