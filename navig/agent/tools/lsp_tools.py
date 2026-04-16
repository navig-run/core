"""
navig.agent.tools.lsp_tools — LSP integration agent tools.

Provides four tools for the agentic ReAct loop to query Language Server
Protocol servers for real-time diagnostics, navigation, and symbol info:

    lsp_diagnostics  — Get errors/warnings for a file (auto-starts server)
    lsp_definition   — Jump to definition at file:line:col
    lsp_references   — Find all references at file:line:col
    lsp_symbols      — List document symbols for a file

All four tools are read-only and safe for plan mode.

Usage::

    from navig.agent.tools import register_lsp_tools
    register_lsp_tools()
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from navig.agent.tool_caps import cap_result
from navig.tools.registry import BaseTool, StatusCallback, ToolResult

logger = logging.getLogger(__name__)


def _read_file_content(path: str) -> str | None:
    """Read file content from disk, returning None on failure."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


# ─────────────────────────────────────────────────────────────
# lsp_diagnostics
# ─────────────────────────────────────────────────────────────


class LspDiagnosticsTool(BaseTool):
    """Return LSP diagnostics (errors and warnings) for a file."""

    name = "lsp_diagnostics"
    description = (
        "Get real-time language diagnostics (errors, warnings) for a source file. "
        "Automatically starts the appropriate language server if not running. "
        "Returns a human-readable summary of issues found."
    )
    owner_only = False
    parameters = [
        {
            "name": "file",
            "type": "string",
            "description": "Absolute or relative path to the source file to check.",
            "required": True,
        },
        {
            "name": "include_warnings",
            "type": "boolean",
            "description": "Include warnings in addition to errors (default: true).",
            "required": False,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        from navig.agent.lsp_manager import (
            format_diagnostic_feedback,
            get_lsp_manager,
        )

        file_path = args.get("file", "")
        if not file_path:
            return ToolResult(name=self.name, success=False, error="file parameter is required")

        resolved = Path(file_path).resolve()
        if not resolved.is_file():
            return ToolResult(
                name=self.name,
                success=False,
                error=f"File not found: {file_path}",
            )

        content = _read_file_content(str(resolved))
        if content is None:
            return ToolResult(
                name=self.name,
                success=False,
                error=f"Could not read file: {file_path}",
            )

        manager = get_lsp_manager()
        include_warnings = args.get("include_warnings", True)

        try:
            if include_warnings:
                diagnostics = await manager.get_all_diagnostics(str(resolved), content)
            else:
                diagnostics = await manager.auto_diagnostics(str(resolved), content)
        except Exception as exc:
            logger.debug("LSP diagnostics failed for %s: %s", file_path, exc)
            return ToolResult(
                name=self.name,
                success=False,
                error=f"LSP server error: {exc}",
            )

        if not diagnostics:
            output = f"No diagnostics for {resolved.name} — file looks clean."
        else:
            output = format_diagnostic_feedback(str(resolved), diagnostics)

        output = cap_result(output, tool_name=self.name)
        return ToolResult(name=self.name, success=True, output=output)


# ─────────────────────────────────────────────────────────────
# lsp_definition
# ─────────────────────────────────────────────────────────────


class LspDefinitionTool(BaseTool):
    """Jump to a symbol's definition via LSP."""

    name = "lsp_definition"
    description = (
        "Go to the definition of a symbol at a given file, line, and column. "
        "Returns the file path and line of the definition. "
        "Line and column are 0-indexed."
    )
    owner_only = False
    parameters = [
        {
            "name": "file",
            "type": "string",
            "description": "Path to the source file.",
            "required": True,
        },
        {
            "name": "line",
            "type": "integer",
            "description": "Zero-indexed line number.",
            "required": True,
        },
        {
            "name": "character",
            "type": "integer",
            "description": "Zero-indexed column/character number.",
            "required": True,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        from navig.agent.lsp_manager import get_lsp_manager

        file_path = args.get("file", "")
        line = args.get("line")
        character = args.get("character")

        if not file_path or line is None or character is None:
            return ToolResult(
                name=self.name,
                success=False,
                error="file, line, and character parameters are required",
            )

        resolved = Path(file_path).resolve()
        if not resolved.is_file():
            return ToolResult(
                name=self.name,
                success=False,
                error=f"File not found: {file_path}",
            )

        # Ensure document is open so server knows about it
        content = _read_file_content(str(resolved))
        if content is not None:
            manager = get_lsp_manager()
            # Trigger didOpen so the server can resolve symbols
            await manager.auto_diagnostics(str(resolved), content, wait=0.2)
        else:
            manager = get_lsp_manager()

        try:
            locations = await manager.goto_definition(str(resolved), int(line), int(character))
        except Exception as exc:
            logger.debug("LSP goto_definition failed: %s", exc)
            return ToolResult(
                name=self.name,
                success=False,
                error=f"LSP error: {exc}",
            )

        if not locations:
            return ToolResult(
                name=self.name,
                success=True,
                output=f"No definition found at {resolved.name}:{line}:{character}",
            )

        lines = [f"Definition location(s) for {resolved.name}:{line}:{character}:"]
        for loc in locations:
            uri = loc.uri
            if uri.startswith("file://"):
                uri = uri[7:]
                # Strip leading slash on Windows paths like /C:/...
                if len(uri) > 2 and uri[0] == "/" and uri[2] == ":":
                    uri = uri[1:]
            start = loc.range.start
            lines.append(f"  {uri}:{start.line + 1}:{start.character + 1}")

        output = cap_result("\n".join(lines), tool_name=self.name)
        return ToolResult(name=self.name, success=True, output=output)


# ─────────────────────────────────────────────────────────────
# lsp_references
# ─────────────────────────────────────────────────────────────


class LspReferencesTool(BaseTool):
    """Find all references to a symbol via LSP."""

    name = "lsp_references"
    description = (
        "Find all references to a symbol at a given file, line, and column. "
        "Returns file paths and lines for each reference. "
        "Line and column are 0-indexed."
    )
    owner_only = False
    parameters = [
        {
            "name": "file",
            "type": "string",
            "description": "Path to the source file.",
            "required": True,
        },
        {
            "name": "line",
            "type": "integer",
            "description": "Zero-indexed line number.",
            "required": True,
        },
        {
            "name": "character",
            "type": "integer",
            "description": "Zero-indexed column/character number.",
            "required": True,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        from navig.agent.lsp_manager import get_lsp_manager

        file_path = args.get("file", "")
        line = args.get("line")
        character = args.get("character")

        if not file_path or line is None or character is None:
            return ToolResult(
                name=self.name,
                success=False,
                error="file, line, and character parameters are required",
            )

        resolved = Path(file_path).resolve()
        if not resolved.is_file():
            return ToolResult(
                name=self.name,
                success=False,
                error=f"File not found: {file_path}",
            )

        # Ensure document is open
        content = _read_file_content(str(resolved))
        if content is not None:
            manager = get_lsp_manager()
            await manager.auto_diagnostics(str(resolved), content, wait=0.2)
        else:
            manager = get_lsp_manager()

        try:
            locations = await manager.find_references(str(resolved), int(line), int(character))
        except Exception as exc:
            logger.debug("LSP find_references failed: %s", exc)
            return ToolResult(
                name=self.name,
                success=False,
                error=f"LSP error: {exc}",
            )

        if not locations:
            return ToolResult(
                name=self.name,
                success=True,
                output=f"No references found at {resolved.name}:{line}:{character}",
            )

        lines = [f"Found {len(locations)} reference(s) for {resolved.name}:{line}:{character}:"]
        for loc in locations:
            uri = loc.uri
            if uri.startswith("file://"):
                uri = uri[7:]
                if len(uri) > 2 and uri[0] == "/" and uri[2] == ":":
                    uri = uri[1:]
            start = loc.range.start
            lines.append(f"  {uri}:{start.line + 1}:{start.character + 1}")

        output = cap_result("\n".join(lines), tool_name=self.name)
        return ToolResult(name=self.name, success=True, output=output)


# ─────────────────────────────────────────────────────────────
# lsp_symbols
# ─────────────────────────────────────────────────────────────

_SYMBOL_KINDS: dict[int, str] = {
    1: "File",
    2: "Module",
    3: "Namespace",
    4: "Package",
    5: "Class",
    6: "Method",
    7: "Property",
    8: "Field",
    9: "Constructor",
    10: "Enum",
    11: "Interface",
    12: "Function",
    13: "Variable",
    14: "Constant",
    15: "String",
    16: "Number",
    17: "Boolean",
    18: "Array",
    19: "Object",
    20: "Key",
    21: "Null",
    22: "EnumMember",
    23: "Struct",
    24: "Event",
    25: "Operator",
    26: "TypeParameter",
}


class LspSymbolsTool(BaseTool):
    """List all document symbols (functions, classes, variables) via LSP."""

    name = "lsp_symbols"
    description = (
        "List all symbols (classes, functions, variables, etc.) in a source file "
        "using the language server. Returns symbol names, kinds, and line numbers."
    )
    owner_only = False
    parameters = [
        {
            "name": "file",
            "type": "string",
            "description": "Path to the source file.",
            "required": True,
        },
    ]

    async def run(
        self,
        args: dict[str, Any],
        on_status: StatusCallback | None = None,
    ) -> ToolResult:
        from navig.agent.lsp_manager import get_lsp_manager

        file_path = args.get("file", "")
        if not file_path:
            return ToolResult(name=self.name, success=False, error="file parameter is required")

        resolved = Path(file_path).resolve()
        if not resolved.is_file():
            return ToolResult(
                name=self.name,
                success=False,
                error=f"File not found: {file_path}",
            )

        # Ensure the document is open
        content = _read_file_content(str(resolved))
        if content is not None:
            manager = get_lsp_manager()
            await manager.auto_diagnostics(str(resolved), content, wait=0.2)
        else:
            manager = get_lsp_manager()

        try:
            symbols = await manager.document_symbols(str(resolved))
        except Exception as exc:
            logger.debug("LSP document_symbols failed: %s", exc)
            return ToolResult(
                name=self.name,
                success=False,
                error=f"LSP error: {exc}",
            )

        if not symbols:
            return ToolResult(
                name=self.name,
                success=True,
                output=f"No symbols found in {resolved.name}.",
            )

        lines = [f"Symbols in {resolved.name} ({len(symbols)}):"]
        for sym in symbols:
            kind_label = _SYMBOL_KINDS.get(sym.kind, f"kind={sym.kind}")
            detail = f" — {sym.detail}" if sym.detail else ""
            lines.append(f"  L{sym.range.start.line + 1}: [{kind_label}] {sym.name}{detail}")

        output = cap_result("\n".join(lines), tool_name=self.name)
        return ToolResult(name=self.name, success=True, output=output)


# ─────────────────────────────────────────────────────────────
# Registration
# ─────────────────────────────────────────────────────────────


def register_lsp_tools() -> None:
    """Register all LSP agent tools."""
    from navig.agent.agent_tool_registry import _AGENT_REGISTRY

    _AGENT_REGISTRY.register(LspDiagnosticsTool(), toolset="lsp")
    _AGENT_REGISTRY.register(LspDefinitionTool(), toolset="lsp")
    _AGENT_REGISTRY.register(LspReferencesTool(), toolset="lsp")
    _AGENT_REGISTRY.register(LspSymbolsTool(), toolset="lsp")
    logger.debug(
        "Agent LSP tools registered: lsp_diagnostics, lsp_definition, lsp_references, lsp_symbols"
    )
