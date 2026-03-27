"""
tests/test_loader.py — pytest suite for plugin_base + plugin_loader.

Run from the telegram-bot-navig root:
    pytest tests/ -v

No Telegram token needed — all PTB objects are mocked.
"""

from __future__ import annotations

import asyncio
import sys
import textwrap
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# Make imports work from tests/ subdirectory
# ---------------------------------------------------------------------------
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from plugin_base import BotPlugin, PluginContext, PluginEvent, PluginMeta
from plugin_loader import PluginLoader

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app() -> MagicMock:
    """Minimal PTB Application mock."""
    app = MagicMock()
    app.add_handler = MagicMock()
    app.remove_handler = MagicMock()
    return app


def _make_update(text: str = "/cmd") -> MagicMock:
    update = MagicMock()
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


def _make_context(*args: str) -> MagicMock:
    ctx = MagicMock()
    ctx.args = list(args)
    return ctx


def _write_plugin(tmp_path: Path, filename: str, body: str) -> Path:
    """Write a plugin .py file into tmp_path and return its path."""
    p = tmp_path / filename
    p.write_text(textwrap.dedent(body), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Minimal concrete plugin for testing
# ---------------------------------------------------------------------------


class _PingPlugin(BotPlugin):
    @property
    def meta(self) -> PluginMeta:
        return PluginMeta("ping", "Test ping plugin")

    @property
    def command(self) -> str:
        return "ping"

    async def handle(self, update, context) -> None:
        await update.message.reply_text("pong")


class _PassivePlugin(BotPlugin):
    @property
    def meta(self) -> PluginMeta:
        return PluginMeta("passive", "Passive test plugin")

    @property
    def command(self) -> str:
        return ""

    @property
    def passive_patterns(self) -> list[str]:
        return [r"hello\s+world"]

    async def handle(self, update, context) -> None:
        pass

    async def handle_message(self, update, context) -> None:
        await update.message.reply_text("hi!")


class _BrokenPlugin(BotPlugin):
    """Plugin whose handle() always raises."""

    @property
    def meta(self) -> PluginMeta:
        return PluginMeta("broken", "Always raises")

    @property
    def command(self) -> str:
        return "broken"

    async def handle(self, update, context) -> None:
        raise RuntimeError("intentional failure")


# ===========================================================================
# 1. PluginMeta + PluginContext + PluginEvent types
# ===========================================================================


class TestTypes:
    def test_plugin_meta_str(self):
        meta = PluginMeta(name="foo", description="bar", version="2.0.0")
        assert "foo" in str(meta)
        assert "2.0.0" in str(meta)

    def test_plugin_context_keys(self):
        keys = set(PluginContext.__annotations__)
        assert {"plugin_id", "plugin_dir", "store_dir", "config", "logger"} <= keys

    def test_plugin_event_fields(self):
        import dataclasses

        fields = {f.name for f in dataclasses.fields(PluginEvent)}
        assert fields == {"name", "source", "data"}

    def test_plugin_event_default_data(self):
        ev = PluginEvent(name="on_message", source="gateway")
        assert ev.data == {}


# ===========================================================================
# 2. BotPlugin base behaviour
# ===========================================================================


class TestBotPlugin:
    def setup_method(self):
        self.plugin = _PingPlugin()

    def test_enabled_by_default(self):
        assert self.plugin.enabled is True

    def test_disable_enable_roundtrip(self):
        self.plugin.disable()
        assert self.plugin.enabled is False
        self.plugin.enable()
        assert self.plugin.enabled is True

    def test_repr_shows_state(self):
        r = repr(self.plugin)
        assert "/ping" in r
        assert "on" in r
        self.plugin.disable()
        assert "off" in repr(self.plugin)

    def test_disabled_guard_returns_polite_message(self):
        self.plugin.disable()
        update = _make_update("/ping")
        asyncio.run(self.plugin(update, _make_context()))
        update.message.reply_text.assert_called_once()
        args = update.message.reply_text.call_args[0]
        assert "disabled" in args[0].lower()

    def test_exception_safety_replies_generic_error(self):
        plugin = _BrokenPlugin()
        update = _make_update("/broken")
        asyncio.run(plugin(update, _make_context()))
        update.message.reply_text.assert_called_once()
        args = update.message.reply_text.call_args[0]
        assert "went wrong" in args[0].lower() or "error" in args[0].lower()

    def test_handle_called_when_enabled(self):
        update = _make_update("/ping")
        asyncio.run(self.plugin(update, _make_context()))
        update.message.reply_text.assert_called_with("pong")

    def test_passive_patterns_default_empty(self):
        assert self.plugin.passive_patterns == []

    def test_handles_business_default_false(self):
        assert self.plugin.handles_business is False


# ===========================================================================
# 3. PluginLoader — discovery
# ===========================================================================


class TestPluginLoaderDiscovery:
    def test_load_all_discovers_plugins(self, tmp_path):
        _write_plugin(
            tmp_path,
            "myplugin.py",
            """
            from plugin_base import BotPlugin, PluginMeta
            class P(BotPlugin):
                @property
                def meta(self): return PluginMeta("myplugin","test")
                @property
                def command(self): return "myplugin"
                async def handle(self, u, c): pass
            def create(): return P()
        """,
        )
        loader = PluginLoader(_make_app(), plugins_dir=tmp_path)
        loader.load_all()
        assert "myplugin" in loader._plugins

    def test_load_all_skips_underscore_files(self, tmp_path):
        _write_plugin(tmp_path, "_private.py", "# should be skipped")
        loader = PluginLoader(_make_app(), plugins_dir=tmp_path)
        loader.load_all()
        assert len(loader._plugins) == 0

    def test_load_all_skips_no_create(self, tmp_path):
        _write_plugin(tmp_path, "nofactory.py", "x = 1  # no create()")
        loader = PluginLoader(_make_app(), plugins_dir=tmp_path)
        loader.load_all()
        assert len(loader._plugins) == 0

    def test_broken_plugin_tracked_in_errors(self, tmp_path):
        _write_plugin(tmp_path, "badplugin.py", "raise RuntimeError('boom')")
        loader = PluginLoader(_make_app(), plugins_dir=tmp_path)
        loader.load_all()
        assert "badplugin" in loader._errors

    def test_duplicate_name_not_registered(self, tmp_path):
        body = """
            from plugin_base import BotPlugin, PluginMeta
            class P(BotPlugin):
                @property
                def meta(self): return PluginMeta("same","test")
                @property
                def command(self): return "same"
                async def handle(self, u, c): pass
            def create(): return P()
        """
        _write_plugin(tmp_path, "same1.py", body)
        _write_plugin(tmp_path, "same2.py", body)
        loader = PluginLoader(_make_app(), plugins_dir=tmp_path)
        loader.load_all()
        # Only first one should be registered
        assert len([n for n in loader._plugins if n == "same"]) == 1

    def test_command_handler_stored(self, tmp_path):
        _write_plugin(
            tmp_path,
            "cmdplugin.py",
            """
            from plugin_base import BotPlugin, PluginMeta
            class P(BotPlugin):
                @property
                def meta(self): return PluginMeta("cmdplugin","test")
                @property
                def command(self): return "cmdplugin"
                async def handle(self, u, c): pass
            def create(): return P()
        """,
        )
        loader = PluginLoader(_make_app(), plugins_dir=tmp_path)
        loader.load_all()
        assert "cmdplugin" in loader._cmd_handlers


# ===========================================================================
# 4. PluginLoader — pip dep installation
# ===========================================================================


class TestPipDeps:
    def test_pip_deps_installed_from_manifest(self, tmp_path):
        manifest = '{"id":"t","version":"1.0.0","depends":{"pip":["requests>=2.0"]}}'
        (tmp_path / "plugin.json").write_text(manifest, encoding="utf-8")
        plugin_file = tmp_path / "t.py"
        plugin_file.write_text("", encoding="utf-8")

        with patch("subprocess.check_call") as mock_sub:
            loader = PluginLoader(_make_app(), plugins_dir=tmp_path)
            loader._install_pip_deps(plugin_file)
            mock_sub.assert_called_once()
            args = mock_sub.call_args[0][0]
            assert "requests>=2.0" in args

    def test_pip_deps_skipped_if_none(self, tmp_path):
        manifest = '{"id":"t","version":"1.0.0","depends":{"pip":[]}}'
        (tmp_path / "plugin.json").write_text(manifest, encoding="utf-8")
        plugin_file = tmp_path / "t.py"
        plugin_file.write_text("", encoding="utf-8")

        with patch("subprocess.check_call") as mock_sub:
            loader = PluginLoader(_make_app(), plugins_dir=tmp_path)
            loader._install_pip_deps(plugin_file)
            mock_sub.assert_not_called()

    def test_pip_deps_skipped_if_no_manifest(self, tmp_path):
        plugin_file = tmp_path / "nometa.py"
        plugin_file.write_text("", encoding="utf-8")
        with patch("subprocess.check_call") as mock_sub:
            loader = PluginLoader(_make_app(), plugins_dir=tmp_path)
            loader._install_pip_deps(plugin_file)
            mock_sub.assert_not_called()

    def test_pip_bad_manifest_logs_warning(self, tmp_path):
        (tmp_path / "plugin.json").write_text("{bad json", encoding="utf-8")
        plugin_file = tmp_path / "bad.py"
        plugin_file.write_text("", encoding="utf-8")
        with patch("subprocess.check_call") as mock_sub:
            loader = PluginLoader(_make_app(), plugins_dir=tmp_path)
            loader._install_pip_deps(plugin_file)  # should not raise
            mock_sub.assert_not_called()

    def test_pip_dep_as_string_coerced_to_list(self, tmp_path):
        """Sidecar with "pip": "pkg" (string not list) must not crash or char-unpack."""
        manifest = '{"id":"t","version":"1.0.0","depends":{"pip":"requests>=2.0"}}'
        (tmp_path / "plugin.json").write_text(manifest, encoding="utf-8")
        plugin_file = tmp_path / "t.py"
        plugin_file.write_text("", encoding="utf-8")
        with patch("subprocess.check_call") as mock_sub:
            loader = PluginLoader(_make_app(), plugins_dir=tmp_path)
            loader._install_pip_deps(plugin_file)
            # Must have called pip with the full dep string, not individual chars
            mock_sub.assert_called_once()
            args = mock_sub.call_args[0][0]
            assert "requests>=2.0" in args


# ===========================================================================
# 5. PluginLoader — activate / deactivate commands
# ===========================================================================


class TestActivateDeactivate:
    def _loader_with_plugin(self):
        app = _make_app()
        loader = PluginLoader(app)
        loader._plugins["ping"] = _PingPlugin()
        return loader

    def test_activate_enables_plugin(self):
        loader = self._loader_with_plugin()
        loader._plugins["ping"].disable()
        update = _make_update("/activate ping")
        asyncio.run(loader._cmd_activate(update, _make_context("ping")))
        assert loader._plugins["ping"].enabled is True
        update.message.reply_text.assert_called_once()

    def test_deactivate_disables_plugin(self):
        loader = self._loader_with_plugin()
        update = _make_update("/deactivate ping")
        asyncio.run(loader._cmd_deactivate(update, _make_context("ping")))
        assert loader._plugins["ping"].enabled is False
        update.message.reply_text.assert_called_once()

    def test_activate_unknown_plugin(self):
        loader = self._loader_with_plugin()
        update = _make_update("/activate unknown")
        asyncio.run(loader._cmd_activate(update, _make_context("unknown")))
        text = update.message.reply_text.call_args[0][0]
        assert "not found" in text.lower()

    def test_activate_no_args(self):
        loader = self._loader_with_plugin()
        update = _make_update("/activate")
        asyncio.run(loader._cmd_activate(update, _make_context()))
        text = update.message.reply_text.call_args[0][0]
        assert "usage" in text.lower()

    def test_deactivate_no_args(self):
        loader = self._loader_with_plugin()
        update = _make_update("/deactivate")
        asyncio.run(loader._cmd_deactivate(update, _make_context()))
        text = update.message.reply_text.call_args[0][0]
        assert "usage" in text.lower()


# ===========================================================================
# 6. PluginLoader — passive dispatch
# ===========================================================================


class TestPassiveDispatch:
    def _loader_with_passive(self):
        loader = PluginLoader(_make_app())
        loader._plugins["passive"] = _PassivePlugin()
        return loader

    def test_matching_text_fires_handle_message(self):
        loader = self._loader_with_passive()
        update = _make_update("hello world please")
        asyncio.run(loader._dispatch_passive(update, _make_context()))
        update.message.reply_text.assert_called_with("hi!")

    def test_non_matching_text_skips_plugin(self):
        loader = self._loader_with_passive()
        update = _make_update("goodbye world")
        asyncio.run(loader._dispatch_passive(update, _make_context()))
        update.message.reply_text.assert_not_called()

    def test_disabled_plugin_not_dispatched(self):
        loader = self._loader_with_passive()
        loader._plugins["passive"].disable()
        update = _make_update("hello world")
        asyncio.run(loader._dispatch_passive(update, _make_context()))
        update.message.reply_text.assert_not_called()


# ===========================================================================
# 7. PluginLoader — /plugins shows errors
# ===========================================================================


class TestPluginsCommand:
    def test_plugins_lists_loaded(self):
        loader = PluginLoader(_make_app())
        loader._plugins["ping"] = _PingPlugin()
        update = _make_update("/plugins")
        asyncio.run(loader._cmd_plugins(update, _make_context()))
        text = update.message.reply_text.call_args[0][0]
        assert "ping" in text

    def test_plugins_shows_load_errors(self):
        loader = PluginLoader(_make_app())
        loader._errors["badplugin"] = "SyntaxError: oops"
        update = _make_update("/plugins")
        asyncio.run(loader._cmd_plugins(update, _make_context()))
        text = update.message.reply_text.call_args[0][0]
        assert "badplugin" in text

    def test_plugins_empty(self):
        loader = PluginLoader(_make_app())
        update = _make_update("/plugins")
        asyncio.run(loader._cmd_plugins(update, _make_context()))
        text = update.message.reply_text.call_args[0][0]
        assert "no plugins" in text.lower()

    def test_passive_plugin_shows_pattern_count(self):
        loader = PluginLoader(_make_app())
        loader._plugins["passive"] = _PassivePlugin()
        update = _make_update("/plugins")
        asyncio.run(loader._cmd_plugins(update, _make_context()))
        text = update.message.reply_text.call_args[0][0]
        assert "passive" in text.lower() or "pattern" in text.lower()


# ===========================================================================
# 8. Hot-reload
# ===========================================================================


class TestHotReload:
    def test_hot_load_adds_new_plugin(self, tmp_path):
        _write_plugin(
            tmp_path,
            "dynplugin.py",
            """
            from plugin_base import BotPlugin, PluginMeta
            class P(BotPlugin):
                @property
                def meta(self): return PluginMeta("dynplugin","dyn")
                @property
                def command(self): return "dynplugin"
                async def handle(self, u, c): pass
            def create(): return P()
        """,
        )
        loader = PluginLoader(_make_app(), plugins_dir=tmp_path)
        loader._hot_load(tmp_path / "dynplugin.py")
        assert "dynplugin" in loader._plugins

    def test_hot_load_replaces_existing(self, tmp_path):
        body_v1 = """
            from plugin_base import BotPlugin, PluginMeta
            class P(BotPlugin):
                @property
                def meta(self): return PluginMeta("evolving","v1")
                @property
                def command(self): return "evolving"
                async def handle(self, u, c): pass
            def create(): return P()
        """
        _write_plugin(tmp_path, "evolving.py", body_v1)
        loader = PluginLoader(_make_app(), plugins_dir=tmp_path)
        loader._hot_load(tmp_path / "evolving.py")
        first = loader._plugins["evolving"]

        body_v2 = body_v1.replace("v1", "v2")
        Path(tmp_path / "evolving.py").write_text(
            textwrap.dedent(body_v2), encoding="utf-8"
        )
        loader._hot_load(tmp_path / "evolving.py")

        second = loader._plugins.get("evolving")
        assert second is not None
        assert second is not first  # new instance loaded

    def test_hot_load_populates_stem_map(self, tmp_path):
        _write_plugin(
            tmp_path,
            "mapped.py",
            """
            from plugin_base import BotPlugin, PluginMeta
            class P(BotPlugin):
                @property
                def meta(self): return PluginMeta("mapped","test")
                @property
                def command(self): return "mapped"
                async def handle(self, u, c): pass
            def create(): return P()
        """,
        )
        loader = PluginLoader(_make_app(), plugins_dir=tmp_path)
        loader._hot_load(tmp_path / "mapped.py")
        assert loader._stem_to_name_map.get("mapped") == "mapped"

    def test_hot_load_replace_clears_old_stem_map(self, tmp_path):
        body = """
            from plugin_base import BotPlugin, PluginMeta
            class P(BotPlugin):
                @property
                def meta(self): return PluginMeta("remap","test")
                @property
                def command(self): return "remap"
                async def handle(self, u, c): pass
            def create(): return P()
        """
        _write_plugin(tmp_path, "remap.py", body)
        loader = PluginLoader(_make_app(), plugins_dir=tmp_path)
        loader._hot_load(tmp_path / "remap.py")
        # Reload — old entry should be replaced, not duplicated
        loader._hot_load(tmp_path / "remap.py")
        assert list(loader._stem_to_name_map.values()).count("remap") == 1

    def test_start_watcher_no_crash_without_watchdog(self):
        import plugin_loader as _pl

        orig = _pl._WATCHDOG_AVAILABLE
        _pl._WATCHDOG_AVAILABLE = False
        try:
            loader = PluginLoader(_make_app())
            loader.start_watcher()  # must not raise
        finally:
            _pl._WATCHDOG_AVAILABLE = orig


# ===========================================================================
# 9. provides conflict detection
# ===========================================================================


class TestProvidesConflict:
    def _make_provides_plugin_file(
        self, tmp_path: Path, name: str, provides: list
    ) -> Path:
        import json as _json

        py = _write_plugin(
            tmp_path,
            f"{name}.py",
            f"""
            from plugin_base import BotPlugin, PluginMeta
            class P(BotPlugin):
                @property
                def meta(self): return PluginMeta("{name}","test")
                @property
                def command(self): return "{name}"
                async def handle(self, u, c): pass
            def create(): return P()
        """,
        )
        sidecar = tmp_path / f"{name}.json"
        sidecar.write_text(
            _json.dumps(
                {
                    "id": name,
                    "version": "1.0.0",
                    "provides": provides,
                    "depends": {"pip": []},
                }
            ),
            encoding="utf-8",
        )
        return py

    def test_first_plugin_registers_capability(self, tmp_path):
        self._make_provides_plugin_file(tmp_path, "provider_a", ["bot.feature"])
        loader = PluginLoader(_make_app(), plugins_dir=tmp_path)
        loader.load_all()
        assert loader._provides_index.get("bot.feature") == "provider_a"

    def test_second_plugin_conflict_not_loaded(self, tmp_path):
        self._make_provides_plugin_file(tmp_path, "provider_a", ["bot.feature"])
        self._make_provides_plugin_file(tmp_path, "provider_b", ["bot.feature"])
        loader = PluginLoader(_make_app(), plugins_dir=tmp_path)
        loader.load_all()
        # Only one provider allowed — one should be in errors
        assert ("provider_a" in loader._plugins) ^ (
            "provider_b" in loader._plugins
        ) or ("provider_a" in loader._plugins and "provider_b" not in loader._plugins)
        conflict_stems = [s for s, e in loader._errors.items() if "conflict" in e]
        assert len(conflict_stems) == 1

    def test_conflict_error_message_names_owner(self, tmp_path):
        self._make_provides_plugin_file(tmp_path, "owner_plugin", ["special.cap"])
        self._make_provides_plugin_file(tmp_path, "latecomer", ["special.cap"])
        loader = PluginLoader(_make_app(), plugins_dir=tmp_path)
        loader.load_all()
        # Load order depends on filesystem sort — first wins, second gets an error.
        # Check that whichever was rejected has an error mentioning the capability.
        err_owner = loader._errors.get("owner_plugin", "")
        err_late = loader._errors.get("latecomer", "")
        assert "special.cap" in (err_owner + err_late)

    def test_conflict_plugin_not_in_sys_modules(self, tmp_path):
        self._make_provides_plugin_file(tmp_path, "cap_a", ["unique.cap"])
        self._make_provides_plugin_file(tmp_path, "cap_b", ["unique.cap"])
        loader = PluginLoader(_make_app(), plugins_dir=tmp_path)
        loader.load_all()
        # The conflicting plugin's module must not remain in sys.modules
        conflicting_stem = next(s for s, e in loader._errors.items() if "conflict" in e)
        assert f"_navig_plugin_{conflicting_stem}" not in sys.modules

    def test_hot_unload_releases_capability(self, tmp_path):
        self._make_provides_plugin_file(tmp_path, "releaseable", ["release.cap"])
        loader = PluginLoader(_make_app(), plugins_dir=tmp_path)
        loader._hot_load(tmp_path / "releaseable.py")
        assert "release.cap" in loader._provides_index  # capability registered
        # Now unload and verify the capability is released
        loader._hot_unload_by_name("releaseable")
        assert "release.cap" not in loader._provides_index  # capability freed

        loader._hot_unload_by_name("releaseable")
        assert "release.cap" not in loader._provides_index
