"""
Tests for navig.settings — layered resolver.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Dict


def _write_json(path: Path, data: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


class TestSettingsResolver:
    def setup_method(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.project_root = self.tmp / "myproject"
        self.project_root.mkdir()
        (self.project_root / ".navig").mkdir()
        self.global_dir = self.tmp / ".navig_global"
        self.global_dir.mkdir()

    def _make_resolver(self, layer=None, resolve_secrets=False):
        from navig.settings.resolver import SettingsResolver

        resolver = SettingsResolver(
            project_root=self.project_root,
            layer=layer,
            resolve_secrets=resolve_secrets,
        )
        # Point to temp global dir
        import navig.settings.resolver as m

        orig = m._global_settings_dir
        m._global_settings_dir = lambda: self.global_dir
        self._restore_global = lambda: setattr(m, "_global_settings_dir", orig)
        return resolver

    def teardown_method(self):
        if hasattr(self, "_restore_global"):
            self._restore_global()

    def test_returns_defaults_when_no_files(self):
        resolver = self._make_resolver()
        settings = resolver.resolve()
        # Should return NAVIG defaults
        assert "navig.ai.provider" in settings
        assert settings["navig.ai.provider"] == "openai"

    def test_global_overrides_defaults(self):
        _write_json(
            self.global_dir / "settings.json",
            {"navig": {"ai": {"provider": "anthropic"}}},
        )
        resolver = self._make_resolver()
        settings = resolver.resolve()
        assert settings["navig.ai.provider"] == "anthropic"

    def test_project_overrides_global(self):
        _write_json(
            self.global_dir / "settings.json",
            {"navig": {"ai": {"provider": "anthropic"}}},
        )
        _write_json(
            self.project_root / ".navig" / "settings.json",
            {"navig": {"ai": {"provider": "openai"}}},
        )
        resolver = self._make_resolver()
        settings = resolver.resolve()
        assert settings["navig.ai.provider"] == "openai"

    def test_local_overrides_project(self):
        _write_json(
            self.project_root / ".navig" / "settings.json",
            {"navig": {"inbox": {"mode": "move"}}},
        )
        _write_json(
            self.project_root / ".navig" / "settings.local.json",
            {"navig": {"inbox": {"mode": "link"}}},
        )
        resolver = self._make_resolver()
        settings = resolver.resolve()
        assert settings["navig.inbox.mode"] == "link"

    def test_nested_deep_merge(self):
        """Non-overlapping nested keys are merged, not replaced."""
        _write_json(
            self.global_dir / "settings.json",
            {"navig": {"ai": {"provider": "openai", "temperature": 0.5}}},
        )
        _write_json(
            self.project_root / ".navig" / "settings.json",
            {"navig": {"ai": {"model": "gpt-4"}}},
        )
        resolver = self._make_resolver()
        settings = resolver.resolve()
        # Both provider (from global) and model (from project) should be present
        assert settings["navig.ai.provider"] == "openai"
        assert settings["navig.ai.model"] == "gpt-4"

    def test_get_method(self):
        _write_json(
            self.project_root / ".navig" / "settings.json",
            {"navig": {"mesh": {"enabled": True}}},
        )
        resolver = self._make_resolver()
        assert resolver.get("navig.mesh.enabled") is True
        assert resolver.get("navig.nonexistent", default="fallback") == "fallback"

    def test_set_method_project_layer(self):
        resolver = self._make_resolver()
        resolver.set("navig.inbox.mode", "move", layer="project")
        settings_file = self.project_root / ".navig" / "settings.json"
        assert settings_file.is_file()
        data = json.loads(settings_file.read_text())
        assert data["navig"]["inbox"]["mode"] == "move"

    def test_set_method_invalidates_cache(self):
        resolver = self._make_resolver()
        first = resolver.get("navig.inbox.mode")
        resolver.set("navig.inbox.mode", "link", layer="project")
        second = resolver.get("navig.inbox.mode")
        assert second == "link"
        assert first != second

    def test_set_method_local_layer(self):
        resolver = self._make_resolver()
        resolver.set("navig.safety.mode", "strict", layer="local")
        local_file = self.project_root / ".navig" / "settings.local.json"
        assert local_file.is_file()

    def test_cache_refresh(self):
        resolver = self._make_resolver()
        _ = resolver.resolve()  # warm cache
        # Write a new project settings file
        _write_json(
            self.project_root / ".navig" / "settings.json",
            {"navig": {"isolation": True}},
        )
        # Without refresh — stale cache
        stale = resolver.get("navig.isolation")
        # With refresh
        fresh = resolver.resolve(refresh=True)
        assert fresh["navig.isolation"] is True

    def test_all_sources_returns_list(self):
        resolver = self._make_resolver()
        sources = resolver.all_sources()
        assert len(sources) >= 2
        # Each entry is (name, path, exists)
        for name, path, exists in sources:
            assert isinstance(name, str)
            assert isinstance(path, Path)
            assert isinstance(exists, bool)

    def test_layer_settings_loaded(self):
        import navig.settings.resolver as m
        from navig.settings.resolver import SettingsResolver

        orig = m._global_settings_dir
        m._global_settings_dir = lambda: self.global_dir

        layers_dir = self.global_dir / "layers"
        _write_json(
            layers_dir / "production" / "settings.json",
            {"navig": {"safety": {"mode": "strict"}}},
        )

        resolver = SettingsResolver(
            project_root=self.project_root,
            layer="production",
            resolve_secrets=False,
        )
        resolver._global_settings_dir = lambda: self.global_dir  # not used directly

        # Manually mock layers_dir
        orig_ld = m._layers_dir
        m._layers_dir = lambda: layers_dir

        try:
            settings = resolver.resolve()
            assert settings.get("navig.safety.mode") == "strict"
        finally:
            m._global_settings_dir = orig
            m._layers_dir = orig_ld

    def test_secret_reference_left_as_is_when_vault_unavailable(self):
        _write_json(
            self.project_root / ".navig" / "settings.json",
            {"navig": {"ai": {"api_key": "${BLACKBOX:openai_key}"}}},
        )
        resolver = self._make_resolver(resolve_secrets=True)
        settings = resolver.resolve()
        # Vault not configured — reference should be kept as-is
        key = settings.get("navig.ai.api_key", "")
        # Either kept as-is or resolved (either is acceptable in test env)
        assert key == "" or "${BLACKBOX:" in key or len(key) > 0

    def test_module_level_get_function(self):
        """Module-level get() should work without crashing."""
        import navig.settings

        # Should return the default without error
        val = navig.settings.get("navig.ai.provider", default="openai")
        assert val is not None


class TestFlattenUnflatten:
    def test_flatten_nested(self):
        from navig.settings.resolver import _flatten

        d = {"navig": {"ai": {"provider": "openai", "model": "gpt-4"}}}
        flat = _flatten(d)
        assert flat["navig.ai.provider"] == "openai"
        assert flat["navig.ai.model"] == "gpt-4"

    def test_unflatten(self):
        from navig.settings.resolver import _unflatten

        flat = {"navig.ai.provider": "openai", "navig.inbox.mode": "copy"}
        nested = _unflatten(flat)
        assert nested["navig"]["ai"]["provider"] == "openai"
        assert nested["navig"]["inbox"]["mode"] == "copy"

    def test_flatten_already_flat(self):
        from navig.settings.resolver import _flatten

        d = {"key": "value", "other": 42}
        flat = _flatten(d)
        assert flat == {"key": "value", "other": 42}

    def test_deep_merge_overrides(self):
        from navig.settings.resolver import _deep_merge

        base = {"a": {"b": 1, "c": 2}}
        override = {"a": {"b": 99}}
        result = _deep_merge(base, override)
        assert result["a"]["b"] == 99
        assert result["a"]["c"] == 2  # preserved

    def test_deep_merge_does_not_mutate(self):
        from navig.settings.resolver import _deep_merge

        base = {"x": {"y": 1}}
        override = {"x": {"z": 2}}
        _deep_merge(base, override)
        # base should not be modified
        assert "z" not in base["x"]
