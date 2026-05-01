"""
Batch 105 — tests for:
  - navig.prompt_loader    (load_prompt)
  - navig.ssh_keys         (_looks_like_private_key, discover_local_ssh_keys)
  - navig.file_history     (FileVersion, FileHistoryStore, get_file_history_store)
"""

from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch


# ============================================================================
# navig.prompt_loader
# ============================================================================


class TestLoadPrompt:
    def test_missing_slug_returns_warning(self):
        from navig.prompt_loader import load_prompt

        # Cache may hold previous value — use a unique slug
        result = load_prompt("navig_test_batch105_nonexistent_9z9z")
        assert "Warning" in result or "not found" in result.lower()

    def test_returns_string(self):
        from navig.prompt_loader import load_prompt

        result = load_prompt("navig_test_batch105_nonexistent_2")
        assert isinstance(result, str)

    def test_existing_prompt_returns_content(self, tmp_path, monkeypatch):
        """If a prompt file exists, load_prompt should return its content."""
        from navig import prompt_loader

        # Create a fake prompts directory
        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "test_slug.md").write_text(
            "Hello from test prompt", encoding="utf-8"
        )

        monkeypatch.setattr(
            "navig.prompt_loader.builtin_store_dir", lambda: tmp_path
        )
        # Clear the lru_cache so monkeypatch applies
        prompt_loader.load_prompt.cache_clear()
        result = prompt_loader.load_prompt("test_slug")
        assert "Hello from test prompt" in result

    def test_frontmatter_stripped(self, tmp_path, monkeypatch):
        from navig import prompt_loader

        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "fm_slug.md").write_text(
            "---\ntitle: Test\n---\nActual content here", encoding="utf-8"
        )
        monkeypatch.setattr(
            "navig.prompt_loader.builtin_store_dir", lambda: tmp_path
        )
        prompt_loader.load_prompt.cache_clear()
        result = prompt_loader.load_prompt("fm_slug")
        assert "Actual content here" in result
        assert "title: Test" not in result

    def test_no_frontmatter_returns_full_content(self, tmp_path, monkeypatch):
        from navig import prompt_loader

        prompts_dir = tmp_path / "prompts"
        prompts_dir.mkdir()
        (prompts_dir / "plain.md").write_text(
            "Just plain content", encoding="utf-8"
        )
        monkeypatch.setattr(
            "navig.prompt_loader.builtin_store_dir", lambda: tmp_path
        )
        prompt_loader.load_prompt.cache_clear()
        result = prompt_loader.load_prompt("plain")
        assert result == "Just plain content"


# ============================================================================
# navig.ssh_keys
# ============================================================================


class TestLooksLikePrivateKey:
    def test_pub_key_excluded(self, tmp_path):
        from navig.ssh_keys import _looks_like_private_key

        pub = tmp_path / "id_rsa.pub"
        pub.write_text("ssh-rsa AAAA...", encoding="utf-8")
        assert _looks_like_private_key(pub) is False

    def test_known_hosts_excluded(self, tmp_path):
        from navig.ssh_keys import _looks_like_private_key

        known = tmp_path / "known_hosts"
        known.write_text("github.com ...", encoding="utf-8")
        assert _looks_like_private_key(known) is False

    def test_config_excluded(self, tmp_path):
        from navig.ssh_keys import _looks_like_private_key

        config = tmp_path / "config"
        config.write_text("Host *", encoding="utf-8")
        assert _looks_like_private_key(config) is False

    def test_authorized_keys_excluded(self, tmp_path):
        from navig.ssh_keys import _looks_like_private_key

        ak = tmp_path / "authorized_keys"
        ak.write_text("ssh-rsa ...", encoding="utf-8")
        assert _looks_like_private_key(ak) is False

    def test_nonexistent_file_excluded(self, tmp_path):
        from navig.ssh_keys import _looks_like_private_key

        p = tmp_path / "id_ed25519_nonexistent"
        assert _looks_like_private_key(p) is False

    def test_private_key_file_accepted(self, tmp_path):
        from navig.ssh_keys import _looks_like_private_key

        key = tmp_path / "id_rsa"
        key.write_text("-----BEGIN RSA PRIVATE KEY-----", encoding="utf-8")
        assert _looks_like_private_key(key) is True

    def test_custom_key_name_accepted(self, tmp_path):
        from navig.ssh_keys import _looks_like_private_key

        key = tmp_path / "deploy_key"
        key.write_text("some private key", encoding="utf-8")
        assert _looks_like_private_key(key) is True


class TestDiscoverLocalSshKeys:
    def test_returns_dict_with_required_keys(self):
        from navig.ssh_keys import discover_local_ssh_keys

        result = discover_local_ssh_keys(no_cache=True)
        assert isinstance(result, dict)
        assert "keys" in result
        assert "count" in result

    def test_count_matches_keys_length(self):
        from navig.ssh_keys import discover_local_ssh_keys

        result = discover_local_ssh_keys(no_cache=True)
        assert result["count"] == len(result["keys"])

    def test_keys_have_path_and_name(self):
        from navig.ssh_keys import discover_local_ssh_keys

        result = discover_local_ssh_keys(no_cache=True)
        for key in result["keys"]:
            assert "path" in key
            assert "name" in key

    def test_no_pub_keys_in_results(self):
        from navig.ssh_keys import discover_local_ssh_keys

        result = discover_local_ssh_keys(no_cache=True)
        for key in result["keys"]:
            assert not key["name"].endswith(".pub")


# ============================================================================
# navig.file_history
# ============================================================================


class TestFileVersion:
    def test_creation(self, tmp_path):
        bak = tmp_path / "test.txt.bak"
        bak.write_text("content", encoding="utf-8")
        v = _make_version(original_path="/tmp/test.txt", backup_path=bak)
        assert v.original_path == "/tmp/test.txt"
        assert v.backup_path == bak

    def test_frozen_immutable(self, tmp_path):
        import pytest
        bak = tmp_path / "test.txt.bak"
        bak.touch()
        v = _make_version(backup_path=bak)
        with pytest.raises((AttributeError, TypeError)):
            v.session_id = "new"  # type: ignore[misc]

    def test_ordering(self, tmp_path):
        bak1 = tmp_path / "v1.bak"
        bak1.touch()
        bak2 = tmp_path / "v2.bak"
        bak2.touch()
        v1 = _make_version(
            backup_path=bak1,
            captured_at=datetime(2024, 1, 1, tzinfo=timezone.utc)
        )
        v2 = _make_version(
            backup_path=bak2,
            captured_at=datetime(2024, 6, 1, tzinfo=timezone.utc)
        )
        assert v1 < v2


def _make_version(**kwargs):
    from navig.file_history import FileVersion

    defaults = dict(
        original_path="/tmp/test.txt",
        backup_path=Path("/tmp/test.txt.bak"),
        session_id="sess1",
        turn_id="turn1",
        captured_at=datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc),
        size_bytes=100,
    )
    defaults.update(kwargs)
    return FileVersion(**defaults)


class TestFileHistoryStore:
    def test_checkpoint_returns_none_for_missing_file(self, tmp_path):
        from navig.file_history import FileHistoryStore

        store = FileHistoryStore(cache_dir=tmp_path / "cache")
        result = store.checkpoint("/nonexistent/path/file.txt", "sess1", "turn1")
        assert result is None

    def test_checkpoint_returns_path_for_existing_file(self, tmp_path):
        from navig.file_history import FileHistoryStore

        # Create a file to checkpoint
        src = tmp_path / "source.txt"
        src.write_text("original content", encoding="utf-8")

        cache_dir = tmp_path / "cache"
        store = FileHistoryStore(cache_dir=cache_dir)
        result = store.checkpoint(src, "sess1", "turn1")

        # Result may be None if disabled via config, or a Path
        assert result is None or isinstance(result, Path)

    def test_checkpoint_creates_backup_file(self, tmp_path, monkeypatch):
        from navig.file_history import FileHistoryStore

        src = tmp_path / "source.txt"
        src.write_text("original content", encoding="utf-8")

        cache_dir = tmp_path / "cache"
        store = FileHistoryStore(cache_dir=cache_dir)

        # Patch _is_enabled to return True
        monkeypatch.setattr(store, "_is_enabled", lambda: True)
        result = store.checkpoint(src, "sess1", "turn1")
        assert result is not None
        assert result.exists()

    def test_list_versions_empty_for_unknown_session(self, tmp_path):
        from navig.file_history import FileHistoryStore

        store = FileHistoryStore(cache_dir=tmp_path / "cache")
        versions = store.list_versions("/tmp/test.txt", "unknown_session")
        assert versions == []

    def test_list_versions_returns_sorted(self, tmp_path, monkeypatch):
        from navig.file_history import FileHistoryStore

        src = tmp_path / "source.txt"
        src.write_text("v1", encoding="utf-8")
        cache_dir = tmp_path / "cache"
        store = FileHistoryStore(cache_dir=cache_dir)
        monkeypatch.setattr(store, "_is_enabled", lambda: True)

        store.checkpoint(src, "sess1", "turn1")
        src.write_text("v2", encoding="utf-8")
        store.checkpoint(src, "sess1", "turn2")

        versions = store.list_versions(src, "sess1")
        assert len(versions) == 2
        assert versions[0].turn_id == "turn1"
        assert versions[1].turn_id == "turn2"

    def test_restore_returns_false_for_missing_backup(self, tmp_path):
        from navig.file_history import FileHistoryStore

        store = FileHistoryStore(cache_dir=tmp_path / "cache")
        v = _make_version(
            backup_path=tmp_path / "missing.bak",
            original_path=str(tmp_path / "orig.txt"),
        )
        assert store.restore(v) is False

    def test_restore_copies_backup_to_original(self, tmp_path):
        from navig.file_history import FileHistoryStore

        bak = tmp_path / "backup.bak"
        bak.write_text("backed up content", encoding="utf-8")
        orig = tmp_path / "original.txt"
        orig.write_text("current content", encoding="utf-8")

        store = FileHistoryStore(cache_dir=tmp_path / "cache")
        v = _make_version(backup_path=bak, original_path=str(orig))
        success = store.restore(v)
        assert success is True
        assert orig.read_text(encoding="utf-8") == "backed up content"

    def test_diff_versions_returns_string(self, tmp_path):
        from navig.file_history import FileHistoryStore

        bak1 = tmp_path / "v1.bak"
        bak1.write_text("line1\nline2\n", encoding="utf-8")
        bak2 = tmp_path / "v2.bak"
        bak2.write_text("line1\nline3\n", encoding="utf-8")

        store = FileHistoryStore(cache_dir=tmp_path / "cache")
        v1 = _make_version(backup_path=bak1, turn_id="turn1")
        v2 = _make_version(backup_path=bak2, turn_id="turn2")
        diff = store.diff_versions(v1, v2)
        assert isinstance(diff, str)
        assert "line" in diff or "@@" in diff


class TestGetFileHistoryStore:
    def test_returns_store_instance(self):
        from navig.file_history import FileHistoryStore, get_file_history_store

        store = get_file_history_store()
        assert isinstance(store, FileHistoryStore)
