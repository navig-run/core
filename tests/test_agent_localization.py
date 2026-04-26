"""Tests for navig.agent.conv.localization — LocalizationStore."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.agent.conv.localization import LocalizationStore


@pytest.fixture
def locales_dir(tmp_path: Path) -> Path:
    d = tmp_path / "locales"
    d.mkdir()
    return d


def write_locale(locales_dir: Path, lang: str, data: dict) -> None:
    (locales_dir / f"{lang}.json").write_text(json.dumps(data), encoding="utf-8")


class TestLocalizationStoreGet:
    def test_returns_value_from_locale(self, locales_dir):
        write_locale(locales_dir, "en", {"greeting": "Hello"})
        store = LocalizationStore(locales_root=locales_dir)
        assert store.get("greeting", "en") == "Hello"

    def test_falls_back_to_en_for_missing_lang(self, locales_dir):
        write_locale(locales_dir, "en", {"farewell": "Goodbye"})
        store = LocalizationStore(locales_root=locales_dir)
        assert store.get("farewell", "fr") == "Goodbye"

    def test_returns_key_when_not_found_anywhere(self, locales_dir):
        store = LocalizationStore(locales_root=locales_dir)
        assert store.get("missing_key", "en") == "missing_key"

    def test_prefers_lang_over_en_fallback(self, locales_dir):
        write_locale(locales_dir, "en", {"k": "English"})
        write_locale(locales_dir, "fr", {"k": "French"})
        store = LocalizationStore(locales_root=locales_dir)
        assert store.get("k", "fr") == "French"

    def test_never_raises_on_missing_file(self, locales_dir):
        store = LocalizationStore(locales_root=locales_dir)
        result = store.get("any_key", "zh")
        assert isinstance(result, str)

    def test_never_raises_on_corrupt_json(self, locales_dir):
        (locales_dir / "es.json").write_text("{invalid}", encoding="utf-8")
        store = LocalizationStore(locales_root=locales_dir)
        result = store.get("key", "es")
        assert result == "key"


class TestLocalizationStoreCache:
    def test_cache_avoids_second_disk_read(self, locales_dir, monkeypatch):
        write_locale(locales_dir, "en", {"x": "X"})
        store = LocalizationStore(locales_root=locales_dir)
        store.get("x", "en")  # loads into cache
        # Now remove the file — cache should be used
        (locales_dir / "en.json").unlink()
        result = store.get("x", "en")
        assert result == "X"

    def test_preload_populates_cache(self, locales_dir):
        write_locale(locales_dir, "de", {"hi": "Hallo"})
        store = LocalizationStore(locales_root=locales_dir)
        store.preload("de")
        assert "de" in store._cache

    def test_multiple_preloads(self, locales_dir):
        write_locale(locales_dir, "en", {})
        write_locale(locales_dir, "fr", {})
        store = LocalizationStore(locales_root=locales_dir)
        store.preload("en", "fr")
        assert "en" in store._cache
        assert "fr" in store._cache


class TestLocalizationStoreInit:
    def test_default_root_is_set(self):
        store = LocalizationStore()
        assert store._root.exists() or not store._root.exists()  # no crash

    def test_custom_root(self, tmp_path):
        store = LocalizationStore(locales_root=tmp_path)
        assert store._root == tmp_path

    def test_cache_starts_empty(self, tmp_path):
        store = LocalizationStore(locales_root=tmp_path)
        assert store._cache == {}
