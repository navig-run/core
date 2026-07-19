"""PluginHost — one façade over every plugin format.

Three install formats, one API:

- **package** — a CC/NAVIG bundle dir (`.claude-plugin/plugin.json`) under the
  user plugins dir or a trusted project's `.navig/plugins/` (`package.py`).
- **pip** — a separately installed distribution exposing `navig.plugins` /
  `navig.commands` entry points (navig-social, navig-email, harbor…).
- **legacy** — a self-registering `plugin.py` Typer dir (`PluginManager`).

State is unified on the existing config key `plugins.disabled_plugins`
(one list for all formats). Every enable/disable regenerates
``~/.navig/disabled_commands.json`` — a flat ``{command: plugin_id}`` map of
CLI commands whose provider is disabled. That file is the ONLY thing the CLI
fast path reads (`cli/registration.py`), so toggling never slows startup.

The Store hub (`navig.hub`) and the `navig plugin` / `navig store` commands
are thin wrappers over this class. Reuses `load_package` / `PluginManager` /
`MarketplaceStore` — never reimplements them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from navig.plugins.lifecycle import LifecycleTracker, PluginHealth

logger = logging.getLogger(__name__)

DISABLED_COMMANDS_FILE = "disabled_commands.json"


@dataclass
class InstalledPlugin:
    id: str
    format: str                      # "package" | "pip" | "legacy"
    path: Path | None
    version: str = ""
    description: str = ""
    enabled: bool = True
    health: PluginHealth | None = None
    source: str = "user"             # builtin | user | project | pip
    commands: list[str] = field(default_factory=list)   # top-level CLI names it provides
    error: str = ""                  # legacy-format load error, when known
    missing_deps: list[str] = field(default_factory=list)  # legacy-format unmet deps


class PluginHost:
    """Aggregates, toggles, installs, and removes plugins across all formats."""

    # ── discovery ────────────────────────────────────────────────────────────

    def list_installed(self, *, refresh: bool = False) -> list[InstalledPlugin]:
        out: dict[str, InstalledPlugin] = {}
        for p in (*self._package_plugins(), *self._pip_plugins(),
                  *self._legacy_plugins(refresh=refresh)):
            existing = out.get(p.id)
            if existing is None:
                out[p.id] = p
            else:
                # Same id across formats (e.g. a package dir AND a pip dist):
                # first format wins the row, but UNION the CLI verbs so a later
                # format's commands aren't lost from disabled_commands.json.
                for cmd in p.commands:
                    if cmd not in existing.commands:
                        existing.commands.append(cmd)
        return sorted(out.values(), key=lambda p: p.id.lower())

    def get(self, plugin_id: str) -> InstalledPlugin | None:
        plugins = self.list_installed()
        exact = next((p for p in plugins if p.id == plugin_id), None)
        if exact is not None:
            return exact
        # Legacy alias: a plugin's directory name (e.g. `mini_control` for `mini`).
        return next(
            (p for p in plugins if p.path is not None and p.path.name == plugin_id), None
        )

    def tracker(self) -> LifecycleTracker:
        """Aggregated health of every package-format plugin (for doctor/Store)."""
        tracker = LifecycleTracker()
        from navig.plugins.package import installed_plugin_roots, load_package, project_plugin_roots

        for root in (
            *installed_plugin_roots(include_disabled=True),
            *project_plugin_roots(include_disabled=True),
        ):
            tracker.track(load_package(root).health)
        return tracker

    def _package_plugins(self) -> list[InstalledPlugin]:
        from navig.plugins.package import (
            disabled_plugin_ids,
            installed_plugin_roots,
            load_package,
            project_plugin_roots,
        )

        disabled = disabled_plugin_ids()
        plugins: list[InstalledPlugin] = []
        for root, source in (
            *((r, "user") for r in installed_plugin_roots(include_disabled=True)),
            *((r, "project") for r in project_plugin_roots(include_disabled=True)),
        ):
            pkg = load_package(root)
            plugins.append(InstalledPlugin(
                id=pkg.plugin_id,
                format="package",
                path=root,
                version=str(pkg.manifest.get("version", "") or ""),
                description=str(pkg.manifest.get("description", "") or ""),
                enabled=pkg.plugin_id not in disabled,
                health=pkg.health,
                source=source,
            ))
        return plugins

    def _pip_plugins(self) -> list[InstalledPlugin]:
        from importlib import metadata

        from navig.plugins.package import disabled_plugin_ids

        disabled = disabled_plugin_ids()
        by_dist: dict[str, InstalledPlugin] = {}

        def _dist_of(ep) -> tuple[str, str, str]:
            dist = getattr(ep, "dist", None)
            if dist is None:
                return ep.name, "", ""
            meta = dist.metadata
            return (
                (dist.name or ep.name),
                dist.version or "",
                (meta.get("Summary") or "") if meta else "",
            )

        try:
            for ep in metadata.entry_points(group="navig.plugins"):
                dist_name, version, summary = _dist_of(ep)
                if dist_name == "navig":  # core never self-registers; be safe
                    continue
                by_dist.setdefault(dist_name, InstalledPlugin(
                    id=dist_name, format="pip", path=None, version=version,
                    description=summary, enabled=dist_name not in disabled, source="pip",
                ))
            for ep in metadata.entry_points(group="navig.commands"):
                dist_name, version, summary = _dist_of(ep)
                if dist_name == "navig":
                    continue
                plugin = by_dist.setdefault(dist_name, InstalledPlugin(
                    id=dist_name, format="pip", path=None, version=version,
                    description=summary, enabled=dist_name not in disabled, source="pip",
                ))
                if ep.name not in plugin.commands:
                    plugin.commands.append(ep.name)
        except Exception as exc:  # noqa: BLE001 — a broken dist must not kill discovery
            logger.debug("pip plugin discovery failed: %s", exc)
        return list(by_dist.values())

    def _legacy_plugins(self, *, refresh: bool = False) -> list[InstalledPlugin]:
        try:
            from navig.plugins import get_plugin_manager

            manager = get_plugin_manager()
            if refresh or not manager.list_plugins():
                manager.discover_plugins()
            plugins: list[InstalledPlugin] = []
            for info in manager.list_plugins().values():
                plugins.append(InstalledPlugin(
                    id=info.name,
                    format="legacy",
                    path=info.path,
                    version=info.version,
                    description=info.description,
                    enabled=info.enabled,
                    source=info.source,
                    commands=[info.name],  # a legacy plugin registers its name as the CLI verb
                    error=getattr(info, "error", None) or "",
                    missing_deps=list(getattr(info, "missing_deps", None) or []),
                ))
            return plugins
        except Exception as exc:  # noqa: BLE001
            logger.debug("legacy plugin discovery failed: %s", exc)
            return []

    def diagnose_legacy(self, plugin: InstalledPlugin) -> InstalledPlugin:
        """Force-load a legacy plugin to surface its real error / missing deps.

        Discovery alone never imports legacy plugins, so list-time error state
        is usually empty; `navig plugin show` calls this for the one plugin the
        user is asking about.
        """
        if plugin.format != "legacy":
            return plugin
        try:
            from navig.plugins import get_plugin_manager

            manager = get_plugin_manager()
            manager.load_plugin(plugin.id)
            info = manager.get_plugin_info(plugin.id)
            if info is not None:
                plugin.error = getattr(info, "error", None) or ""
                plugin.missing_deps = list(getattr(info, "missing_deps", None) or [])
        except Exception as exc:  # noqa: BLE001
            plugin.error = plugin.error or str(exc)
        return plugin

    # ── enable / disable ─────────────────────────────────────────────────────

    def enable(self, plugin_id: str) -> InstalledPlugin:
        plugin = self._require(plugin_id)
        from navig.config import get_config_manager

        config = get_config_manager()
        # Clear both the canonical id and (defensively) the dir name, in case an
        # older build wrote the dir name into the disabled set.
        names = {plugin.id}
        if plugin.path is not None:
            names.add(plugin.path.name)
        for name in names:
            config.enable_plugin(name)
        self.regenerate_disabled_commands()
        return plugin

    def disable(self, plugin_id: str) -> InstalledPlugin:
        plugin = self._require(plugin_id)
        from navig.config import get_config_manager

        config = get_config_manager()
        for name in self._identifiers(plugin):
            config.disable_plugin(name)
        self.regenerate_disabled_commands()
        return plugin

    def _require(self, plugin_id: str) -> InstalledPlugin:
        plugin = self.get(plugin_id)
        if plugin is None:
            raise KeyError(f"Plugin '{plugin_id}' is not installed")
        return plugin

    @staticmethod
    def _identifiers(plugin: InstalledPlugin) -> list[str]:
        # Only the CANONICAL id — writing the bare dir name too would let one
        # plugin's id collaterally disable a different plugin whose directory
        # happens to share that name (the disabled set is one flat pool, and the
        # root filters key on the manifest id via _plugin_id_of).
        return [plugin.id]

    def regenerate_disabled_commands(self) -> Path:
        """Rewrite ~/.navig/disabled_commands.json ({command: plugin_id}).

        The CLI fast path reads only this file to drop a disabled plugin's
        commands from registration, so they fall through to the
        suggest-and-activate path.
        """
        from navig.platform.paths import config_dir
        from navig.plugins.package import disabled_plugin_ids

        disabled = disabled_plugin_ids()
        mapping: dict[str, str] = {}
        if disabled:
            for plugin in self.list_installed():
                if plugin.id in disabled or (plugin.path and plugin.path.name in disabled):
                    for cmd in plugin.commands:
                        mapping[cmd] = plugin.id
        target = config_dir() / DISABLED_COMMANDS_FILE
        try:
            if mapping:
                # Atomic: the CLI fast path reads this on every startup, and a
                # torn write would silently re-enable every disabled command.
                from navig.core.yaml_io import atomic_write_text

                atomic_write_text(target, json.dumps(mapping, indent=2, sort_keys=True))
            elif target.exists():
                target.unlink()
        except OSError as exc:
            logger.warning("could not write %s: %s", target, exc)
        return target

    # ── install / uninstall ──────────────────────────────────────────────────

    def install(self, source: str) -> Path:
        """Install a plugin from a local dir, .zip, git URL, or marketplace name.

        Returns the installed plugin's directory. Raises ValueError with a
        user-facing message on any rejection (callers render it).
        """
        src = Path(source).expanduser()
        if src.exists() and src.is_file() and src.suffix.lower() == ".zip":
            return self._install_zip(src)
        if src.exists() and src.is_dir():
            return self._install_dir(src)
        if source.startswith(("http://", "https://", "git@", "ssh://")):
            return self._install_git(source)
        return self._install_from_marketplace(source)

    def uninstall(self, plugin_id: str) -> InstalledPlugin:
        import shutil

        plugin = self._require(plugin_id)
        if plugin.source == "builtin":
            raise ValueError("Cannot uninstall built-in plugins — disable instead")
        if plugin.format == "pip":
            raise ValueError(
                f"'{plugin_id}' is a pip-installed plugin — remove it with: "
                f"pip uninstall {plugin_id}"
            )
        if plugin.path is None or not plugin.path.exists():
            raise ValueError(f"Plugin '{plugin_id}' has no removable directory")
        shutil.rmtree(plugin.path)
        self.regenerate_disabled_commands()
        return plugin

    # — install helpers (moved from main.py plugin_install, logic unchanged) —

    def _install_dir(self, source_path: Path) -> Path:
        import re
        import shutil

        from navig.config import get_config_manager

        try:
            source_path = source_path.resolve(strict=True)
        except OSError as exc:
            raise ValueError(f"Invalid plugin path: {exc}") from exc
        if source_path.is_symlink():
            raise ValueError("Plugin source directories cannot be symbolic links.")
        linked_entry = next(
            (entry for entry in source_path.rglob("*") if entry.is_symlink()), None
        )
        if linked_entry is not None:
            raise ValueError(f"Plugin source contains a symbolic link: {linked_entry}")

        legacy_entry = source_path / "plugin.py"
        has_package_manifest = (
            (source_path / ".claude-plugin" / "plugin.json").exists()
            or (source_path / "plugin.json").exists()
        )
        if not legacy_entry.exists() and not has_package_manifest:
            raise ValueError("Directory must contain plugin.py or .claude-plugin/plugin.json.")

        if has_package_manifest and not legacy_entry.exists():
            from navig.plugins.package import load_package

            pkg = load_package(source_path)
            if not pkg.health.is_usable:
                raise ValueError(
                    f"Plugin package is not usable ({pkg.health.state.value}): "
                    f"{pkg.health.error or 'manifest could not be loaded'}"
                )
            for comp in pkg.health.degraded_components():
                logger.warning("%s:%s degraded — %s", comp.kind, comp.name, comp.error)

        plugin_name = source_path.name
        if plugin_name in {"", ".", ".."} or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_-]*", plugin_name
        ):
            raise ValueError(
                f"Invalid plugin name: '{plugin_name}' — names must contain only "
                "letters, digits, underscores, or hyphens."
            )

        dest_root = get_config_manager().plugins_dir.resolve()
        dest_path = (dest_root / plugin_name).resolve()
        try:
            dest_path.relative_to(dest_root)
        except ValueError:
            raise ValueError("Resolved plugin path escapes the NAVIG plugins directory.") from None
        if dest_path.exists():
            raise ValueError(f"Plugin '{plugin_name}' already exists at {dest_path}")

        dest_root.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source_path, dest_path)
        return dest_path

    def _install_zip(self, zip_path: Path) -> Path:
        """Extract a plugin .zip to a temp dir (zip-slip guarded) and install it."""
        import shutil
        import tempfile
        import zipfile

        extract_root = Path(tempfile.mkdtemp(prefix="navig-plugin-zip-")).resolve()
        try:
            try:
                with zipfile.ZipFile(zip_path) as zf:
                    for member in zf.namelist():
                        target = (extract_root / member).resolve()
                        # Strict containment (prefix checks pass sibling dirs).
                        if not target.is_relative_to(extract_root):
                            raise ValueError(f"Zip entry escapes extraction dir: {member}")
                    zf.extractall(extract_root)
            except zipfile.BadZipFile as exc:
                raise ValueError(f"Not a valid zip archive: {zip_path}") from exc
            # Accept both a bare bundle and a single wrapping directory.
            entries = [p for p in extract_root.iterdir() if not p.name.startswith("__MACOSX")]
            root = entries[0] if len(entries) == 1 and entries[0].is_dir() else extract_root
            return self._install_dir(root)
        finally:
            # _install_dir copied to dest; the temp extraction is no longer needed.
            shutil.rmtree(extract_root, ignore_errors=True)

    def _install_git(self, url: str) -> Path:
        import shutil
        import subprocess
        import tempfile

        tmp_root = Path(tempfile.mkdtemp(prefix="navig-plugin-"))
        clone_dir = tmp_root / "src"
        try:
            try:
                subprocess.run(
                    ["git", "clone", "--depth", "1", url, str(clone_dir)],
                    check=True, capture_output=True, text=True,
                )
            except (subprocess.CalledProcessError, FileNotFoundError) as exc:
                detail = getattr(exc, "stderr", None) or str(exc)
                raise ValueError(
                    f"Could not clone plugin repository: {detail.strip()[:300]}"
                ) from exc
            return self._install_dir(clone_dir)
        finally:
            shutil.rmtree(tmp_root, ignore_errors=True)

    def _install_from_marketplace(self, name: str) -> Path:
        import tempfile

        from navig.plugins.marketplace import MarketplaceStore, _is_git_url, fetch_marketplace

        resolved = MarketplaceStore().resolve(name)
        if resolved is None:
            raise ValueError(
                f"Plugin '{name}' not found — provide a local dir, a .zip, a Git URL, "
                "or a plugin from a marketplace (navig plugin marketplace add <url>)."
            )
        mkt, entry = resolved
        if _is_git_url(entry.source):
            return self._install_git(entry.source)
        if _is_git_url(mkt.url):
            import shutil

            clone_root = Path(tempfile.mkdtemp(prefix="navig-market-"))
            try:
                fetch_marketplace(mkt.url, workdir=clone_root)
                return self._install_dir((clone_root / entry.source).resolve())
            finally:
                shutil.rmtree(clone_root, ignore_errors=True)
        return self._install_dir((Path(mkt.url) / entry.source).resolve())


_host: PluginHost | None = None


def get_plugin_host() -> PluginHost:
    global _host
    if _host is None:
        _host = PluginHost()
    return _host
