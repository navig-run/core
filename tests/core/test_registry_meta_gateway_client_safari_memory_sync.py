"""
Batch 67: hermetic unit tests for
  - navig/registry/meta.py               (CommandMeta, command_meta decorator, registry)
  - navig/gateway_client.py              (gateway_cli_defaults, gateway_base_url, gateway_request_headers)
  - navig/importers/sources/safari.py    (SafariImporter._walk, parse)
  - navig/memory/sync.py                 (_as_chunk, import_chunks)
"""

from __future__ import annotations

import plistlib
import struct
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig/registry/meta.py
# ---------------------------------------------------------------------------

class TestCommandMeta:
    def test_importable(self) -> None:
        from navig.registry.meta import CommandMeta
        assert CommandMeta is not None

    def test_basic_fields(self) -> None:
        from navig.registry.meta import CommandMeta
        meta = CommandMeta(summary="A command", status="stable", since="1.0")
        assert meta.summary == "A command"
        assert meta.status == "stable"
        assert meta.since == "1.0"
        assert meta.tags == []
        assert meta.deprecated is None

    def test_frozen(self) -> None:
        from navig.registry.meta import CommandMeta
        meta = CommandMeta(summary="x", status="beta", since="2.0")
        with pytest.raises((AttributeError, TypeError)):
            meta.summary = "y"


class TestDeprecationInfo:
    def test_fields(self) -> None:
        from navig.registry.meta import DeprecationInfo
        d = DeprecationInfo(since="1.0", remove_after="3.0", replaced_by="new_cmd")
        assert d.since == "1.0"
        assert d.replace_by if hasattr(d, "replace_by") else d.replaced_by == "new_cmd"


class TestCommandMetaDecorator:
    def test_decorates_function(self) -> None:
        from navig.registry.meta import command_meta, _META_ATTR

        @command_meta(summary="test cmd", status="stable", since="1.0", tags=["net"])
        def my_command():
            pass

        meta = getattr(my_command, _META_ATTR, None)
        assert meta is not None
        assert meta.summary == "test cmd"
        assert "net" in meta.tags

    def test_registers_in_registry(self) -> None:
        from navig.registry.meta import command_meta, get_registry

        @command_meta(summary="reg test", status="experimental", since="2.0")
        def my_reg_cmd():
            pass

        reg = get_registry()
        assert my_reg_cmd.__qualname__ in reg

    def test_returns_original_function(self) -> None:
        from navig.registry.meta import command_meta

        @command_meta(summary="return", status="stable", since="1.0")
        def fn():
            return 42

        assert fn() == 42

    def test_deprecated_key_creates_deprecation_info(self) -> None:
        from navig.registry.meta import command_meta, DeprecationInfo

        @command_meta(
            summary="old cmd",
            status="deprecated",
            since="0.5",
            deprecated={
                "since": "1.0",
                "remove_after": "3.0",
                "replaced_by": "new_cmd",
            },
        )
        def legacy():
            pass

        from navig.registry.meta import _META_ATTR
        meta = getattr(legacy, _META_ATTR)
        assert isinstance(meta.deprecated, DeprecationInfo)
        assert meta.deprecated.replaced_by == "new_cmd"


class TestGetMetaForCallback:
    def test_returns_none_for_none_input(self) -> None:
        from navig.registry.meta import get_meta_for_callback
        assert get_meta_for_callback(None) is None

    def test_returns_none_for_unregistered_fn(self) -> None:
        from navig.registry.meta import get_meta_for_callback
        def unregistered():
            pass
        assert get_meta_for_callback(unregistered) is None

    def test_returns_meta_for_decorated_fn(self) -> None:
        from navig.registry.meta import command_meta, get_meta_for_callback

        @command_meta(summary="lookup test", status="stable", since="1.0")
        def my_lookup_fn():
            pass

        result = get_meta_for_callback(my_lookup_fn)
        assert result is not None
        assert result.summary == "lookup test"


# ---------------------------------------------------------------------------
# navig/gateway_client.py
# ---------------------------------------------------------------------------

class TestGatewayCliDefaults:
    def test_returns_tuple(self) -> None:
        from navig.gateway_client import gateway_cli_defaults
        with patch("navig.config.get_config_manager", side_effect=Exception):
            port, host = gateway_cli_defaults()
        assert isinstance(port, int)
        assert isinstance(host, str)

    def test_defaults_on_error(self) -> None:
        from navig._daemon_defaults import _GATEWAY_PORT
        from navig.gateway_client import gateway_cli_defaults
        with patch("navig.config.get_config_manager", side_effect=RuntimeError):
            port, host = gateway_cli_defaults()
        assert port == _GATEWAY_PORT == 8789
        assert host == "127.0.0.1"

    def test_reads_from_config(self) -> None:
        from navig.gateway_client import gateway_cli_defaults
        mock_cfg = MagicMock()
        mock_cfg._load_global_config.return_value = {"gateway": {"port": 9000, "host": "10.0.0.1"}}
        with patch("navig.config.get_config_manager", return_value=mock_cfg):
            port, host = gateway_cli_defaults()
        assert port == 9000
        assert host == "10.0.0.1"


class TestGatewayBaseUrl:
    def test_returns_http_url(self) -> None:
        from navig.gateway_client import gateway_base_url
        with patch("navig.gateway_client.gateway_cli_defaults", return_value=(8789, "127.0.0.1")):
            url = gateway_base_url()
        assert url == "http://127.0.0.1:8789"

    def test_uses_configured_port(self) -> None:
        from navig.gateway_client import gateway_base_url
        with patch("navig.gateway_client.gateway_cli_defaults", return_value=(9999, "localhost")):
            url = gateway_base_url()
        assert "9999" in url


class TestGatewayRequestHeaders:
    def test_always_has_x_actor(self) -> None:
        from navig.gateway_client import gateway_request_headers
        with patch("navig.config.get_config_manager", side_effect=Exception):
            headers = gateway_request_headers()
        assert headers.get("X-Actor") == "navig-cli"

    def test_adds_bearer_token_when_configured(self) -> None:
        from navig.gateway_client import gateway_request_headers
        mock_cfg = MagicMock()
        mock_cfg._load_global_config.return_value = {
            "gateway": {"auth": {"token": "secret123"}}
        }
        with patch("navig.config.get_config_manager", return_value=mock_cfg):
            headers = gateway_request_headers()
        assert headers.get("Authorization") == "Bearer secret123"

    def test_no_auth_header_when_no_token(self) -> None:
        from navig.gateway_client import gateway_request_headers
        mock_cfg = MagicMock()
        mock_cfg._load_global_config.return_value = {"gateway": {}}
        with patch("navig.config.get_config_manager", return_value=mock_cfg):
            headers = gateway_request_headers()
        assert "Authorization" not in headers


# ---------------------------------------------------------------------------
# navig/importers/sources/safari.py
# ---------------------------------------------------------------------------

def _make_plist(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "Bookmarks.plist"
    path.write_bytes(plistlib.dumps(data))
    return path


class TestSafariImporterWalk:
    def _make(self):
        from navig.importers.sources.safari import SafariImporter
        return SafariImporter()

    def test_empty_children_gives_empty_list(self) -> None:
        importer = self._make()
        items = []
        importer._walk(children=[], folder_path=[], items=items)
        assert items == []

    def test_single_url_node(self) -> None:
        importer = self._make()
        items = []
        node = {"Title": "Example", "URLString": "https://example.com"}
        importer._walk(children=[node], folder_path=[], items=items)
        assert len(items) == 1
        assert items[0].value == "https://example.com"
        assert items[0].label == "Example"

    def test_folder_path_stored_in_meta(self) -> None:
        importer = self._make()
        items = []
        node = {"Title": "Site", "URLString": "https://site.com"}
        importer._walk(children=[node], folder_path=["Favorites", "Work"], items=items)
        assert items[0].meta["folder"] == "Favorites/Work"

    def test_nested_children_recursed(self) -> None:
        importer = self._make()
        items = []
        nested = [{"Title": "Inner", "URLString": "https://inner.com"}]
        node = {"Title": "Folder", "Children": nested}
        importer._walk(children=[node], folder_path=[], items=items)
        assert len(items) == 1
        assert items[0].value == "https://inner.com"

    def test_nodes_without_url_not_added(self) -> None:
        importer = self._make()
        items = []
        node = {"Title": "Empty folder"}
        importer._walk(children=[node], folder_path=[], items=items)
        assert items == []

    def test_source_is_safari(self) -> None:
        importer = self._make()
        items = []
        node = {"Title": "Test", "URLString": "https://test.com"}
        importer._walk(children=[node], folder_path=[], items=items)
        assert items[0].source == "safari"


class TestSafariImporterParse:
    def _make(self):
        from navig.importers.sources.safari import SafariImporter
        return SafariImporter()

    def test_empty_when_file_missing(self) -> None:
        importer = self._make()
        assert importer.parse("/nonexistent.plist") == []

    def test_parses_plist_file(self, tmp_path: Path) -> None:
        data = {
            "Children": [
                {"Title": "Home", "URLString": "https://home.com"},
            ]
        }
        path = _make_plist(tmp_path, data)
        importer = self._make()
        items = importer.parse(str(path))
        assert len(items) == 1
        assert items[0].value == "https://home.com"

    def test_returns_empty_on_parse_error(self, tmp_path: Path) -> None:
        bad_file = tmp_path / "bad.plist"
        bad_file.write_bytes(b"not a plist")
        importer = self._make()
        result = importer.parse(str(bad_file))
        assert result == []


# ---------------------------------------------------------------------------
# navig/memory/sync.py
# ---------------------------------------------------------------------------

class TestAsChunk:
    def test_returns_none_on_missing_content(self) -> None:
        from navig.memory.sync import _as_chunk
        assert _as_chunk({}, "file.py") is None
        assert _as_chunk({"content": ""}, "file.py") is None
        assert _as_chunk({"content": "   "}, "file.py") is None

    def test_returns_chunk_on_valid_item(self) -> None:
        from navig.memory.sync import _as_chunk
        from navig.memory.storage import MemoryChunk
        item = {"content": "def foo(): pass", "id": "abc123", "file_path": "a.py"}
        chunk = _as_chunk(item, "fallback.py")
        assert isinstance(chunk, MemoryChunk)
        assert chunk.content == "def foo(): pass"

    def test_uses_fallback_file_when_file_path_missing(self) -> None:
        from navig.memory.sync import _as_chunk
        item = {"content": "hello", "id": "x"}
        chunk = _as_chunk(item, "remote/default/sync")
        assert chunk.file_path == "remote/default/sync"

    def test_auto_generates_chunk_id_when_missing(self) -> None:
        from navig.memory.sync import _as_chunk
        item = {"content": "generated"}
        chunk = _as_chunk(item, "f.py")
        assert chunk.id.startswith("sync::")

    def test_parses_json_string_metadata(self) -> None:
        from navig.memory.sync import _as_chunk
        import json
        item = {"content": "test", "metadata": json.dumps({"key": "value"})}
        chunk = _as_chunk(item, "f.py")
        assert chunk.metadata == {"key": "value"}

    def test_handles_invalid_metadata_string(self) -> None:
        from navig.memory.sync import _as_chunk
        item = {"content": "test", "metadata": "not-json"}
        chunk = _as_chunk(item, "f.py")
        assert isinstance(chunk.metadata, dict)


class TestImportChunks:
    def test_skips_non_dict_items(self, tmp_path: Path) -> None:
        from navig.memory.sync import import_chunks
        imported, skipped = import_chunks(tmp_path / "test.db", ["not a dict", None, 42])
        assert skipped == 3

    def test_skips_empty_content(self, tmp_path: Path) -> None:
        from navig.memory.sync import import_chunks
        items = [{"content": "", "id": "1"}, {"content": "  ", "id": "2"}]
        imported, skipped = import_chunks(tmp_path / "test.db", items)
        assert skipped == 2

    def test_imports_valid_chunks(self, tmp_path: Path) -> None:
        from navig.memory.sync import import_chunks
        items = [
            {"content": "def foo(): pass", "id": "c1", "file_path": "a.py"},
            {"content": "def bar(): pass", "id": "c2", "file_path": "b.py"},
        ]
        imported, skipped = import_chunks(tmp_path / "test.db", items)
        assert imported == 2
        assert skipped == 0

    def test_returns_tuple(self, tmp_path: Path) -> None:
        from navig.memory.sync import import_chunks
        result = import_chunks(tmp_path / "test.db", [])
        assert isinstance(result, tuple) and len(result) == 2
