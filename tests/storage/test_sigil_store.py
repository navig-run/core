"""Tests for navig.identity.sigil_store — persist_entity, load_entity, etc."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import navig.identity.sigil_store as store_mod
from navig.identity.sigil_store import (
    entity_exists,
    get_seed_for_session,
    load_entity,
    persist_entity,
    reset_entity,
)


def _mock_entity(seed="abc123", name="Navi", archetype="sage",
                 palette_key="deep_blue", resonance=0.85) -> MagicMock:
    e = MagicMock()
    e.seed = seed
    e.name = name
    e.archetype = archetype
    e.palette_key = palette_key
    e.resonance = resonance
    return e


class TestPersistEntity:
    def test_creates_file(self, tmp_path) -> None:
        entity_path = tmp_path / "entity.json"
        with patch.object(store_mod, "_entity_json_path", return_value=entity_path):
            persist_entity(_mock_entity())
        assert entity_path.exists()

    def test_file_contains_seed(self, tmp_path) -> None:
        entity_path = tmp_path / "entity.json"
        with patch.object(store_mod, "_entity_json_path", return_value=entity_path):
            persist_entity(_mock_entity(seed="myuniqueseed"))
        assert "myuniqueseed" in entity_path.read_text(encoding="utf-8")

    def test_creates_parent_dirs(self, tmp_path) -> None:
        entity_path = tmp_path / "state" / "entity.json"
        with patch.object(store_mod, "_entity_json_path", return_value=entity_path):
            persist_entity(_mock_entity())
        assert entity_path.parent.exists()

    def test_is_valid_json(self, tmp_path) -> None:
        import json
        entity_path = tmp_path / "entity.json"
        with patch.object(store_mod, "_entity_json_path", return_value=entity_path):
            persist_entity(_mock_entity())
        data = json.loads(entity_path.read_text(encoding="utf-8"))
        assert "seed" in data


class TestLoadEntity:
    def test_returns_none_when_missing(self, tmp_path) -> None:
        entity_path = tmp_path / "entity.json"
        with patch.object(store_mod, "_entity_json_path", return_value=entity_path):
            assert load_entity() is None

    def test_returns_dict_on_valid_file(self, tmp_path) -> None:
        import json
        entity_path = tmp_path / "entity.json"
        entity_path.write_text(json.dumps({"seed": "ab", "version": 1}), encoding="utf-8")
        with patch.object(store_mod, "_entity_json_path", return_value=entity_path):
            result = load_entity()
        assert isinstance(result, dict)
        assert result["seed"] == "ab"

    def test_returns_none_on_empty_file(self, tmp_path) -> None:
        entity_path = tmp_path / "entity.json"
        entity_path.write_text("", encoding="utf-8")
        with patch.object(store_mod, "_entity_json_path", return_value=entity_path):
            assert load_entity() is None

    def test_returns_none_on_corrupt_json(self, tmp_path) -> None:
        entity_path = tmp_path / "entity.json"
        entity_path.write_text("{not valid json!", encoding="utf-8")
        with patch.object(store_mod, "_entity_json_path", return_value=entity_path):
            assert load_entity() is None

    def test_returns_none_if_no_seed_key(self, tmp_path) -> None:
        import json
        entity_path = tmp_path / "entity.json"
        entity_path.write_text(json.dumps({"name": "x"}), encoding="utf-8")
        with patch.object(store_mod, "_entity_json_path", return_value=entity_path):
            assert load_entity() is None


class TestEntityExists:
    def test_false_when_no_file(self, tmp_path) -> None:
        entity_path = tmp_path / "entity.json"
        with patch.object(store_mod, "_entity_json_path", return_value=entity_path):
            assert entity_exists() is False

    def test_true_after_persist(self, tmp_path) -> None:
        entity_path = tmp_path / "entity.json"
        with patch.object(store_mod, "_entity_json_path", return_value=entity_path):
            persist_entity(_mock_entity())
            assert entity_exists() is True


class TestResetEntity:
    def test_deletes_file(self, tmp_path) -> None:
        entity_path = tmp_path / "entity.json"
        entity_path.write_text("{}", encoding="utf-8")
        with patch.object(store_mod, "_entity_json_path", return_value=entity_path):
            reset_entity()
        assert not entity_path.exists()

    def test_no_error_when_already_missing(self, tmp_path) -> None:
        entity_path = tmp_path / "entity.json"
        with patch.object(store_mod, "_entity_json_path", return_value=entity_path):
            reset_entity()  # must not raise


class TestGetSeedForSession:
    def test_demo_mode_returns_default_fallback(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            seed = get_seed_for_session(demo=True)
        assert seed == "deadbeef" * 8

    def test_demo_mode_uses_env_var(self) -> None:
        with patch.dict("os.environ", {"NAVIG_DEMO_SEED": "custom_seed_xyz"}):
            seed = get_seed_for_session(demo=True)
        assert seed == "custom_seed_xyz"

    def test_normal_mode_returns_string(self) -> None:
        mock_seed = "a" * 64
        with patch("navig.identity.seed.generate_seed", return_value=mock_seed):
            seed = get_seed_for_session(demo=False)
        assert seed == mock_seed
