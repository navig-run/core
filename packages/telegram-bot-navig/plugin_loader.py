"""
plugin_loader.py — Discovers, registers, and manages telegram-bot-navig plugins.

Responsibilities
----------------
1. Scan the `plugins/` directory for all `*.py` files (one plugin per file).
2. Import each module and call its `create()` factory to get a BotPlugin instance.
3. Register each plugin's command with the PTB Application as a CommandHandler.
4. Provide built-in management commands:
     /plugins            — list all plugins + their enabled/disabled status
     /activate <name>    — enable a named plugin
     /deactivate <name>  — disable a named plugin

Usage
-----
    from telegram.ext import Application
    from plugin_loader import PluginLoader

    app = Application.builder().token("YOUR_TOKEN").build()
    loader = PluginLoader(app, plugins_dir="plugins")
    loader.load_all()
    app.run_polling()
"""

from __future__ import annotations

import importlib.util
import json
import logging
import re
import subprocess
import sys
import threading
from pathlib import Path
from typing import Dict, Optional

from plugin_base import BotPlugin
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

try:
    from watchdog.events import FileSystemEvent, FileSystemEventHandler
    from watchdog.observers import Observer

    _WATCHDOG_AVAILABLE = True
except ImportError:
    _WATCHDOG_AVAILABLE = False

logger = logging.getLogger(__name__)


class PluginLoader:
    """
    Discovers all plugins in `plugins_dir`, registers their command handlers
    with the given PTB Application, and exposes management slug-commands.

    Parameters
    ----------
    app         : PTB Application instance (not yet running)
    plugins_dir : path to the directory that contains plugin modules
                  (defaults to "plugins/" relative to this file)
    """

    def __init__(
        self,
        app: Application,
        plugins_dir: str | Path | None = None,
    ) -> None:
        self._app = app
        self._plugins: Dict[str, BotPlugin] = {}  # name → instance
        self._errors: Dict[str, str] = {}  # stem → load error
        self._cmd_handlers: Dict[str, CommandHandler] = {}  # name → handler obj
        self._provides_index: Dict[str, str] = {}  # capability → plugin_name
        self._stem_to_name_map: Dict[str, str] = {}  # stem → plugin_name
        self._observer: Optional[object] = None
        self._reload_lock = threading.Lock()

        if plugins_dir is None:
            plugins_dir = Path(__file__).parent / "plugins"
        self._plugins_dir = Path(plugins_dir)

    # ------------------------------------------------------------------ #
    # Public API                                                           #
    # ------------------------------------------------------------------ #

    def load_all(self) -> None:
        """
        Scan plugins_dir, import every *.py module, instantiate plugins via
        create(), register their command handlers, and wire management commands.
        Call this once before app.run_polling().
        """
        if not self._plugins_dir.exists():
            logger.warning("Plugins directory does not exist: %s", self._plugins_dir)
            self._plugins_dir.mkdir(parents=True, exist_ok=True)

        for plugin_file in sorted(self._plugins_dir.glob("*.py")):
            if plugin_file.name.startswith("_"):
                continue
            self._load_from_file(plugin_file)

        # Register management commands
        self._app.add_handler(CommandHandler("help", self._cmd_help))
        self._app.add_handler(CommandHandler("plugins", self._cmd_plugins))
        self._app.add_handler(CommandHandler("activate", self._cmd_activate))
        self._app.add_handler(CommandHandler("deactivate", self._cmd_deactivate))

        # Register passive (NL / URL) message dispatcher
        passive = [p for p in self._plugins.values() if p.passive_patterns]
        if passive:
            self._app.add_handler(
                MessageHandler(filters.TEXT & ~filters.COMMAND, self._dispatch_passive)
            )
            logger.info(
                "%d plugin(s) registered for passive message detection",
                len(passive),
            )

        # Register Telegram Business message dispatcher
        biz = [p for p in self._plugins.values() if p.handles_business]
        if biz:
            try:
                self._app.add_handler(
                    MessageHandler(
                        filters.UpdateType.BUSINESS_MESSAGE, self._dispatch_business
                    )
                )
                logger.info(
                    "%d plugin(s) registered for business message handling",
                    len(biz),
                )
            except AttributeError:
                logger.warning(
                    "filters.UpdateType.BUSINESS_MESSAGE not available in this "
                    "python-telegram-bot version — business chat plugins inactive"
                )

        logger.info(
            "PluginLoader ready: %d plugin(s) loaded from %s",
            len(self._plugins),
            self._plugins_dir,
        )

    def get(self, name: str) -> BotPlugin | None:
        """Return the plugin instance for *name*, or None if not found."""
        return self._plugins.get(name)

    def all_plugins(self) -> list[BotPlugin]:
        """Return all registered plugin instances, in load order."""
        return list(self._plugins.values())

    def start_watcher(self) -> None:
        """
        Start a watchdog observer that hot-reloads plugins on file changes.

        - New *.py file dropped into plugins_dir → loaded immediately.
        - Existing *.py file modified → old plugin removed, new one loaded.
        - Deleted *.py file → plugin disabled and removed from registry.

        Call *after* load_all() and *before* app.run_polling().
        No-ops silently if watchdog is not installed.
        """
        if not _WATCHDOG_AVAILABLE:
            logger.warning(
                "watchdog is not installed — hot-reload disabled. "
                "Install with: pip install watchdog"
            )
            return

        handler = _PluginReloadHandler(self)
        observer = Observer()
        observer.schedule(handler, str(self._plugins_dir), recursive=False)
        observer.daemon = True
        observer.start()
        self._observer = observer
        logger.info("Hot-reload watcher started on %s", self._plugins_dir)

    def stop_watcher(self) -> None:
        """Stop the watchdog observer if running."""
        if self._observer is not None:
            self._observer.stop()  # type: ignore[attr-defined]
            self._observer.join()  # type: ignore[attr-defined]
            self._observer = None
            logger.info("Hot-reload watcher stopped")

    # ------------------------------------------------------------------ #
    # Hot-reload internals                                                 #
    # ------------------------------------------------------------------ #

    def _hot_load(self, path: Path) -> None:
        """Load or reload a single plugin file, updating handlers in place."""
        with self._reload_lock:
            # If plugin already loaded, unload the old instance first
            existing_name = self._stem_to_name(path.stem)
            if existing_name:
                self._hot_unload_by_name(existing_name)

            self._load_from_file(path)

            # Register passive dispatcher if this is the first passive plugin
            new_name = self._stem_to_name(path.stem)
            if new_name and self._plugins[new_name].passive_patterns:
                # Re-registering is safe — PTB deduplicates by object identity.
                # For simplicity we only add if not already present globally;
                # a full solution would track handler objects per plugin.
                pass  # passive dispatcher is registered at load_all() time.
                # New passive plugins discovered via hot-reload will fire
                # on next message because _dispatch_passive iterates
                # self._plugins dynamically — no re-registration needed.

            logger.info("Hot-reload complete for %s", path.name)

    def _hot_unload_by_name(self, name: str) -> None:
        """Remove a plugin's command handler and drop it from the registry."""
        plugin = self._plugins.pop(name, None)
        if plugin is None:
            return
        # Release any capabilities this plugin claimed
        self._provides_index = {
            cap: owner for cap, owner in self._provides_index.items() if owner != name
        }
        handler = self._cmd_handlers.pop(name, None)
        if handler is not None:
            try:
                self._app.remove_handler(handler, group=0)
            except Exception:
                pass  # PTB may raise if handler not found
        # Use the stem map for a direct, correct module key lookup
        stem = self._stem_to_name_map.pop(name, name)
        sys.modules.pop(f"_navig_plugin_{stem}", None)
        logger.info("Unloaded plugin '%s'", name)

    def _hot_delete(self, path: Path) -> None:
        """Disable and remove a plugin whose file was deleted."""
        with self._reload_lock:
            name = self._stem_to_name(path.stem)
            if name:
                plugin = self._plugins.get(name)
                if plugin:
                    plugin.disable()
                self._hot_unload_by_name(name)
            self._errors.pop(path.stem, None)
            logger.info("Removed deleted plugin file: %s", path.name)

    def _stem_to_name(self, stem: str) -> str | None:
        """Return the plugin registry name that was loaded from *stem*.py, or None."""
        return self._stem_to_name_map.get(stem)

    # ------------------------------------------------------------------ #
    # Internal loading                                                     #
    # ------------------------------------------------------------------ #

    def _read_manifest(self, plugin_file: Path) -> dict:
        """Return the sidecar JSON manifest for *plugin_file*, or {} if none."""
        for candidate in (
            plugin_file.parent / "plugin.json",
            plugin_file.with_suffix(".json"),
        ):
            if candidate.exists():
                try:
                    return json.loads(candidate.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical
        return {}

    def _install_pip_deps(
        self, plugin_file: Path, manifest: dict | None = None
    ) -> None:
        """
        Read the plugin.json adjacent to *plugin_file* and pip-install any
        packages listed under depends.pip.  Skips gracefully if no manifest
        or no pip deps are declared.

        If *manifest* is already parsed (e.g. from _read_manifest), pass it
        directly to avoid a double filesystem read.
        """
        if manifest is None:
            manifest_path = plugin_file.parent / "plugin.json"
            if not manifest_path.exists():
                manifest_path = plugin_file.with_suffix(".json")
                if not manifest_path.exists():
                    return
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                logger.warning(
                    "Malformed plugin.json at %s: %s",
                    plugin_file.with_suffix(".json"),
                    exc,
                )
                return

        pip_deps = manifest.get("depends", {}).get("pip", [])
        # Defensively coerce a bare string to a single-item list (malformed sidecar)
        if isinstance(pip_deps, str):
            pip_deps = [pip_deps]
        if not pip_deps:
            return

        logger.info(
            "Installing pip deps for plugin '%s': %s",
            manifest.get("id", plugin_file.stem),
            pip_deps,
        )
        try:
            subprocess.check_call(
                [sys.executable, "-m", "pip", "install", "--quiet", *pip_deps],
                stdout=subprocess.DEVNULL,
            )
        except subprocess.CalledProcessError as exc:
            raise RuntimeError(f"pip install failed for {pip_deps}: {exc}") from exc

    def _load_from_file(self, path: Path) -> None:
        """Import a single plugin module and register it."""
        module_name = f"_navig_plugin_{path.stem}"
        try:
            # Read manifest once — shared by pip-deps install and conflict check
            manifest = self._read_manifest(path)
            self._install_pip_deps(path, manifest=manifest)

            spec = importlib.util.spec_from_file_location(module_name, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot create module spec for {path}")

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            spec.loader.exec_module(module)  # type: ignore[attr-defined]

            if not hasattr(module, "create"):
                logger.warning(
                    "Plugin file %s has no create() factory — skipping", path.name
                )
                sys.modules.pop(module_name, None)  # clean up dangling ref
                return

            plugin: BotPlugin = module.create()

            if not isinstance(plugin, BotPlugin):
                logger.warning(
                    "create() in %s did not return a BotPlugin — skipping", path.name
                )
                sys.modules.pop(module_name, None)
                return

            # Prevent duplicate registrations
            if plugin.meta.name in self._plugins:
                logger.warning(
                    "Duplicate plugin name '%s' from %s — skipping",
                    plugin.meta.name,
                    path.name,
                )
                sys.modules.pop(module_name, None)
                return

            # Check for provides capability conflicts before committing
            for cap in manifest.get("provides", []):
                if cap in self._provides_index:
                    owner = self._provides_index[cap]
                    conflict_msg = (
                        f"provides conflict: '{cap}' already claimed by '{owner}'"
                    )
                    logger.warning(
                        "Plugin '%s' skipped — %s", plugin.meta.name, conflict_msg
                    )
                    self._errors[path.stem] = conflict_msg
                    sys.modules.pop(module_name, None)  # clean up dangling ref
                    return

            # All checks passed — commit registration
            self._plugins[plugin.meta.name] = plugin
            self._stem_to_name_map[path.stem] = plugin.meta.name

            # Register capabilities in the provides index
            for cap in manifest.get("provides", []):
                self._provides_index[cap] = plugin.meta.name

            if plugin.command:
                h = CommandHandler(plugin.command, plugin)
                self._cmd_handlers[plugin.meta.name] = h
                self._app.add_handler(h)
                logger.info(
                    "Loaded plugin '%s' (/%s) from %s",
                    plugin.meta.name,
                    plugin.command,
                    path.name,
                )
            else:
                logger.info(
                    "Loaded passive plugin '%s' from %s",
                    plugin.meta.name,
                    path.name,
                )

        except Exception as _exc:
            logger.exception("Failed to load plugin from %s", path)
            self._errors[path.stem] = str(_exc)
            sys.modules.pop(module_name, None)  # ensure no dangling ref on error

    # ------------------------------------------------------------------ #
    # Management command handlers                                          #
    # ------------------------------------------------------------------ #

    async def _dispatch_passive(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Route a non-command text message to all matching passive plugins."""
        text = (update.message.text or "") if update.message else ""
        for plugin in self._plugins.values():
            if not plugin.enabled:
                continue
            for pattern in plugin.passive_patterns:
                if re.search(pattern, text, re.IGNORECASE | re.DOTALL):
                    try:
                        await plugin.handle_message(update, context)
                    except Exception:
                        logger.exception(
                            "Passive handler error in plugin '%s'", plugin.meta.name
                        )
                    break  # one match per plugin prevents double-firing

    async def _dispatch_business(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Route business_message updates to all business-capable plugins."""
        for plugin in self._plugins.values():
            if plugin.enabled and plugin.handles_business:
                try:
                    await plugin.handle_business(update, context)
                except Exception:
                    logger.exception(
                        "Business handler error in plugin '%s'", plugin.meta.name
                    )

    async def _cmd_help(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/help — list all available bot commands."""
        lines = ["<b>Available commands:</b>\n"]
        lines.append("<i>Management</i>")
        lines.append("<code>/help</code>  — show this message")
        lines.append("<code>/plugins</code>  — list all plugins and their status")
        lines.append("<code>/activate &lt;name&gt;</code>  — enable a plugin")
        lines.append("<code>/deactivate &lt;name&gt;</code>  — disable a plugin")

        cmd_plugins = [p for p in self._plugins.values() if p.command]
        if cmd_plugins:
            lines.append("\n<i>Plugins</i>")
            for plugin in cmd_plugins:
                state = "" if plugin.enabled else " <i>(disabled)</i>"
                lines.append(f"<code>/{plugin.command}</code>  — {plugin.meta.description}{state}")

        passive_plugins = [
            p for p in self._plugins.values() if not p.command and p.passive_patterns
        ]
        if passive_plugins:
            lines.append("\n<i>Passive listeners</i>")
            for plugin in passive_plugins:
                state = "" if plugin.enabled else " <i>(disabled)</i>"
                lines.append(
                    f"• <b>{plugin.meta.name}</b>  — {plugin.meta.description}{state}"
                )

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def _cmd_plugins(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/plugins — list all plugins and their state, including load errors."""
        if not self._plugins and not self._errors:
            await update.message.reply_text("No plugins loaded.")
            return

        lines = ["<b>Installed plugins:</b>\n"]
        for plugin in self._plugins.values():
            state = "✅ enabled" if plugin.enabled else "❌ disabled"
            if plugin.command:
                trigger = f"<code>/{plugin.command}</code>"
            elif plugin.passive_patterns:
                trigger = f"<i>(passive — {len(plugin.passive_patterns)} pattern(s))</i>"
            else:
                trigger = "<i>(business only)</i>"
            biz = "  🏢 business" if plugin.handles_business else ""
            lines.append(
                f"• {trigger} — <b>{plugin.meta.name}</b> v{plugin.meta.version}"
                f"\n  {plugin.meta.description}"
                f"\n  Status: {state}{biz}"
            )

        if self._errors:
            lines.append("\n<b>Failed to load:</b>")
            for stem, err in self._errors.items():
                short = (err[:80] + "...") if len(err) > 80 else err
                lines.append(f"• WARNING <code>{stem}</code> — <code>{short}</code>")

        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def _cmd_activate(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/activate <plugin_name> — enable a named plugin."""
        args = context.args or []
        if not args:
            await update.message.reply_text("Usage: /activate <plugin_name>")
            return

        name = args[0].lower()
        plugin = self._plugins.get(name)
        if plugin is None:
            await update.message.reply_text(
                f'Plugin "{name}" not found. Use /plugins to see available plugins.'
            )
            return

        plugin.enable()
        await update.message.reply_text(
            f'Plugin "{name}" has been <b>enabled</b>.', parse_mode="HTML"
        )

    async def _cmd_deactivate(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """/deactivate <plugin_name> — disable a named plugin."""
        args = context.args or []
        if not args:
            await update.message.reply_text("Usage: /deactivate <plugin_name>")
            return

        name = args[0].lower()
        plugin = self._plugins.get(name)
        if plugin is None:
            await update.message.reply_text(
                f'Plugin "{name}" not found. Use /plugins to see available plugins.'
            )
            return

        plugin.disable()
        await update.message.reply_text(
            f'Plugin "{name}" has been <b>disabled</b>.', parse_mode="HTML"
        )


# ---------------------------------------------------------------------------
# Watchdog event handler for hot-reload
# ---------------------------------------------------------------------------

if _WATCHDOG_AVAILABLE:

    class _PluginReloadHandler(FileSystemEventHandler):
        """
        Watches the plugins/ directory and hot-reloads *.py files on change.

        Events handled:
          created  → load new plugin
          modified → reload existing plugin (remove old + load fresh)
          deleted  → disable + remove plugin
        """

        def __init__(self, loader: PluginLoader) -> None:
            super().__init__()
            self._loader = loader

        def _is_plugin_file(self, path: str) -> bool:
            p = Path(path)
            return p.suffix == ".py" and not p.name.startswith("_")

        def on_created(self, event: "FileSystemEvent") -> None:
            if not event.is_directory and self._is_plugin_file(event.src_path):
                logger.info("Hot-reload: new plugin file detected: %s", event.src_path)
                self._loader._hot_load(Path(event.src_path))

        def on_modified(self, event: "FileSystemEvent") -> None:
            if not event.is_directory and self._is_plugin_file(event.src_path):
                logger.info("Hot-reload: plugin file changed: %s", event.src_path)
                self._loader._hot_load(Path(event.src_path))

        def on_deleted(self, event: "FileSystemEvent") -> None:
            if not event.is_directory and self._is_plugin_file(event.src_path):
                logger.info("Hot-reload: plugin file removed: %s", event.src_path)
                self._loader._hot_delete(Path(event.src_path))

else:

    class _PluginReloadHandler:  # type: ignore[no-redef]
        """Stub when watchdog is not installed."""

        def __init__(self, loader: PluginLoader) -> None:
            pass
