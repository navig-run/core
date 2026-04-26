"""Hermetic unit tests for navig.personas.loader."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# _read_soul_md
# ---------------------------------------------------------------------------


class TestReadSoulMd:
    def test_returns_content_when_soul_md_exists(self, tmp_path):
        from navig.personas.loader import _read_soul_md

        soul_file = tmp_path / "soul.md"
        soul_file.write_text("You are NAVIG.", encoding="utf-8")
        assert _read_soul_md(tmp_path) == "You are NAVIG."

    def test_returns_empty_string_when_missing(self, tmp_path):
        from navig.personas.loader import _read_soul_md

        assert _read_soul_md(tmp_path) == ""

    def test_strips_whitespace(self, tmp_path):
        from navig.personas.loader import _read_soul_md

        soul_file = tmp_path / "soul.md"
        soul_file.write_text("  soul content \n\n", encoding="utf-8")
        assert _read_soul_md(tmp_path) == "soul content"


# ---------------------------------------------------------------------------
# _read_persona_yaml
# ---------------------------------------------------------------------------


class TestReadPersonaYaml:
    def test_returns_empty_dict_when_missing(self, tmp_path):
        from navig.personas.loader import _read_persona_yaml

        assert _read_persona_yaml(tmp_path) == {}

    def test_returns_dict_when_valid_yaml(self, tmp_path):
        from navig.personas.loader import _read_persona_yaml

        yaml_file = tmp_path / "persona.yaml"
        yaml_file.write_text("display_name: Nero\ntone: formal\n", encoding="utf-8")
        data = _read_persona_yaml(tmp_path)
        assert data["display_name"] == "Nero"
        assert data["tone"] == "formal"

    def test_raises_value_error_for_invalid_yaml(self, tmp_path):
        from navig.personas.loader import _read_persona_yaml

        yaml_file = tmp_path / "persona.yaml"
        yaml_file.write_text("key: [unclosed bracket", encoding="utf-8")
        with pytest.raises(ValueError, match="Invalid persona.yaml"):
            _read_persona_yaml(tmp_path)

    def test_returns_empty_dict_when_yaml_is_non_dict(self, tmp_path):
        from navig.personas.loader import _read_persona_yaml

        yaml_file = tmp_path / "persona.yaml"
        yaml_file.write_text("- item1\n- item2\n", encoding="utf-8")
        # YAML list → not a dict → returns {}
        assert _read_persona_yaml(tmp_path) == {}


# ---------------------------------------------------------------------------
# _load_raw_chain
# ---------------------------------------------------------------------------


class TestLoadRawChain:
    def test_returns_empty_for_unknown_name(self, tmp_path):
        from navig.personas.loader import _load_raw_chain

        with patch("navig.personas.loader.resolve_persona", return_value=None):
            data, soul = _load_raw_chain("nonexistent_persona")
        assert data == {}
        assert soul == ""

    def test_returns_empty_for_default_when_not_found(self):
        from navig.personas.loader import _load_raw_chain

        with patch("navig.personas.loader.resolve_persona", return_value=None):
            data, soul = _load_raw_chain("default")
        assert data == {}
        assert soul == ""

    def test_loads_simple_persona(self, tmp_path):
        from navig.personas.loader import _load_raw_chain

        (tmp_path / "persona.yaml").write_text("tone: calm\n", encoding="utf-8")
        (tmp_path / "soul.md").write_text("I am calm.", encoding="utf-8")

        with patch("navig.personas.loader.resolve_persona", return_value=tmp_path):
            data, soul = _load_raw_chain("calm_persona")
        assert data["tone"] == "calm"
        assert soul == "I am calm."

    def test_child_overrides_parent_on_chain(self, tmp_path):
        from navig.personas.loader import _load_raw_chain

        parent_dir = tmp_path / "parent"
        parent_dir.mkdir()
        (parent_dir / "persona.yaml").write_text(
            "tone: warm\ndisplay_name: Parent\n", encoding="utf-8"
        )
        (parent_dir / "soul.md").write_text("Parent soul.", encoding="utf-8")

        child_dir = tmp_path / "child"
        child_dir.mkdir()
        (child_dir / "persona.yaml").write_text(
            "tone: cool\nsoul_extends: parent\n", encoding="utf-8"
        )
        (child_dir / "soul.md").write_text("Child soul.", encoding="utf-8")

        def resolve_side_effect(name, cwd=None):
            if name == "child":
                return child_dir
            if name == "parent":
                return parent_dir
            return None

        with patch("navig.personas.loader.resolve_persona", side_effect=resolve_side_effect):
            data, soul = _load_raw_chain("child")

        assert data["tone"] == "cool"         # child overrides parent
        assert data["display_name"] == "Parent"  # inherited from parent
        assert soul == "Child soul."          # child soul wins when non-empty

    def test_child_inherits_parent_soul_when_empty(self, tmp_path):
        from navig.personas.loader import _load_raw_chain

        parent_dir = tmp_path / "p"
        parent_dir.mkdir()
        (parent_dir / "persona.yaml").write_text("tone: warm\n", encoding="utf-8")
        (parent_dir / "soul.md").write_text("Parent soul.", encoding="utf-8")

        child_dir = tmp_path / "c"
        child_dir.mkdir()
        (child_dir / "persona.yaml").write_text(
            "tone: cool\nsoul_extends: parent\n", encoding="utf-8"
        )
        # no soul.md in child directory

        def resolve_side_effect(name, cwd=None):
            if name == "child_no_soul":
                return child_dir
            if name == "parent":
                return parent_dir
            return None

        with patch("navig.personas.loader.resolve_persona", side_effect=resolve_side_effect):
            _, soul = _load_raw_chain("child_no_soul")

        assert soul == "Parent soul."  # inherits from parent

    def test_depth_limit_stops_recursion(self, tmp_path):
        """Chain deeper than _SOUL_EXTENDS_MAX_DEPTH should stop gracefully."""
        from navig.personas.loader import _load_raw_chain

        # Build a chain of 7 personas (> max depth of 5), all extending the next
        dirs = {}
        for i in range(7):
            d = tmp_path / f"p{i}"
            d.mkdir()
            next_name = f"p{i+1}" if i < 6 else ""
            yaml_content = f"tone: t{i}\n"
            if next_name:
                yaml_content += f"soul_extends: p{i+1}\n"
            (d / "persona.yaml").write_text(yaml_content, encoding="utf-8")
            dirs[f"p{i}"] = d

        def resolve_side_effect(name, cwd=None):
            return dirs.get(name)

        # Should not raise; depth limit cuts recursion
        with patch("navig.personas.loader.resolve_persona", side_effect=resolve_side_effect):
            data, soul = _load_raw_chain("p0")
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# load_persona
# ---------------------------------------------------------------------------


class TestLoadPersona:
    def test_raises_file_not_found_when_persona_missing(self):
        from navig.personas.loader import load_persona

        with patch("navig.personas.resolver.resolve_persona", return_value=None):
            with patch("navig.personas.loader.resolve_persona", return_value=None):
                with pytest.raises(FileNotFoundError, match="not found"):
                    load_persona("ghost_persona")

    def test_returns_persona_config_and_soul(self, tmp_path):
        from navig.personas.contracts import PersonaConfig
        from navig.personas.loader import load_persona

        (tmp_path / "persona.yaml").write_text(
            "display_name: Test\ntone: warm\n", encoding="utf-8"
        )
        (tmp_path / "soul.md").write_text("Test soul.", encoding="utf-8")

        # load_persona re-imports resolve_persona locally AND _load_raw_chain uses
        # the module-level binding — patch both locations.
        with patch("navig.personas.resolver.resolve_persona", return_value=tmp_path):
            with patch("navig.personas.loader.resolve_persona", return_value=tmp_path):
                config, soul = load_persona("test_persona")

        assert isinstance(config, PersonaConfig)
        assert config.display_name == "Test"
        assert soul == "Test soul."

    def test_returns_defaults_when_yaml_empty(self, tmp_path):
        from navig.personas.contracts import PersonaConfig
        from navig.personas.loader import load_persona

        # No persona.yaml → defaults applied by PersonaConfig.from_dict
        with patch("navig.personas.resolver.resolve_persona", return_value=tmp_path):
            with patch("navig.personas.loader.resolve_persona", return_value=tmp_path):
                config, soul = load_persona("minimal")

        assert isinstance(config, PersonaConfig)
        assert config.name == "minimal"
        assert soul == ""
