"""
Batch 94 — tests for navig.core.plugins
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import sys

import pytest


# ---------------------------------------------------------------------------
# PluginState / PluginType enums
# ---------------------------------------------------------------------------


class TestPluginStateEnum:
    def test_values(self):
        from navig.core.plugins import PluginState
        assert PluginState.DISCOVERED == "discovered"
        assert PluginState.LOADED == "loaded"
        assert PluginState.ENABLED == "enabled"
        assert PluginState.DISABLED == "disabled"
        assert PluginState.ERROR == "error"
        assert PluginState.UNLOADED == "unloaded"


class TestPluginTypeEnum:
    def test_values(self):
        from navig.core.plugins import PluginType
        assert PluginType.COMMAND == "command"
        assert PluginType.CHANNEL == "channel"
        assert PluginType.PROVIDER == "provider"
        assert PluginType.TOOL == "tool"
        assert PluginType.HOOK == "hook"
        assert PluginType.EXTENSION == "extension"


# ---------------------------------------------------------------------------
# PluginMetadata
# ---------------------------------------------------------------------------


class TestPluginMetadata:
    def test_required_fields(self):
        from navig.core.plugins import PluginMetadata
        m = PluginMetadata(name="test", version="1.0.0")
        assert m.name == "test"
        assert m.version == "1.0.0"
        assert m.description == ""

    def test_defaults(self):
        from navig.core.plugins import PluginMetadata, PluginType
        m = PluginMetadata(name="x", version="0.1")
        assert m.type == PluginType.EXTENSION
        assert m.auto_enable is True
        assert m.priority == 100
        assert m.tags == []
        assert m.dependencies == []

    def test_to_dict(self):
        from navig.core.plugins import PluginMetadata
        m = PluginMetadata(name="x", version="1.0", description="desc")
        d = m.to_dict()
        assert d["name"] == "x"
        assert d["version"] == "1.0"
        assert d["description"] == "desc"
        assert "type" in d


# ---------------------------------------------------------------------------
# PluginInfo
# ---------------------------------------------------------------------------


class TestPluginInfo:
    def _make_info(self, state=None):
        from navig.core.plugins import PluginInfo, PluginMetadata, PluginState
        meta = PluginMetadata(name="myplugin", version="1.0")
        return PluginInfo(
            metadata=meta,
            state=state or PluginState.DISCOVERED,
        )

    def test_name_and_version_delegated(self):
        info = self._make_info()
        assert info.name == "myplugin"
        assert info.version == "1.0"

    def test_is_enabled_true(self):
        from navig.core.plugins import PluginState
        info = self._make_info(PluginState.ENABLED)
        assert info.is_enabled() is True

    def test_is_enabled_false_when_loaded(self):
        from navig.core.plugins import PluginState
        info = self._make_info(PluginState.LOADED)
        assert info.is_enabled() is False

    def test_is_loaded_states(self):
        from navig.core.plugins import PluginState
        for state in (PluginState.LOADED, PluginState.ENABLED, PluginState.DISABLED):
            info = self._make_info(state)
            assert info.is_loaded() is True

    def test_is_loaded_false_for_discovered(self):
        from navig.core.plugins import PluginState
        info = self._make_info(PluginState.DISCOVERED)
        assert info.is_loaded() is False

    def test_to_dict(self):
        from navig.core.plugins import PluginState
        info = self._make_info(PluginState.LOADED)
        d = info.to_dict()
        assert d["name"] == "myplugin"
        assert d["state"] == "loaded"
        assert d["error"] is None
        assert d["loaded_at"] is None

    def test_to_dict_with_source_path(self, tmp_path):
        from navig.core.plugins import PluginInfo, PluginMetadata, PluginState
        meta = PluginMetadata(name="p", version="1")
        info = PluginInfo(metadata=meta, state=PluginState.LOADED, source_path=tmp_path)
        d = info.to_dict()
        assert d["source_path"] == str(tmp_path)


# ---------------------------------------------------------------------------
# Plugin base class and @plugin decorator
# ---------------------------------------------------------------------------


class TestPluginBase:
    def _make_plugin_cls(self):
        from navig.core.plugins import Plugin, plugin

        @plugin(name="test-plugin", version="2.0", description="A test plugin")
        class TestPlugin(Plugin):
            def on_load(self):
                self.loaded = True

            def on_enable(self):
                self.enabled = True

            def on_disable(self):
                self.disabled = True

            def on_unload(self):
                self.unloaded = True

        return TestPlugin

    def test_decorator_sets_metadata(self):
        cls = self._make_plugin_cls()
        assert cls.metadata.name == "test-plugin"
        assert cls.metadata.version == "2.0"
        assert cls.metadata.description == "A test plugin"

    def test_name_property(self):
        cls = self._make_plugin_cls()
        p = cls()
        assert p.name == "test-plugin"

    def test_configure_merges_defaults(self):
        from navig.core.plugins import Plugin, plugin

        @plugin(name="cfg-plugin", version="1.0")
        class CfgPlugin(Plugin):
            pass

        CfgPlugin.metadata.default_config = {"timeout": 30, "retries": 3}
        p = CfgPlugin()
        p.configure({"retries": 5})
        assert p.config["timeout"] == 30
        assert p.config["retries"] == 5

    def test_lifecycle_callbacks(self):
        cls = self._make_plugin_cls()
        p = cls()
        p.on_load()
        assert p.loaded is True
        p.on_enable()
        assert p.enabled is True
        p.on_disable()
        assert p.disabled is True
        p.on_unload()
        assert p.unloaded is True

    def test_register_hook_graceful_when_hooks_unavailable(self):
        from navig.core.plugins import Plugin, plugin

        @plugin(name="hook-plugin", version="1.0")
        class HookPlugin(Plugin):
            pass

        p = HookPlugin()
        # Should not raise even if navig.core.hooks is unavailable
        with patch.dict("sys.modules", {"navig.core.hooks": None}):
            result = p.register_hook("some:event", lambda: None)
        # result is "" when import fails
        assert isinstance(result, str)

    def test_cleanup_hooks_graceful(self):
        from navig.core.plugins import Plugin, plugin

        @plugin(name="cleanup-plugin", version="1.0")
        class CleanupPlugin(Plugin):
            pass

        p = CleanupPlugin()
        with patch.dict("sys.modules", {"navig.core.hooks": None}):
            p._cleanup_hooks()  # Should not raise


# ---------------------------------------------------------------------------
# PluginRegistry — basic operations
# ---------------------------------------------------------------------------


class TestPluginRegistry:
    def _registry(self):
        from navig.core.plugins import PluginRegistry
        r = PluginRegistry()
        # Don't call initialize() to avoid filesystem side effects
        return r

    def test_initial_state(self):
        r = self._registry()
        assert r._plugins == {}
        assert r._load_order == []
        assert r._initialized is False

    def test_get_plugin_missing_returns_none(self):
        r = self._registry()
        assert r.get_plugin("nonexistent") is None

    def test_get_status_empty(self):
        r = self._registry()
        status = r.get_status()
        assert status["total"] == 0
        assert status["states"] == {}

    def test_list_plugins_empty(self):
        r = self._registry()
        assert r.list_plugins() == []

    def test_load_plugin_raises_when_not_found(self):
        from navig.core.plugins import PluginRegistry
        r = PluginRegistry()
        with pytest.raises(ValueError, match="not found"):
            r.load_plugin("nonexistent")

    def test_enable_plugin_raises_when_not_found(self):
        from navig.core.plugins import PluginRegistry
        r = PluginRegistry()
        with pytest.raises(ValueError, match="not found"):
            r.enable_plugin("nonexistent")

    def test_disable_plugin_raises_when_not_found(self):
        from navig.core.plugins import PluginRegistry
        r = PluginRegistry()
        with pytest.raises(ValueError, match="not found"):
            r.disable_plugin("nonexistent")

    def test_unload_plugin_raises_when_not_found(self):
        from navig.core.plugins import PluginRegistry
        r = PluginRegistry()
        with pytest.raises(ValueError, match="not found"):
            r.unload_plugin("nonexistent")


# ---------------------------------------------------------------------------
# PluginRegistry — lifecycle with a synthetic plugin
# ---------------------------------------------------------------------------


class TestPluginRegistryLifecycle:
    def _make_registry_with_plugin(self):
        """Create a registry with a pre-loaded mock plugin."""
        from navig.core.plugins import (
            PluginRegistry, PluginInfo, PluginMetadata, PluginState,
            Plugin, plugin,
        )

        @plugin(name="lifecycle-plugin", version="1.0")
        class LifecyclePlugin(Plugin):
            def on_load(self):
                self.was_loaded = True

            def on_enable(self):
                self.was_enabled = True

            def on_disable(self):
                self.was_disabled = True

            def on_unload(self):
                self.was_unloaded = True

        r = PluginRegistry()
        # Seed the registry with discovered state
        module_name = "navig_plugins._test_lifecycle"
        module = MagicMock()
        module.__dict__["LifecyclePlugin"] = LifecyclePlugin
        import inspect as _inspect
        # We'll just manually plant the class in a fake module
        import types
        fake_mod = types.ModuleType(module_name)
        fake_mod.LifecyclePlugin = LifecyclePlugin
        sys.modules[module_name] = fake_mod

        info = PluginInfo(
            metadata=LifecyclePlugin.metadata,
            state=PluginState.DISCOVERED,
            source_path=Path("/fake"),
            module_name=module_name,
        )
        r._plugins["lifecycle-plugin"] = info
        return r, LifecyclePlugin

    def test_load_plugin(self):
        from navig.core.plugins import PluginState
        r, _ = self._make_registry_with_plugin()
        with patch.object(r, "_trigger_hook"):
            info = r.load_plugin("lifecycle-plugin")
        assert info.state == PluginState.LOADED
        assert info.instance is not None
        assert info.loaded_at is not None
        assert info.instance.was_loaded is True

    def test_enable_plugin(self):
        from navig.core.plugins import PluginState
        r, _ = self._make_registry_with_plugin()
        with patch.object(r, "_trigger_hook"):
            r.load_plugin("lifecycle-plugin")
            info = r.enable_plugin("lifecycle-plugin")
        assert info.state == PluginState.ENABLED
        assert info.enabled_at is not None
        assert info.instance.was_enabled is True

    def test_disable_plugin(self):
        from navig.core.plugins import PluginState
        r, _ = self._make_registry_with_plugin()
        with patch.object(r, "_trigger_hook"):
            r.load_plugin("lifecycle-plugin")
            r.enable_plugin("lifecycle-plugin")
            info = r.disable_plugin("lifecycle-plugin")
        assert info.state == PluginState.DISABLED
        assert info.instance.was_disabled is True

    def test_unload_plugin(self):
        from navig.core.plugins import PluginState
        r, _ = self._make_registry_with_plugin()
        with patch.object(r, "_trigger_hook"):
            r.load_plugin("lifecycle-plugin")
            r.enable_plugin("lifecycle-plugin")
            info = r.unload_plugin("lifecycle-plugin")
        assert info.state == PluginState.UNLOADED
        assert info.instance is None

    def test_disable_already_disabled_is_noop(self):
        from navig.core.plugins import PluginState
        r, _ = self._make_registry_with_plugin()
        with patch.object(r, "_trigger_hook"):
            r.load_plugin("lifecycle-plugin")
            r.enable_plugin("lifecycle-plugin")
            r.disable_plugin("lifecycle-plugin")
            # Second call should be no-op
            info = r.disable_plugin("lifecycle-plugin")
        assert info.state == PluginState.DISABLED

    def test_enable_already_enabled_is_noop(self):
        from navig.core.plugins import PluginState
        r, _ = self._make_registry_with_plugin()
        with patch.object(r, "_trigger_hook"):
            r.load_plugin("lifecycle-plugin")
            r.enable_plugin("lifecycle-plugin")
            info = r.enable_plugin("lifecycle-plugin")
        assert info.state == PluginState.ENABLED

    def test_load_error_state(self):
        from navig.core.plugins import (
            PluginRegistry, PluginInfo, PluginMetadata, PluginState,
        )
        r = PluginRegistry()
        info = PluginInfo(
            metadata=PluginMetadata(name="bad-plugin", version="0"),
            state=PluginState.ERROR,
            error="init failed",
        )
        r._plugins["bad-plugin"] = info

        with pytest.raises(ValueError, match="error state"):
            r.load_plugin("bad-plugin")


# ---------------------------------------------------------------------------
# PluginRegistry — query methods
# ---------------------------------------------------------------------------


class TestPluginRegistryQuery:
    def _registry_with_plugins(self):
        from navig.core.plugins import (
            PluginRegistry, PluginInfo, PluginMetadata, PluginState, PluginType
        )
        r = PluginRegistry()
        meta_cmd = PluginMetadata(name="cmd-plugin", version="1", type=PluginType.COMMAND)
        meta_ext = PluginMetadata(name="ext-plugin", version="1", type=PluginType.EXTENSION)
        r._plugins["cmd-plugin"] = PluginInfo(metadata=meta_cmd, state=PluginState.ENABLED)
        r._plugins["ext-plugin"] = PluginInfo(metadata=meta_ext, state=PluginState.LOADED)
        return r

    def test_list_plugins_all(self):
        r = self._registry_with_plugins()
        assert len(r.list_plugins()) == 2

    def test_list_plugins_filter_state(self):
        from navig.core.plugins import PluginState
        r = self._registry_with_plugins()
        enabled = r.list_plugins(state=PluginState.ENABLED)
        assert len(enabled) == 1
        assert enabled[0].name == "cmd-plugin"

    def test_list_plugins_filter_type(self):
        from navig.core.plugins import PluginType
        r = self._registry_with_plugins()
        cmds = r.list_plugins(plugin_type=PluginType.COMMAND)
        assert len(cmds) == 1
        assert cmds[0].name == "cmd-plugin"

    def test_get_enabled_plugins(self):
        r = self._registry_with_plugins()
        enabled = r.get_enabled_plugins()
        assert len(enabled) == 1
        assert enabled[0].name == "cmd-plugin"

    def test_get_status(self):
        r = self._registry_with_plugins()
        status = r.get_status()
        assert status["total"] == 2
        assert status["states"]["enabled"] == 1
        assert status["states"]["loaded"] == 1


# ---------------------------------------------------------------------------
# PluginRegistry — discover_plugins (filesystem-based)
# ---------------------------------------------------------------------------


class TestPluginRegistryDiscover:
    def test_discover_empty_dir(self, tmp_path):
        from navig.core.plugins import PluginRegistry
        r = PluginRegistry()
        r._plugin_dirs = [tmp_path]
        r._initialized = True
        discovered = r.discover_plugins()
        assert discovered == []

    def test_discover_plugin_file(self, tmp_path):
        from navig.core.plugins import PluginRegistry

        plugin_code = '''
from navig.core.plugins import Plugin, plugin

@plugin(name="file-plugin", version="0.1")
class FilePlugin(Plugin):
    pass
'''
        plugin_file = tmp_path / "fileplugin.py"
        plugin_file.write_text(plugin_code)

        r = PluginRegistry()
        r._plugin_dirs = [tmp_path]
        r._initialized = True
        discovered = r.discover_plugins()
        assert any(p.name == "file-plugin" for p in discovered)

    def test_discover_plugin_package(self, tmp_path):
        from navig.core.plugins import PluginRegistry

        pkg_dir = tmp_path / "mypkg"
        pkg_dir.mkdir()
        plugin_code = '''
from navig.core.plugins import Plugin, plugin

@plugin(name="pkg-plugin", version="0.2")
class PkgPlugin(Plugin):
    pass
'''
        (pkg_dir / "__init__.py").write_text(plugin_code)

        r = PluginRegistry()
        r._plugin_dirs = [tmp_path]
        r._initialized = True
        discovered = r.discover_plugins()
        assert any(p.name == "pkg-plugin" for p in discovered)

    def test_discover_handles_broken_file(self, tmp_path):
        from navig.core.plugins import PluginRegistry, PluginState

        bad_file = tmp_path / "badplugin.py"
        bad_file.write_text("this is invalid python !!!!")

        r = PluginRegistry()
        r._plugin_dirs = [tmp_path]
        r._initialized = True
        discovered = r.discover_plugins()
        assert any(p.state == PluginState.ERROR for p in discovered)


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


class TestModuleLevelHelpers:
    def test_get_plugin_registry_returns_instance(self):
        from navig.core import plugins as pm
        # Reset singleton
        orig = pm._registry
        pm._registry = None
        try:
            with patch.object(pm.PluginRegistry, "initialize"):
                reg = pm.get_plugin_registry()
            assert reg is not None
        finally:
            pm._registry = orig

    def test_get_plugin_registry_singleton(self):
        from navig.core import plugins as pm
        orig = pm._registry
        pm._registry = None
        try:
            with patch.object(pm.PluginRegistry, "initialize"):
                r1 = pm.get_plugin_registry()
                r2 = pm.get_plugin_registry()
            assert r1 is r2
        finally:
            pm._registry = orig

    def test_list_plugins_calls_registry(self):
        from navig.core import plugins as pm
        mock_reg = MagicMock()
        mock_reg.list_plugins.return_value = []
        with patch.object(pm, "get_plugin_registry", return_value=mock_reg):
            result = pm.list_plugins()
        assert result == []
        mock_reg.list_plugins.assert_called_once()

    def test_get_plugin_calls_registry(self):
        from navig.core import plugins as pm
        mock_reg = MagicMock()
        mock_reg.get_plugin.return_value = None
        with patch.object(pm, "get_plugin_registry", return_value=mock_reg):
            result = pm.get_plugin("some-plugin")
        assert result is None
        mock_reg.get_plugin.assert_called_once_with("some-plugin")
