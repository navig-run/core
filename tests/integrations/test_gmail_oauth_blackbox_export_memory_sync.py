"""Batch 58 — gmail oauth_config, blackbox export, memory sync."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# navig.connectors.gmail.oauth_config
# ---------------------------------------------------------------------------

class TestBuildGmailOAuthConfig:
    def _build(self, **kw):
        from navig.connectors.gmail.oauth_config import build_gmail_oauth_config
        return build_gmail_oauth_config(**kw)

    def test_returns_oauth_provider_config(self):
        from navig.providers.oauth import OAuthProviderConfig
        cfg = self._build(client_id="cid123")
        assert isinstance(cfg, OAuthProviderConfig)

    def test_name_is_gmail(self):
        cfg = self._build(client_id="cid")
        assert cfg.name == "Gmail"

    def test_client_id_propagated(self):
        cfg = self._build(client_id="my-client-id")
        assert cfg.client_id == "my-client-id"

    def test_client_secret_default_none(self):
        cfg = self._build(client_id="cid")
        assert cfg.client_secret is None

    def test_client_secret_propagated(self):
        cfg = self._build(client_id="cid", client_secret="sec")
        assert cfg.client_secret == "sec"

    def test_scopes_match_gmail_scopes(self):
        from navig.connectors.gmail.oauth_config import GMAIL_SCOPES, build_gmail_oauth_config
        cfg = build_gmail_oauth_config(client_id="cid")
        assert cfg.scopes == GMAIL_SCOPES

    def test_scopes_contain_gmail_readonly(self):
        from navig.connectors.gmail.oauth_config import GMAIL_SCOPES
        assert "https://www.googleapis.com/auth/gmail.readonly" in GMAIL_SCOPES

    def test_scopes_contain_gmail_send(self):
        from navig.connectors.gmail.oauth_config import GMAIL_SCOPES
        assert "https://www.googleapis.com/auth/gmail.send" in GMAIL_SCOPES

    def test_scopes_contain_openid(self):
        from navig.connectors.gmail.oauth_config import GMAIL_SCOPES
        assert "openid" in GMAIL_SCOPES

    def test_authorize_url_uses_google_auth_url(self):
        from navig.connectors.google_oauth_constants import GOOGLE_AUTH_URL
        cfg = self._build(client_id="cid")
        assert cfg.authorize_url == GOOGLE_AUTH_URL

    def test_token_url_uses_google_token_url(self):
        from navig.connectors.google_oauth_constants import GOOGLE_TOKEN_URL
        cfg = self._build(client_id="cid")
        assert cfg.token_url == GOOGLE_TOKEN_URL

    def test_userinfo_url_uses_google_userinfo_url(self):
        from navig.connectors.google_oauth_constants import GOOGLE_USERINFO_URL
        cfg = self._build(client_id="cid")
        assert cfg.userinfo_url == GOOGLE_USERINFO_URL


# ---------------------------------------------------------------------------
# navig.blackbox.export — export_bundle
# ---------------------------------------------------------------------------

class TestExportBundle:
    def _make_bundle(self):
        return MagicMock()

    def test_non_encrypted_returns_zip_path(self, tmp_path):
        from navig.blackbox.export import export_bundle
        bundle = self._make_bundle()
        fake_zip = tmp_path / "bundle.navbox"
        fake_zip.write_bytes(b"ZIP")

        with patch("navig.blackbox.export.write_bundle", return_value=fake_zip) as wb:
            result = export_bundle(bundle, tmp_path / "out.navbox", encrypted=False)

        assert result == fake_zip
        wb.assert_called_once_with(bundle, tmp_path / "out.navbox")

    def test_non_encrypted_is_default(self, tmp_path):
        from navig.blackbox.export import export_bundle
        bundle = self._make_bundle()
        fake_zip = tmp_path / "bundle.navbox"
        fake_zip.write_bytes(b"ZIP")

        with patch("navig.blackbox.export.write_bundle", return_value=fake_zip):
            result = export_bundle(bundle, tmp_path / "out.navbox")

        assert result == fake_zip

    def test_encrypted_writes_enc_file(self, tmp_path):
        from navig.blackbox.export import export_bundle
        bundle = self._make_bundle()

        zip_path = tmp_path / "bundle.navbox"
        zip_path.write_bytes(b"RAW_ZIP_DATA")

        enc_path = zip_path.with_suffix(".navbox.enc")
        sealed_data = b"SEALED_DATA"

        mock_vault = MagicMock()
        mock_vault.engine.return_value.derive_key.return_value = b"master_key"

        mock_crypto = MagicMock()
        mock_crypto.seal.return_value = sealed_data

        with (
            patch("navig.blackbox.export.write_bundle", return_value=zip_path),
            patch("navig.vault.core.get_vault", return_value=mock_vault, create=True),
            patch("navig.vault.crypto.CryptoEngine", mock_crypto, create=True),
        ):
            result = export_bundle(bundle, tmp_path / "out.navbox", encrypted=True)

        assert result == enc_path
        assert enc_path.read_bytes() == sealed_data
        assert not zip_path.exists()  # plaintext removed

    def test_encrypted_failure_returns_unencrypted(self, tmp_path):
        from navig.blackbox.export import export_bundle
        bundle = self._make_bundle()

        zip_path = tmp_path / "bundle.navbox"
        zip_path.write_bytes(b"RAW_DATA")

        with (
            patch("navig.blackbox.export.write_bundle", return_value=zip_path),
            patch("navig.vault.core.get_vault", side_effect=Exception("vault error"), create=True),
        ):
            result = export_bundle(bundle, tmp_path / "out.navbox", encrypted=True)

        # plaintext zip returned, still exists
        assert result == zip_path
        assert zip_path.exists()


# ---------------------------------------------------------------------------
# navig.memory.sync — _as_chunk, import_chunks
# ---------------------------------------------------------------------------

class TestAsChunk:
    def _call(self, item, default_file="file.py"):
        from navig.memory.sync import _as_chunk
        return _as_chunk(item, default_file)

    def test_valid_item_returns_memory_chunk(self):
        from navig.memory.storage import MemoryChunk
        chunk = self._call({"content": "def foo(): pass", "id": "c1"})
        assert isinstance(chunk, MemoryChunk)

    def test_content_propagated(self):
        chunk = self._call({"content": "hello world", "id": "x"})
        assert chunk.content == "hello world"

    def test_no_content_returns_none(self):
        assert self._call({"id": "x"}) is None

    def test_blank_content_returns_none(self):
        assert self._call({"content": "   "}) is None

    def test_id_from_item(self):
        chunk = self._call({"content": "abc", "id": "myid"})
        assert chunk.id == "myid"

    def test_chunk_id_fallback(self):
        chunk = self._call({"content": "abc", "chunk_id": "cid99"})
        assert chunk.id == "cid99"

    def test_auto_generated_id_when_missing(self):
        chunk = self._call({"content": "x" * 10}, default_file="foo.py")
        assert chunk.id.startswith("sync::")

    def test_file_path_from_item(self):
        chunk = self._call({"content": "x", "file_path": "/src/main.py"})
        assert chunk.file_path == "/src/main.py"

    def test_file_path_defaults_to_default_file(self):
        chunk = self._call({"content": "x"}, default_file="default.py")
        assert "default.py" in chunk.file_path or chunk.file_path == "default.py"

    def test_metadata_string_parsed_as_json(self):
        chunk = self._call({"content": "x", "metadata": '{"key": "val"}'})
        assert chunk.metadata == {"key": "val"}

    def test_metadata_bad_json_wrapped(self):
        chunk = self._call({"content": "x", "metadata": "not-json!!!"})
        assert chunk.metadata == {"raw_metadata": "not-json!!!"}

    def test_metadata_not_dict_becomes_empty(self):
        chunk = self._call({"content": "x", "metadata": 42})
        assert chunk.metadata == {}

    def test_line_start_default_1(self):
        chunk = self._call({"content": "x"})
        assert chunk.line_start == 1

    def test_line_end_default_1(self):
        chunk = self._call({"content": "x"})
        assert chunk.line_end == 1

    def test_token_count_default_0(self):
        chunk = self._call({"content": "x"})
        assert chunk.token_count == 0


class TestImportChunks:
    def test_returns_two_tuple(self, tmp_path):
        from navig.memory.sync import import_chunks
        with patch("navig.memory.sync.MemoryStorage") as MockStorage:
            MockStorage.return_value.upsert_chunks.return_value = 2
            result = import_chunks(tmp_path / "db.sqlite", [
                {"content": "a"}, {"content": "b"}
            ])
        assert isinstance(result, tuple)
        assert len(result) == 2

    def test_valid_chunks_counted(self, tmp_path):
        from navig.memory.sync import import_chunks
        with patch("navig.memory.sync.MemoryStorage") as MockStorage:
            MockStorage.return_value.upsert_chunks.return_value = 3
            imported, skipped = import_chunks(tmp_path / "db.sqlite", [
                {"content": "a"}, {"content": "b"}, {"content": "c"}
            ])
        assert imported == 3
        assert skipped == 0

    def test_non_dict_items_are_skipped(self, tmp_path):
        from navig.memory.sync import import_chunks
        with patch("navig.memory.sync.MemoryStorage") as MockStorage:
            MockStorage.return_value.upsert_chunks.return_value = 0
            imported, skipped = import_chunks(tmp_path / "db.sqlite", [
                "not-a-dict", 42, None
            ])
        assert skipped == 3

    def test_blank_content_items_are_skipped(self, tmp_path):
        from navig.memory.sync import import_chunks
        with patch("navig.memory.sync.MemoryStorage") as MockStorage:
            MockStorage.return_value.upsert_chunks.return_value = 0
            imported, skipped = import_chunks(tmp_path / "db.sqlite", [
                {"content": "   "}
            ])
        assert skipped == 1

    def test_formation_used_in_default_file(self, tmp_path):
        from navig.memory.sync import import_chunks
        captured_chunks = []

        def fake_upsert(chunks):
            captured_chunks.extend(chunks)
            return len(chunks)

        with patch("navig.memory.sync.MemoryStorage") as MockStorage:
            MockStorage.return_value.upsert_chunks.side_effect = fake_upsert
            import_chunks(
                tmp_path / "db.sqlite",
                [{"content": "x"}],
                formation="myformation",
            )

        assert any("myformation" in c.file_path for c in captured_chunks)

    def test_mixed_valid_and_invalid(self, tmp_path):
        from navig.memory.sync import import_chunks
        with patch("navig.memory.sync.MemoryStorage") as MockStorage:
            MockStorage.return_value.upsert_chunks.return_value = 1
            imported, skipped = import_chunks(tmp_path / "db.sqlite", [
                {"content": "valid"}, "bad", {"content": ""}
            ])
        assert imported == 1
        assert skipped == 2
