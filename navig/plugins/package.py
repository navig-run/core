"""
Plugin/package loader — a **superset of a Claude Code plugin**.

Reads `.claude-plugin/plugin.json`, discovers the CC bundle (commands / agents /
skills / hooks / .mcp.json) AND the NAVIG-native block (personas / formations /
spaces), and returns a :class:`LoadedPackage` + a :class:`PluginHealth`.

Any single part that fails to load is **isolated** — recorded as a degraded
component, never fatal — so a broken part can't block boot or the rest of the
plugin (see `lifecycle.py`). A pure CC plugin (no `navig` block) loads unchanged;
a NAVIG package adds personas/formations/spaces on top. See `docs/plugin-spec.md`.

Discovery reuses the existing loaders (never reimplements them):
skills → `navig.skills.loader.parse_skill_file`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navig.plugins.lifecycle import PluginHealth, State

logger = logging.getLogger(__name__)


@dataclass
class LoadedPackage:
    plugin_id: str
    path: Path
    manifest: dict[str, Any]
    health: PluginHealth
    commands: list[Path] = field(default_factory=list)
    agents: list[Path] = field(default_factory=list)
    skills: list[Any] = field(default_factory=list)          # navig.skills.loader.Skill
    hooks: dict[str, Any] = field(default_factory=dict)
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    personas: list[Path] = field(default_factory=list)
    formations: list[Path] = field(default_factory=list)
    spaces: list[Path] = field(default_factory=list)

    @property
    def is_claude_compatible(self) -> bool:
        """True for a plain CC plugin (no NAVIG-native additions)."""
        return not (self.personas or self.formations or self.spaces)

    def summary(self) -> dict[str, int]:
        return {
            "commands": len(self.commands), "agents": len(self.agents),
            "skills": len(self.skills), "hooks": len(self.hooks),
            "mcp_servers": len(self.mcp_servers), "personas": len(self.personas),
            "formations": len(self.formations), "spaces": len(self.spaces),
        }


def _manifest_path(root: Path) -> Path | None:
    for cand in (root / ".claude-plugin" / "plugin.json", root / "plugin.json"):
        if cand.exists():
            return cand
    return None


def _plugin_id_of(root: Path) -> str:
    """Canonical plugin id for a dir = its manifest `name`, dir name as fallback.

    Disabled-state matching must key on the id (not the bare dir name), else a
    plugin whose *directory* is named like another plugin's *id* gets collaterally
    unwired (ids and dir names share one flat `plugins.disabled_plugins` pool).
    """
    mpath = _manifest_path(root)
    if mpath is not None:
        try:
            name = json.loads(mpath.read_text(encoding="utf-8")).get("name")
            if isinstance(name, str) and name:
                return name
        except Exception:  # noqa: BLE001
            pass
    return root.name


def disabled_plugin_ids() -> set[str]:
    """Plugin ids the user disabled (`plugins.disabled_plugins` in config).

    One state key for every plugin format — package dirs, pip entry-point
    plugins, and legacy Typer plugins all check the same list.
    """
    try:
        from navig.config import get_config_manager

        plugins = get_config_manager().global_config.get("plugins", {})
        disabled = plugins.get("disabled_plugins", [])
        return set(disabled) if isinstance(disabled, list) else set()
    except Exception:  # noqa: BLE001
        return set()


def installed_plugin_roots(*, include_disabled: bool = False) -> list[Path]:
    """Root dirs of installed CC/NAVIG packages under the user plugins dir.

    A package = a subdir carrying `.claude-plugin/plugin.json` (or `plugin.json`).
    Legacy `plugin.py` Typer plugins are ignored here — they self-register their
    own Typer app. Never raises; returns [] when the dir is missing/unreadable.
    This is the seam the live loaders (skills / personas / prompts / mcp) use to
    pick up a plugin's capabilities without reimplementing discovery. Disabled
    plugins are excluded so a single toggle unwires every capability at once.
    """
    try:
        from navig.core import Config

        plugins_dir = Config().plugins_dir
    except Exception:  # noqa: BLE001
        return []
    if not plugins_dir.exists():
        return []
    disabled = set() if include_disabled else disabled_plugin_ids()
    roots: list[Path] = []
    try:
        for child in sorted(plugins_dir.iterdir()):
            if not (child.is_dir() and _manifest_path(child) is not None):
                continue
            if disabled and _plugin_id_of(child) in disabled:
                continue
            roots.append(child)
    except Exception:  # noqa: BLE001
        return []
    return roots


def project_plugin_roots(cwd: Path | None = None, *, include_disabled: bool = False) -> list[Path]:
    """CC/NAVIG package roots under the active project's `.navig/plugins/`.

    Space-scoped capabilities: only surfaced when the containing space has been
    trusted (`trusted: true` in ~/.navig/spaces.json — one prompt per space,
    mirroring Claude Code project trust). Untrusted or undecided spaces expose
    nothing here. Never raises. Pass ``include_disabled=True`` for management
    surfaces (list/enable/remove) — otherwise a disabled project plugin would
    be unreachable and could never be re-enabled.
    """
    try:
        base = (cwd or Path.cwd()).resolve()
    except Exception:  # noqa: BLE001
        return []
    project_navig: Path | None = None
    for candidate in (base, *base.parents):
        if (candidate / ".navig").is_dir():
            project_navig = candidate / ".navig"
            break
    if project_navig is None:
        return []
    plugins_dir = project_navig / "plugins"
    if not plugins_dir.is_dir():
        return []
    try:
        from navig.spaces.registry import is_trusted

        if is_trusted(project_navig.parent) is not True:
            return []
    except Exception:  # noqa: BLE001
        return []
    disabled = set() if include_disabled else disabled_plugin_ids()
    roots: list[Path] = []
    try:
        for child in sorted(plugins_dir.iterdir()):
            if not (child.is_dir() and _manifest_path(child) is not None):
                continue
            if disabled and _plugin_id_of(child) in disabled:
                continue
            roots.append(child)
    except Exception:  # noqa: BLE001
        return []
    return roots


def installed_plugin_subdirs(kind: str) -> list[Path]:
    """`<plugin>/<kind>` dirs that exist across installed packages.

    e.g. `installed_plugin_subdirs("skills")` → every installed plugin's skills
    dir. Unions the user scope with the trusted project scope (space-carried
    plugins), so every loader picks both up through this one seam.
    """
    out: list[Path] = []
    for root in (*installed_plugin_roots(), *project_plugin_roots()):
        d = root / kind
        if d.is_dir():
            out.append(d)
    return out


def entry_point_plugin_capability_dirs(kind: str) -> list[Path]:
    """`<package>/<kind>` dirs bundled inside pip-installed (entry-point) plugins.

    First-party plugins register via the ``navig.plugins`` / ``navig.commands``
    entry points rather than as ``~/.navig/plugins/`` packages, so a capability
    dir they ship *inside* their Python package (e.g. ``navig_generate/skills/``)
    is invisible to :func:`installed_plugin_subdirs`. This resolves each entry
    point's top-level package location — without importing the plugin — and
    returns its ``<kind>/`` subdir when present, deduped. Disabled plugins are
    excluded (same toggle as the package seam). Never raises; a broken dist is
    skipped so discovery degrades to whatever resolved.
    """
    import importlib.util
    from importlib import metadata

    disabled = disabled_plugin_ids()
    seen: set[Path] = set()
    out: list[Path] = []
    for group in ("navig.plugins", "navig.commands"):
        try:
            eps = list(metadata.entry_points(group=group))
        except Exception:  # noqa: BLE001
            continue
        for ep in eps:
            try:
                dist = getattr(ep, "dist", None)
                dist_name = (getattr(dist, "name", None) or ep.name) if dist else ep.name
                if dist_name == "navig" or dist_name in disabled:
                    continue  # core never self-registers; honor the disable toggle
                top = ep.module.split(".", 1)[0]  # "navig_generate.plugin" -> "navig_generate"
                if not top or top == "navig":
                    continue
                spec = importlib.util.find_spec(top)
                origin = getattr(spec, "origin", None) if spec else None
                if not origin:
                    continue
                cap = Path(origin).parent / kind
                rc = cap.resolve()
                if cap.is_dir() and rc not in seen:
                    seen.add(rc)
                    out.append(cap)
            except Exception:  # noqa: BLE001 — a broken dist must not kill discovery
                continue
    return out


def plugin_capability_dirs(kind: str) -> list[Path]:
    """Every ``<plugin>/<kind>`` capability dir across BOTH plugin install shapes.

    Unions the package-format seam (:func:`installed_plugin_subdirs` — plugins
    under ``~/.navig/plugins`` or a trusted project) with pip entry-point plugins
    (:func:`entry_point_plugin_capability_dirs`), deduped by resolved path. This
    is the single seam the capability loaders (skills / personas / prompts /
    formations / spaces / agents) use, so a plugin's capabilities are picked up
    however it was installed.
    """
    seen: set[Path] = set()
    out: list[Path] = []
    for d in (*installed_plugin_subdirs(kind), *entry_point_plugin_capability_dirs(kind)):
        try:
            rd = d.resolve()
        except Exception:  # noqa: BLE001
            rd = d
        if rd not in seen:
            seen.add(rd)
            out.append(d)
    return out


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_package(path: str | Path) -> LoadedPackage:
    """Load a plugin/package directory. Never raises — a bad manifest yields a
    FAILED package; a bad part yields a DEGRADED component."""
    root = Path(path)
    mpath = _manifest_path(root)
    if mpath is None:
        h = PluginHealth(plugin_id=root.name, manifest_ok=False,
                         error="no .claude-plugin/plugin.json (or plugin.json)")
        return LoadedPackage(plugin_id=root.name, path=root, manifest={}, health=h)

    try:
        manifest = _read_json(mpath)
    except Exception as exc:  # noqa: BLE001
        h = PluginHealth(plugin_id=root.name, manifest_ok=False,
                         error=f"invalid manifest: {exc}")
        return LoadedPackage(plugin_id=root.name, path=root, manifest={}, health=h)

    plugin_id = str(manifest.get("name") or root.name)
    health = PluginHealth(plugin_id=plugin_id)
    pkg = LoadedPackage(plugin_id=plugin_id, path=root, manifest=manifest, health=health)

    # ── CC bundle: commands + agents (markdown files) ───────────────────────
    for kind, attr, sub in (("command", "commands", "commands"), ("agent", "agents", "agents")):
        d = root / sub
        if d.is_dir():
            for f in sorted(d.glob("*.md")):
                getattr(pkg, attr).append(f)
                health.add(f.stem, kind)

    # ── CC bundle: skills (reuse the existing SKILL.md loader) ───────────────
    sk_dir = root / "skills"
    if sk_dir.is_dir():
        from navig.skills.loader import parse_skill_file

        for sf in sorted(sk_dir.rglob("SKILL.md")):
            try:
                skill = parse_skill_file(sf)
            except Exception as exc:  # noqa: BLE001
                health.add(sf.parent.name, "skill", state=State.DEGRADED, error=str(exc)[:200])
                continue
            if skill is None:
                health.add(sf.parent.name, "skill", state=State.DEGRADED, error="unparseable SKILL.md")
            else:
                pkg.skills.append(skill)
                health.add(getattr(skill, "name", sf.parent.name), "skill")

    # ── CC bundle: hooks.json ───────────────────────────────────────────────
    for hp in (root / "hooks" / "hooks.json", root / "hooks.json"):
        if hp.exists():
            try:
                pkg.hooks = _read_json(hp)
                health.add("hooks", "hook")
            except Exception as exc:  # noqa: BLE001
                health.add("hooks", "hook", state=State.DEGRADED, error=str(exc)[:200])
            break

    # ── CC bundle: .mcp.json (mcpServers map, or top-level map) ──────────────
    mcp_path = root / ".mcp.json"
    if mcp_path.exists():
        try:
            data = _read_json(mcp_path)
            servers = data.get("mcpServers", data) if isinstance(data, dict) else {}
            for name, cfg in (servers or {}).items():
                pkg.mcp_servers[name] = cfg
                health.add(name, "mcp")
        except Exception as exc:  # noqa: BLE001
            health.add("mcp", "mcp", state=State.DEGRADED, error=str(exc)[:200])

    # ── NAVIG-native block: personas / formations / spaces ──────────────────
    navig = manifest.get("navig") or {}
    if isinstance(navig, dict):
        for key, attr, kind in (("personas", "personas", "persona"),
                                 ("formations", "formations", "formation"),
                                 ("spaces", "spaces", "space")):
            for rel in navig.get(key, []) or []:
                p = (root / rel)
                if p.exists():
                    getattr(pkg, attr).append(p)
                    health.add(p.name, kind)
                else:
                    health.add(str(rel), kind, state=State.DEGRADED, error="path not found")

    return pkg
