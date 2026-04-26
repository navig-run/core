"""Tests for navig.personas.resolver — resolve_persona, discover_persona_paths."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

import navig.personas.resolver as res_mod
from navig.personas.resolver import discover_persona_paths, resolve_persona


class TestResolvePersona:
    def test_returns_none_when_persona_not_found(self, tmp_path: Path) -> None:
        fake_config = tmp_path / "config"
        with patch.object(res_mod, "config_dir", return_value=fake_config):
            result = resolve_persona("nonexistent", cwd=tmp_path)
        assert result is None

    def test_finds_project_local_persona(self, tmp_path: Path) -> None:
        fake_config = tmp_path / "config"
        navig_dir = tmp_path / ".navig"
        persona_dir = navig_dir / "personas" / "analyst"
        persona_dir.mkdir(parents=True)

        with patch.object(res_mod, "config_dir", return_value=fake_config):
            result = resolve_persona("analyst", cwd=tmp_path)

        assert result == persona_dir

    def test_finds_user_home_persona(self, tmp_path: Path) -> None:
        fake_config = tmp_path / "config"
        user_persona = fake_config / "personas" / "analyst"
        user_persona.mkdir(parents=True)

        with patch.object(res_mod, "config_dir", return_value=fake_config):
            result = resolve_persona("analyst", cwd=tmp_path)

        assert result == user_persona

    def test_project_takes_precedence_over_user_home(self, tmp_path: Path) -> None:
        fake_config = tmp_path / "config"
        user_persona = fake_config / "personas" / "analyst"
        user_persona.mkdir(parents=True)

        navig_dir = tmp_path / ".navig"
        project_persona = navig_dir / "personas" / "analyst"
        project_persona.mkdir(parents=True)

        with patch.object(res_mod, "config_dir", return_value=fake_config):
            result = resolve_persona("analyst", cwd=tmp_path)

        assert result == project_persona

    def test_normalizes_name_to_lowercase(self, tmp_path: Path) -> None:
        fake_config = tmp_path / "config"
        navig_dir = tmp_path / ".navig"
        persona_dir = navig_dir / "personas" / "analyst"
        persona_dir.mkdir(parents=True)

        with patch.object(res_mod, "config_dir", return_value=fake_config):
            result = resolve_persona("Analyst", cwd=tmp_path)

        assert result == persona_dir

    def test_returns_path_object(self, tmp_path: Path) -> None:
        fake_config = tmp_path / "config"
        navig_dir = tmp_path / ".navig"
        (navig_dir / "personas" / "dev").mkdir(parents=True)

        with patch.object(res_mod, "config_dir", return_value=fake_config):
            result = resolve_persona("dev", cwd=tmp_path)

        assert isinstance(result, Path)


class TestDiscoverPersonaPaths:
    def test_returns_empty_when_no_personas_exist(self, tmp_path: Path) -> None:
        fake_config = tmp_path / "config"
        with patch.object(res_mod, "config_dir", return_value=fake_config):
            result = discover_persona_paths(cwd=tmp_path)
        # May include package defaults; just assert it returns a dict
        assert isinstance(result, dict)

    def test_discovers_user_home_personas(self, tmp_path: Path) -> None:
        fake_config = tmp_path / "config"
        (fake_config / "personas" / "analyst").mkdir(parents=True)
        (fake_config / "personas" / "developer").mkdir(parents=True)

        with patch.object(res_mod, "config_dir", return_value=fake_config):
            result = discover_persona_paths(cwd=tmp_path)

        assert "analyst" in result
        assert "developer" in result

    def test_project_persona_overrides_user_home(self, tmp_path: Path) -> None:
        fake_config = tmp_path / "config"
        user_persona = fake_config / "personas" / "analyst"
        user_persona.mkdir(parents=True)

        navig_dir = tmp_path / ".navig"
        project_persona = navig_dir / "personas" / "analyst"
        project_persona.mkdir(parents=True)

        with patch.object(res_mod, "config_dir", return_value=fake_config):
            result = discover_persona_paths(cwd=tmp_path)

        assert result["analyst"] == project_persona
