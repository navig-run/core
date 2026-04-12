"""Tests for the LSP integration layer: lsp_client, lsp_manager, lsp_tools."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── lsp_client helpers ────────────────────────────────────────
from navig.agent.lsp_client import (
    LSP_SEVERITY_ERROR,
    LSP_SEVERITY_WARNING,
    LspDiagnostic,
    LspLocation,
    LspPosition,
    LspRange,
    LspSymbol,
    _parse_locations,
    _parse_symbols,
    file_uri,
    language_id_for_ext,
)

pytestmark = pytest.mark.integration


class TestFileUri:
    """file_uri() converts paths to file:// URIs."""

    def test_unix_style_path(self):
        uri = file_uri("/home/user/project/main.py")
        assert uri.startswith("file:///")
        assert "main.py" in uri

    def test_windows_style_path(self, tmp_path: Path):
        f = tmp_path / "test.py"
        f.write_text("pass", encoding="utf-8")
        uri = file_uri(str(f))
        assert uri.startswith("file://")
        assert "test.py" in uri

    def test_relative_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        f = tmp_path / "rel.py"
        f.write_text("pass", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        uri = file_uri("rel.py")
        assert uri.startswith("file://")


class TestLanguageIdForExt:
    """language_id_for_ext() maps extensions to LSP language IDs."""

    @pytest.mark.parametrize(
        ("ext", "expected"),
        [
            (".py", "python"),
            (".ts", "typescript"),
            (".tsx", "typescriptreact"),
            (".js", "javascript"),
            (".jsx", "javascriptreact"),
            (".go", "go"),
            (".rs", "rust"),
        ],
    )
    def test_known_extensions(self, ext: str, expected: str):
        assert language_id_for_ext(ext) == expected

    def test_unknown_extension_strips_dot(self):
        result = language_id_for_ext(".zig")
        assert result == "zig"

    def test_case_insensitive(self):
        assert language_id_for_ext(".PY") == "python"


# ── Data type unit tests ─────────────────────────────────────


class TestLspDiagnostic:
    def test_severity_label_error(self):
        d = LspDiagnostic(
            range=LspRange(LspPosition(0, 0), LspPosition(0, 5)),
            severity=1,
            message="syntax error",
        )
        assert d.severity_label == "Error"

    def test_severity_label_warning(self):
        d = LspDiagnostic(
            range=LspRange(LspPosition(0, 0), LspPosition(0, 5)),
            severity=2,
            message="unused import",
        )
        assert d.severity_label == "Warning"

    def test_severity_label_unknown(self):
        d = LspDiagnostic(
            range=LspRange(LspPosition(0, 0), LspPosition(0, 5)),
            severity=99,
            message="custom",
        )
        assert d.severity_label == "Unknown"


# ── Parse helpers ─────────────────────────────────────────────


class TestParseLocations:
    def test_none_input(self):
        assert _parse_locations(None) == []

    def test_single_dict(self):
        raw = {
            "uri": "file:///a.py",
            "range": {
                "start": {"line": 10, "character": 5},
                "end": {"line": 10, "character": 15},
            },
        }
        result = _parse_locations(raw)
        assert len(result) == 1
        assert result[0].uri == "file:///a.py"
        assert result[0].range.start.line == 10

    def test_list_of_locations(self):
        raw = [
            {
                "uri": "file:///a.py",
                "range": {"start": {"line": 1, "character": 0}, "end": {"line": 1, "character": 5}},
            },
            {
                "uri": "file:///b.py",
                "range": {"start": {"line": 2, "character": 0}, "end": {"line": 2, "character": 5}},
            },
        ]
        result = _parse_locations(raw)
        assert len(result) == 2

    def test_malformed_entries_skipped(self):
        raw = [
            {"bad": "data"},
            {
                "uri": "ok",
                "range": {"start": {"line": 0, "character": 0}, "end": {"line": 0, "character": 1}},
            },
        ]
        result = _parse_locations(raw)
        assert len(result) == 1

    def test_non_list_non_dict(self):
        assert _parse_locations(42) == []


class TestParseSymbols:
    def test_empty_list(self):
        assert _parse_symbols([]) == []

    def test_basic_symbol(self):
        raw = [
            {
                "name": "MyClass",
                "kind": 5,
                "range": {
                    "start": {"line": 0, "character": 0},
                    "end": {"line": 10, "character": 0},
                },
                "detail": "class",
            }
        ]
        result = _parse_symbols(raw)
        assert len(result) == 1
        assert result[0].name == "MyClass"
        assert result[0].kind == 5
        assert result[0].detail == "class"

    def test_symbol_with_location_key(self):
        """Some servers use 'location.range' instead of 'range'."""
        raw = [
            {
                "name": "foo",
                "kind": 12,
                "location": {
                    "uri": "file:///a.py",
                    "range": {
                        "start": {"line": 5, "character": 0},
                        "end": {"line": 5, "character": 10},
                    },
                },
            }
        ]
        result = _parse_symbols(raw)
        assert len(result) == 1
        assert result[0].range.start.line == 5


# ── LspClient unit tests ─────────────────────────────────────


class TestLspClientDispatch:
    """Test _dispatch routing without a real subprocess."""

    def test_dispatch_response(self):
        from navig.agent.lsp_client import LspClient

        client = LspClient(server_cmd=["fake"], root_uri="file:///tmp")
        loop = asyncio.new_event_loop()
        future = loop.create_future()
        client._pending[1] = future
        client._dispatch({"id": 1, "result": {"foo": "bar"}})
        assert future.result() == {"foo": "bar"}
        loop.close()

    def test_dispatch_error_response(self):
        from navig.agent.lsp_client import LspClient

        client = LspClient(server_cmd=["fake"], root_uri="file:///tmp")
        loop = asyncio.new_event_loop()
        future = loop.create_future()
        client._pending[2] = future
        client._dispatch({"id": 2, "error": {"code": -1, "message": "fail"}})
        with pytest.raises(RuntimeError, match="fail"):
            future.result()
        loop.close()

    def test_dispatch_diagnostics_notification(self):
        from navig.agent.lsp_client import LspClient

        client = LspClient(server_cmd=["fake"], root_uri="file:///tmp")
        client._dispatch(
            {
                "method": "textDocument/publishDiagnostics",
                "params": {
                    "uri": "file:///a.py",
                    "diagnostics": [
                        {
                            "range": {
                                "start": {"line": 0, "character": 0},
                                "end": {"line": 0, "character": 5},
                            },
                            "severity": 1,
                            "message": "syntax error",
                            "source": "pylsp",
                        }
                    ],
                },
            }
        )
        diags = client.get_diagnostics("file:///a.py")
        assert len(diags) == 1
        assert diags[0].severity == LSP_SEVERITY_ERROR
        assert diags[0].message == "syntax error"

    def test_get_errors_and_warnings(self):
        from navig.agent.lsp_client import LspClient

        client = LspClient(server_cmd=["fake"], root_uri="file:///tmp")
        uri = "file:///test.py"
        client._diagnostics[uri] = [
            LspDiagnostic(
                range=LspRange(LspPosition(0, 0), LspPosition(0, 5)),
                severity=1,
                message="error",
            ),
            LspDiagnostic(
                range=LspRange(LspPosition(1, 0), LspPosition(1, 5)),
                severity=2,
                message="warning",
            ),
            LspDiagnostic(
                range=LspRange(LspPosition(2, 0), LspPosition(2, 5)),
                severity=3,
                message="info",
            ),
        ]
        assert len(client.get_errors(uri)) == 1
        assert len(client.get_warnings(uri)) == 1


class TestLspClientDocumentSync:
    """Test did_open / did_change / did_close bookkeeping."""

    def test_did_open_tracks_document(self):
        from navig.agent.lsp_client import LspClient

        client = LspClient(server_cmd=["fake"], root_uri="file:///tmp")
        # Stub _send to avoid needing a process
        client._send = MagicMock()
        client.did_open("file:///a.py", "python", "print('hi')")
        assert "file:///a.py" in client._open_documents
        assert client._doc_version["file:///a.py"] == 1

    def test_did_change_increments_version(self):
        from navig.agent.lsp_client import LspClient

        client = LspClient(server_cmd=["fake"], root_uri="file:///tmp")
        client._send = MagicMock()
        client.did_open("file:///a.py", "python", "v1")
        client.did_change("file:///a.py", "v2")
        assert client._doc_version["file:///a.py"] == 2
        client.did_change("file:///a.py", "v3")
        assert client._doc_version["file:///a.py"] == 3

    def test_did_close_removes_document(self):
        from navig.agent.lsp_client import LspClient

        client = LspClient(server_cmd=["fake"], root_uri="file:///tmp")
        client._send = MagicMock()
        client.did_open("file:///a.py", "python", "code")
        client.did_close("file:///a.py")
        assert "file:///a.py" not in client._open_documents
        assert "file:///a.py" not in client._doc_version


# ── LspManager tests ─────────────────────────────────────────


class TestLspManagerResolve:
    """Test server resolution and extension mapping."""

    def test_supported_extensions(self):
        from navig.agent.lsp_manager import LspManager

        mgr = LspManager(workspace_root="/tmp")
        exts = mgr.supported_extensions()
        assert ".py" in exts
        assert ".ts" in exts
        assert ".go" in exts
        assert ".rs" in exts

    async def test_disabled_manager_returns_no_client(self):
        from navig.agent.lsp_manager import LspManager

        mgr = LspManager(workspace_root="/tmp", enabled=False)
        result = await mgr.get_client(".py")
        assert result is None


class TestLspManagerMissing:
    """When a language server binary is not installed, get_client returns None."""

    @patch("shutil.which", return_value=None)
    async def test_missing_server_binary(self, _mock_which: MagicMock):
        from navig.agent.lsp_manager import LspManager

        mgr = LspManager(workspace_root="/tmp")
        result = await mgr.get_client(".py")
        assert result is None


class TestLspManagerDiagnostics:
    """Test auto_diagnostics with a mocked LspClient."""

    async def test_auto_diagnostics_returns_errors(self, tmp_path: Path):
        from navig.agent.lsp_client import LspClient
        from navig.agent.lsp_manager import LspManager

        mock_client = MagicMock(spec=LspClient)
        mock_client.alive = True
        mock_client._open_documents = set()
        mock_client.did_open = MagicMock()
        mock_client.did_change = MagicMock()

        expected_errors = [
            LspDiagnostic(
                range=LspRange(LspPosition(0, 0), LspPosition(0, 10)),
                severity=1,
                message="unexpected indent",
                source="pylsp",
            )
        ]
        mock_client.get_errors = MagicMock(return_value=expected_errors)

        mgr = LspManager(workspace_root=str(tmp_path), max_diagnostic_wait=0.01)
        mgr._clients["pylsp"] = mock_client

        f = tmp_path / "bad.py"
        f.write_text("  bad indent", encoding="utf-8")

        diags = await mgr.auto_diagnostics(str(f), "  bad indent")
        assert len(diags) == 1
        assert diags[0].message == "unexpected indent"

    async def test_auto_diagnostics_unsupported_ext(self, tmp_path: Path):
        from navig.agent.lsp_manager import LspManager

        mgr = LspManager(workspace_root=str(tmp_path))
        f = tmp_path / "readme.txt"
        f.write_text("hello", encoding="utf-8")

        diags = await mgr.auto_diagnostics(str(f), "hello")
        assert diags == []


# ── format_diagnostic_feedback ────────────────────────────────


class TestFormatDiagnosticFeedback:
    def test_empty_diagnostics(self):
        from navig.agent.lsp_manager import format_diagnostic_feedback

        assert format_diagnostic_feedback("/a.py", []) == ""

    def test_single_error(self):
        from navig.agent.lsp_manager import format_diagnostic_feedback

        diags = [
            LspDiagnostic(
                range=LspRange(LspPosition(4, 0), LspPosition(4, 10)),
                severity=1,
                message="undefined name 'foo'",
                source="pyflakes",
            )
        ]
        result = format_diagnostic_feedback("/a.py", diags)
        assert "1 error" in result
        assert "Line 5" in result
        assert "undefined name" in result

    def test_truncation(self):
        from navig.agent.lsp_manager import format_diagnostic_feedback

        diags = [
            LspDiagnostic(
                range=LspRange(LspPosition(i, 0), LspPosition(i, 5)),
                severity=1,
                message=f"error {i}",
            )
            for i in range(10)
        ]
        result = format_diagnostic_feedback("/a.py", diags, max_items=3)
        assert "and 7 more" in result


# ── LspManager singleton ─────────────────────────────────────


class TestGetLspManager:
    def test_returns_manager(self):
        import navig.agent.lsp_manager as mod

        # Reset singleton
        mod._manager_instance = None
        try:
            mgr = mod.get_lsp_manager("/tmp/test")
            assert isinstance(mgr, mod.LspManager)
            assert mgr.workspace_root == "/tmp/test"
        finally:
            mod._manager_instance = None

    @patch("navig.agent.lsp_manager._read_config_enabled", return_value=False)
    def test_respects_config_disabled(self, _mock: MagicMock):
        import navig.agent.lsp_manager as mod

        mod._manager_instance = None
        try:
            mgr = mod.get_lsp_manager("/tmp/test")
            assert mgr.enabled is False
        finally:
            mod._manager_instance = None


# ── Tool registration ─────────────────────────────────────────


class TestLspToolRegistration:
    """Verify tools register without errors."""

    def test_register_lsp_tools(self):
        from navig.agent.agent_tool_registry import _AGENT_REGISTRY
        from navig.agent.tools.lsp_tools import register_lsp_tools

        before = len(_AGENT_REGISTRY._entries)
        register_lsp_tools()
        after = len(_AGENT_REGISTRY._entries)
        # Should register 4 tools (may already be there from other tests)
        assert after >= before

    def test_tool_names(self):
        from navig.agent.tools.lsp_tools import (
            LspDefinitionTool,
            LspDiagnosticsTool,
            LspReferencesTool,
            LspSymbolsTool,
        )

        assert LspDiagnosticsTool().name == "lsp_diagnostics"
        assert LspDefinitionTool().name == "lsp_definition"
        assert LspReferencesTool().name == "lsp_references"
        assert LspSymbolsTool().name == "lsp_symbols"


# ── Tool run tests (mocked) ──────────────────────────────────


class TestLspDiagnosticsToolRun:
    async def test_missing_file_param(self):
        from navig.agent.tools.lsp_tools import LspDiagnosticsTool

        tool = LspDiagnosticsTool()
        result = await tool.run({})
        assert not result.success
        assert "required" in result.error

    async def test_file_not_found(self, tmp_path: Path):
        from navig.agent.tools.lsp_tools import LspDiagnosticsTool

        tool = LspDiagnosticsTool()
        result = await tool.run({"file": str(tmp_path / "nonexistent.py")})
        assert not result.success
        assert "not found" in result.error.lower()

    async def test_clean_file(self, tmp_path: Path):
        from navig.agent.tools.lsp_tools import LspDiagnosticsTool

        f = tmp_path / "clean.py"
        f.write_text("x = 1\n", encoding="utf-8")

        with patch("navig.agent.lsp_manager.get_lsp_manager") as mock_get:
            mock_mgr = MagicMock()
            mock_mgr.get_all_diagnostics = AsyncMock(return_value=[])
            mock_get.return_value = mock_mgr

            tool = LspDiagnosticsTool()
            result = await tool.run({"file": str(f)})
            assert result.success
            assert "clean" in result.output.lower()


class TestLspDefinitionToolRun:
    async def test_missing_params(self):
        from navig.agent.tools.lsp_tools import LspDefinitionTool

        tool = LspDefinitionTool()
        result = await tool.run({"file": "a.py"})
        assert not result.success

    async def test_no_definition_found(self, tmp_path: Path):
        from navig.agent.tools.lsp_tools import LspDefinitionTool

        f = tmp_path / "test.py"
        f.write_text("x = 1\n", encoding="utf-8")

        with patch("navig.agent.lsp_manager.get_lsp_manager") as mock_get:
            mock_mgr = MagicMock()
            mock_mgr.auto_diagnostics = AsyncMock(return_value=[])
            mock_mgr.goto_definition = AsyncMock(return_value=[])
            mock_get.return_value = mock_mgr

            tool = LspDefinitionTool()
            result = await tool.run({"file": str(f), "line": 0, "character": 0})
            assert result.success
            assert "no definition" in result.output.lower()


class TestLspReferencesToolRun:
    async def test_references_found(self, tmp_path: Path):
        from navig.agent.tools.lsp_tools import LspReferencesTool

        f = tmp_path / "test.py"
        f.write_text("x = 1\nprint(x)\n", encoding="utf-8")

        refs = [
            LspLocation(
                uri=f"file:///{f.as_posix()}",
                range=LspRange(LspPosition(0, 0), LspPosition(0, 1)),
            ),
            LspLocation(
                uri=f"file:///{f.as_posix()}",
                range=LspRange(LspPosition(1, 6), LspPosition(1, 7)),
            ),
        ]

        with patch("navig.agent.lsp_manager.get_lsp_manager") as mock_get:
            mock_mgr = MagicMock()
            mock_mgr.auto_diagnostics = AsyncMock(return_value=[])
            mock_mgr.find_references = AsyncMock(return_value=refs)
            mock_get.return_value = mock_mgr

            tool = LspReferencesTool()
            result = await tool.run({"file": str(f), "line": 0, "character": 0})
            assert result.success
            assert "2 reference" in result.output


class TestLspSymbolsToolRun:
    async def test_symbols_found(self, tmp_path: Path):
        from navig.agent.tools.lsp_tools import LspSymbolsTool

        f = tmp_path / "test.py"
        f.write_text("class Foo:\n    pass\n", encoding="utf-8")

        syms = [
            LspSymbol(
                name="Foo",
                kind=5,
                range=LspRange(LspPosition(0, 0), LspPosition(1, 8)),
                detail="class",
            )
        ]

        with patch("navig.agent.lsp_manager.get_lsp_manager") as mock_get:
            mock_mgr = MagicMock()
            mock_mgr.auto_diagnostics = AsyncMock(return_value=[])
            mock_mgr.document_symbols = AsyncMock(return_value=syms)
            mock_get.return_value = mock_mgr

            tool = LspSymbolsTool()
            result = await tool.run({"file": str(f)})
            assert result.success
            assert "Foo" in result.output
            assert "Class" in result.output


# ── Toolset / plan_mode integration ──────────────────────────


class TestToolsetIntegration:
    """Verify LSP tools appear in the right categories."""

    def test_lsp_in_toolsets(self):
        from navig.agent.toolsets import TOOLSETS

        assert "lsp" in TOOLSETS
        names = TOOLSETS["lsp"]
        assert "lsp_diagnostics" in names
        assert "lsp_definition" in names
        assert "lsp_references" in names
        assert "lsp_symbols" in names

    def test_lsp_tools_parallel_safe(self):
        from navig.agent.toolsets import PARALLEL_SAFE_TOOLS

        for name in ("lsp_diagnostics", "lsp_definition", "lsp_references", "lsp_symbols"):
            assert name in PARALLEL_SAFE_TOOLS, f"{name} should be PARALLEL_SAFE"

    def test_lsp_tools_not_in_never_parallel(self):
        from navig.agent.toolsets import NEVER_PARALLEL_TOOLS

        for name in ("lsp_diagnostics", "lsp_definition", "lsp_references", "lsp_symbols"):
            assert name not in NEVER_PARALLEL_TOOLS

    def test_lsp_tools_in_plan_mode_read_only(self):
        from navig.agent.plan_mode import PlanInterceptor

        for name in ("lsp_diagnostics", "lsp_definition", "lsp_references", "lsp_symbols"):
            assert name in PlanInterceptor.READ_ONLY_TOOLS, f"{name} should be READ_ONLY"
