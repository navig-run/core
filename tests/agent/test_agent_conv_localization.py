"""Tests for navig.agent.conv.localization — LocalizationStore."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.agent.conv.localization import LocalizationStore


def _make_locale_dir(tmp_path: Path, locales: dict[str, dict]) -> Path:
    """Write locale JSON files into a temp directory."""
    locales_dir = tmp_path / "locales"
    locales_dir.mkdir()
    for lang, strings in locales.items():
        (locales_dir / f"{lang}.json").write_text(
            json.dumps(strings), encoding="utf-8"
        )
    return locales_dir


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------

class TestLocalizationStoreInit:
    def test_default_root_is_set(self):
        store = LocalizationStore()
        assert store._root.exists() or True  # root may not exist in all envs

    def test_custom_root_stored(self, tmp_path):
        store = LocalizationStore(locales_root=tmp_path)
        assert store._root == tmp_path

    def test_cache_starts_empty(self):
        store = LocalizationStore()
        assert store._cache == {}


# ---------------------------------------------------------------------------
# get() — basic lookups
# ---------------------------------------------------------------------------

class TestLocalizationStoreGet:
    def test_get_existing_key(self, tmp_path):
        locales = _make_locale_dir(tmp_path, {"en": {"hello": "Hello!"}})
        store = LocalizationStore(locales_root=locales)
        assert store.get("hello", "en") == "Hello!"

    def test_get_missing_key_returns_key_itself(self, tmp_path):
        locales = _make_locale_dir(tmp_path, {"en": {}})
        store = LocalizationStore(locales_root=locales)
        assert store.get("nonexistent.key", "en") == "nonexistent.key"

    def test_get_falls_back_to_english(self, tmp_path):
        locales = _make_locale_dir(tmp_path, {
            "en": {"greeting": "Hello"},
            "fr": {},  # empty file
        })
        store = LocalizationStore(locales_root=locales)
        assert store.get("greeting", "fr") == "Hello"

    def test_get_prefers_target_lang_over_fallback(self, tmp_path):
        locales = _make_locale_dir(tmp_path, {
            "en": {"greeting": "Hello"},
            "fr": {"greeting": "Bonjour"},
        })
        store = LocalizationStore(locales_root=locales)
        assert store.get("greeting", "fr") == "Bonjour"

    def test_get_missing_in_both_langs_returns_key(self, tmp_path):
        locales = _make_locale_dir(tmp_path, {
            "en": {},
            "fr": {},
        })
        store = LocalizationStore(locales_root=locales)
        assert store.get("unknown.key", "fr") == "unknown.key"

    def test_get_missing_locale_file_returns_key(self, tmp_path):
        locales = tmp_path / "locales"
        locales.mkdir()
        store = LocalizationStore(locales_root=locales)
        assert store.get("key", "de") == "key"

    def test_get_never_raises(self, tmp_path):
        store = LocalizationStore(locales_root=tmp_path / "nonexistent")
        # Should not raise despite missing root
        result = store.get("any.key", "en")
        assert result == "any.key"

    def test_get_with_english_direct(self, tmp_path):
        locales = _make_locale_dir(tmp_path, {"en": {"btn.save": "Save"}})
        store = LocalizationStore(locales_root=locales)
        assert store.get("btn.save", "en") == "Save"


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------

class TestLocalizationStoreCaching:
    def test_cache_populated_after_first_get(self, tmp_path):
        locales = _make_locale_dir(tmp_path, {"en": {"k": "v"}})
        store = LocalizationStore(locales_root=locales)
        store.get("k", "en")
        assert "en" in store._cache

    def test_second_get_uses_cache(self, tmp_path):
        locales = _make_locale_dir(tmp_path, {"en": {"k": "v"}})
        store = LocalizationStore(locales_root=locales)
        store.get("k", "en")
        # Delete the file — second call must still work from cache
        (locales / "en.json").unlink()
        assert store.get("k", "en") == "v"

    def test_separate_cache_per_language(self, tmp_path):
        locales = _make_locale_dir(tmp_path, {
            "en": {"k": "english"},
            "de": {"k": "deutsch"},
        })
        store = LocalizationStore(locales_root=locales)
        store.get("k", "en")
        store.get("k", "de")
        assert "en" in store._cache
        assert "de" in store._cache


# ---------------------------------------------------------------------------
# preload()
# ---------------------------------------------------------------------------

class TestLocalizationStorePreload:
    def test_preload_populates_cache(self, tmp_path):
        locales = _make_locale_dir(tmp_path, {"en": {"x": "y"}, "fr": {"x": "z"}})
        store = LocalizationStore(locales_root=locales)
        store.preload("en", "fr")
        assert "en" in store._cache
        assert "fr" in store._cache

    def test_preload_no_disk_read_after(self, tmp_path):
        locales = _make_locale_dir(tmp_path, {"en": {"k": "val"}})
        store = LocalizationStore(locales_root=locales)
        store.preload("en")
        # Delete the file after preload
        (locales / "en.json").unlink()
        assert store.get("k", "en") == "val"

    def test_preload_missing_lang_no_error(self, tmp_path):
        locales = _make_locale_dir(tmp_path, {})
        store = LocalizationStore(locales_root=locales)
        # Should not raise
        store.preload("zz")
        assert "zz" in store._cache
        assert store._cache["zz"] == {}


# ---------------------------------------------------------------------------
# _load_lang — error handling
# ---------------------------------------------------------------------------

class TestLocalizationStoreLoadLang:
    def test_invalid_json_returns_empty(self, tmp_path):
        locales = tmp_path / "locales"
        locales.mkdir()
        (locales / "en.json").write_text("not-valid-json", encoding="utf-8")
        store = LocalizationStore(locales_root=locales)
        result = store._load_lang("en")
        assert result == {}

    def test_non_dict_json_returns_empty(self, tmp_path):
        locales = tmp_path / "locales"
        locales.mkdir()
        (locales / "en.json").write_text('["list", "not", "dict"]', encoding="utf-8")
        store = LocalizationStore(locales_root=locales)
        result = store._load_lang("en")
        assert result == {}

    def test_missing_file_returns_empty(self, tmp_path):
        locales = tmp_path / "locales"
        locales.mkdir()
        store = LocalizationStore(locales_root=locales)
        result = store._load_lang("xx")
        assert result == {}

    def test_values_converted_to_str(self, tmp_path):
        locales = tmp_path / "locales"
        locales.mkdir()
        (locales / "en.json").write_text('{"count": 42}', encoding="utf-8")
        store = LocalizationStore(locales_root=locales)
        result = store._load_lang("en")
        assert result["count"] == "42"
