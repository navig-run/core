"""Tests for navig.formations.registry — FormationRegistry singleton."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import navig.formations.registry as reg_mod
from navig.formations.registry import FormationRegistry, get_registry


def _reset():
    FormationRegistry._instance = None


class TestFormationRegistry:
    def setup_method(self):
        _reset()

    def test_get_instance_singleton(self):
        a = FormationRegistry.get_instance()
        b = FormationRegistry.get_instance()
        assert a is b

    def test_initial_state(self):
        r = FormationRegistry()
        assert r._active_formation is None
        assert r._formation_map == {}
        assert r._initialized is False

    def test_initialize_calls_discover(self):
        with patch("navig.formations.registry.discover_formations", return_value={}) as mock_disc, \
             patch("navig.formations.registry.get_active_formation", return_value=None):
            r = FormationRegistry()
            r.initialize()
        mock_disc.assert_called_once()

    def test_initialize_calls_get_active(self):
        with patch("navig.formations.registry.discover_formations", return_value={}), \
             patch("navig.formations.registry.get_active_formation", return_value=None) as mock_gaf:
            r = FormationRegistry()
            r.initialize(workspace_dir=Path("/tmp"))
        mock_gaf.assert_called_once_with(Path("/tmp"))

    def test_initialize_only_once(self):
        with patch("navig.formations.registry.discover_formations", return_value={}) as mock_disc, \
             patch("navig.formations.registry.get_active_formation", return_value=None):
            r = FormationRegistry()
            r.initialize()
            r.initialize()  # second call should be no-op
        assert mock_disc.call_count == 1

    def test_get_active_returns_active_formation(self):
        fake_formation = MagicMock(name="prod")
        with patch("navig.formations.registry.discover_formations", return_value={}), \
             patch("navig.formations.registry.get_active_formation", return_value=fake_formation):
            r = FormationRegistry()
            r.initialize()
        assert r.get_active() is fake_formation

    def test_get_formation_map_returns_map(self):
        fmap = {"prod": Path("/forms/prod")}
        with patch("navig.formations.registry.discover_formations", return_value=fmap), \
             patch("navig.formations.registry.get_active_formation", return_value=None):
            r = FormationRegistry()
            r.initialize()
        assert r.get_formation_map() == fmap

    def test_reload_reinitializes(self):
        with patch("navig.formations.registry.discover_formations", return_value={}) as mock_disc, \
             patch("navig.formations.registry.get_active_formation", return_value=None):
            r = FormationRegistry()
            r.initialize()
            r.reload()
        assert mock_disc.call_count == 2

    def test_get_registry_returns_instance(self):
        inst = get_registry()
        assert isinstance(inst, FormationRegistry)
        assert inst is FormationRegistry.get_instance()
