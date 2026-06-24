"""
Tests for:
  - navig.adapters.automation.evolution.library  (ScriptEntry, ScriptLibrary)

All tests are hermetic — filesystem I/O uses tmp_path.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

import pytest


# ===========================================================================
# ScriptEntry dataclass
# ===========================================================================

class TestScriptEntry:
    def _entry(self, **kwargs):
        from navig.adapters.automation.evolution.library import ScriptEntry

        defaults = {
            "id": "abc12345",
            "goal": "open notepad",
            "script": "Run, notepad.exe",
            "created_at": "2024-01-01T00:00:00",
        }
        defaults.update(kwargs)
        return ScriptEntry(**defaults)

    def test_minimal_construction(self):
        entry = self._entry()
        assert entry.id == "abc12345"
        assert entry.goal == "open notepad"
        assert entry.script == "Run, notepad.exe"

    def test_defaults(self):
        entry = self._entry()
        assert entry.success_count == 0
        assert entry.last_used == ""
        assert entry.tags == []

    def test_custom_tags(self):
        entry = self._entry(tags=["automation", "notepad"])
        assert entry.tags == ["automation", "notepad"]

    def test_to_dict_returns_dict(self):
        entry = self._entry()
        d = entry.to_dict()
        assert isinstance(d, dict)

    def test_to_dict_includes_all_fields(self):
        entry = self._entry(success_count=3, tags=["x"])
        d = entry.to_dict()
        assert d["id"] == "abc12345"
        assert d["goal"] == "open notepad"
        assert d["script"] == "Run, notepad.exe"
        assert d["success_count"] == 3
        assert d["tags"] == ["x"]

    def test_to_dict_roundtrip(self):
        from navig.adapters.automation.evolution.library import ScriptEntry

        entry = self._entry()
        d = entry.to_dict()
        restored = ScriptEntry(**d)
        assert restored.id == entry.id
        assert restored.goal == entry.goal


# ===========================================================================
# ScriptLibrary._goal_id
# ===========================================================================

class TestGoalId:
    def test_deterministic_for_same_goal(self):
        from navig.adapters.automation.evolution.library import ScriptLibrary

        id1 = ScriptLibrary._goal_id("open notepad")
        id2 = ScriptLibrary._goal_id("open notepad")
        assert id1 == id2

    def test_case_insensitive(self):
        from navig.adapters.automation.evolution.library import ScriptLibrary

        assert ScriptLibrary._goal_id("Open Notepad") == ScriptLibrary._goal_id("open notepad")

    def test_different_goals_different_ids(self):
        from navig.adapters.automation.evolution.library import ScriptLibrary

        assert ScriptLibrary._goal_id("open notepad") != ScriptLibrary._goal_id("open calc")

    def test_returns_8_char_hex(self):
        from navig.adapters.automation.evolution.library import ScriptLibrary

        result = ScriptLibrary._goal_id("any goal")
        assert len(result) == 8
        assert all(c in "0123456789abcdef" for c in result)

    def test_matches_md5_prefix(self):
        from navig.adapters.automation.evolution.library import ScriptLibrary

        goal = "test goal"
        expected = hashlib.md5(goal.lower().encode()).hexdigest()[:8]
        assert ScriptLibrary._goal_id(goal) == expected


# ===========================================================================
# ScriptLibrary full lifecycle
# ===========================================================================

class TestScriptLibrary:
    def _lib(self, tmp_path):
        from navig.adapters.automation.evolution.library import ScriptLibrary

        return ScriptLibrary(storage_dir=tmp_path / "ahk_lib")

    def test_init_creates_dirs(self, tmp_path):
        lib = self._lib(tmp_path)
        assert lib.storage_dir.is_dir()
        assert (lib.storage_dir / "scripts").is_dir()

    def test_save_and_find_script(self, tmp_path):
        lib = self._lib(tmp_path)
        script_id = lib.save_script("open notepad", "Run, notepad.exe")
        entry = lib.find_script("open notepad")
        assert entry is not None
        assert entry.script == "Run, notepad.exe"
        assert entry.id == script_id

    def test_find_missing_returns_none(self, tmp_path):
        lib = self._lib(tmp_path)
        result = lib.find_script("something not saved")
        assert result is None

    def test_save_persists_script_file(self, tmp_path):
        lib = self._lib(tmp_path)
        lib.save_script("my goal", "MsgBox, hello", tags=["test"])
        script_id = lib._goal_id("my goal")
        script_file = lib.storage_dir / "scripts" / f"{script_id}.ahk"
        assert script_file.exists()
        assert script_file.read_text(encoding="utf-8") == "MsgBox, hello"

    def test_save_persists_index(self, tmp_path):
        lib = self._lib(tmp_path)
        lib.save_script("persist test", "Run, cmd.exe")
        assert lib.index_file.exists()
        data = json.loads(lib.index_file.read_text(encoding="utf-8"))
        assert len(data) == 1

    def test_overwrite_same_goal(self, tmp_path):
        lib = self._lib(tmp_path)
        lib.save_script("my goal", "old script")
        lib.save_script("my goal", "new script")
        entry = lib.find_script("my goal")
        assert entry.script == "new script"

    def test_list_scripts_empty(self, tmp_path):
        lib = self._lib(tmp_path)
        assert lib.list_scripts() == []

    def test_list_scripts_returns_all(self, tmp_path):
        lib = self._lib(tmp_path)
        lib.save_script("goal one", "script one")
        lib.save_script("goal two", "script two")
        entries = lib.list_scripts()
        assert len(entries) == 2

    def test_record_usage_increments_success(self, tmp_path):
        lib = self._lib(tmp_path)
        lib.save_script("click btn", "Click, 100, 200")
        script_id = lib._goal_id("click btn")
        lib.record_usage(script_id, success=True)
        entry = lib.find_script("click btn")
        assert entry.success_count == 1

    def test_record_usage_success_false_no_increment(self, tmp_path):
        lib = self._lib(tmp_path)
        lib.save_script("click btn", "Click, 100, 200")
        script_id = lib._goal_id("click btn")
        lib.record_usage(script_id, success=False)
        entry = lib.find_script("click btn")
        assert entry.success_count == 0

    def test_record_usage_missing_id_no_error(self, tmp_path):
        lib = self._lib(tmp_path)
        # Should not raise
        lib.record_usage("non_existent_id", success=True)

    def test_reload_from_disk(self, tmp_path):
        """Index is preserved across separate ScriptLibrary instances."""
        from navig.adapters.automation.evolution.library import ScriptLibrary

        storage = tmp_path / "ahk_lib"
        lib1 = ScriptLibrary(storage_dir=storage)
        lib1.save_script("persistent goal", "Run, calc.exe")

        # Create new instance pointing to same directory
        lib2 = ScriptLibrary(storage_dir=storage)
        entry = lib2.find_script("persistent goal")
        assert entry is not None
        assert entry.script == "Run, calc.exe"

    def test_corrupt_index_starts_fresh(self, tmp_path):
        from navig.adapters.automation.evolution.library import ScriptLibrary

        storage = tmp_path / "ahk_lib"
        storage.mkdir(parents=True)
        (storage / "scripts").mkdir()
        # Write garbage index
        (storage / "index.json").write_text("NOT VALID JSON!!!", encoding="utf-8")

        lib = ScriptLibrary(storage_dir=storage)
        # Should not raise and should start with empty index
        assert lib.list_scripts() == []

    def test_tags_saved_and_retrieved(self, tmp_path):
        lib = self._lib(tmp_path)
        lib.save_script("tagged goal", "some script", tags=["automation", "browser"])
        entry = lib.find_script("tagged goal")
        assert "automation" in entry.tags
        assert "browser" in entry.tags
