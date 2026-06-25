"""navig space — multi-context space management.

Spaces live under ~/.navig/spaces/<name>/ and let you maintain separate
environments (e.g. homelab, client-x, default) within a single NAVIG install.

Active space resolution order:
  1. NAVIG_SPACE environment variable  (CI / scripting override)
  2. ~/.navig/cache/active_space.txt   (persisted by ``navig space switch``)
  3. "default"                         (zero-config fallback)
"""

from __future__ import annotations

import os
import re
import shutil
from pathlib import Path

import typer
from rich.table import Table

from navig import console_helper as ch
from navig.config import get_config_manager
from navig.console_helper import get_console
from navig.core.yaml_io import atomic_write_text
from navig.spaces.kickoff import build_space_kickoff

# ── Typer app ─────────────────────────────────────────────────────────────────

space_app = typer.Typer(
    name="space",
    help="Manage NAVIG spaces (multi-context environments).",
    invoke_without_command=True,
    no_args_is_help=False,
)

_console = get_console()

# Slug: lowercase letters/digits/hyphens, must start with letter or digit
_SLUG_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,28}[a-z0-9])?$")
_BUILTIN_SPACES = ("default", "personal", "work", "focus", "studio")

_DEFAULT_INDEX_MD = """\
# My Space

> Capture how you work, what you’re focused on, and anything Navig should know about you.

## About Me
<!-- Who you are, how you work -->

## How I Use Navig
<!-- Workflow, preferred spaces, shortcuts -->

## Current Focus
<!-- Active projects, priorities, deadlines -->

## Quick Links
<!-- Pinned resources, frequent destinations -->
"""

_DEFAULT_VISION_MD = """# Vision

> What are you working toward?
"""

_DEFAULT_PHASE_MD = """# Current Phase

> What phase are you in right now?
"""

# ── Canonical space skeleton (init / new / create) ───────────────────────────
# ONE structure, produced by `navig space init` and mirrored by the
# navig-community example. Plans live in .navig/plans (root-linked to ./plans);
# inbox in .navig/inbox (root-linked to ./inbox). The .dev/.local/docs hygiene
# zones follow the repo-cleanup convention (.navig, .dev and .local are all
# gitignored — machine-local/private; only docs/ and source are committed).

_SKELETON_DIRS = (
    ".navig/plans",
    ".navig/inbox/docs",
    ".navig/inbox/shortcuts",
    ".navig/memory",
    ".navig/state",
    ".navig/wiki",
    # Capability dirs — the workshop's own skills/packages/agents/personas/rules.
    # `navig wire` junctions these under `.claude/` so Claude Code sees them.
    ".navig/skills",
    ".navig/packages",
    ".navig/personas",
    ".navig/agents",
    ".navig/rules",
    ".navig/brain/prompts",
    ".dev/reports", ".dev/logs", ".dev/audits", ".dev/prompts",
    ".dev/experiments", ".dev/archive", ".dev/notes", ".dev/screenshots", ".dev/temp",
    ".local/dumps", ".local/credentials", ".local/private-notes",
    ".local/machine-config", ".local/scratch",
    "docs/architecture", "docs/setup", "docs/operations",
    "docs/decisions", "docs/reference", "docs/archive",
)

# Root → .navig links (cross-platform: NTFS junction on Windows, symlink on POSIX)
_ROOT_LINKS = (("plans", ".navig/plans"), ("inbox", ".navig/inbox"))

# Legacy → canonical dotdir renames applied on init (singular is canonical).
_LEGACY_DOTDIR_RENAMES = ((".labs", ".lab"), (".backups", ".backup"))


def _migrate_legacy_dotdirs(space_path: Path, *, dry_run: bool = False) -> list[str]:
    """Rename legacy plural dotdirs to their canonical singular form.

    ``.labs`` → ``.lab``, ``.backups`` → ``.backup``. Merge-safe: when the
    canonical dir already exists, children are moved in — name collisions are
    left in the legacy dir and reported, never overwritten. Returns
    human-readable messages (empty when nothing to migrate).
    """
    msgs: list[str] = []
    for legacy, canonical in _LEGACY_DOTDIR_RENAMES:
        src = space_path / legacy
        if not src.is_dir():
            continue
        dest = space_path / canonical
        if not dest.exists():
            if not dry_run:
                src.rename(dest)
            msgs.append(f"{legacy}/ → {canonical}/")
            continue
        # canonical exists → merge children, leave collisions untouched
        moved = collisions = 0
        for child in list(src.iterdir()):
            target = dest / child.name
            if target.exists():
                collisions += 1
                continue
            if not dry_run:
                shutil.move(str(child), str(target))
            moved += 1
        if not dry_run:
            try:
                src.rmdir()  # only succeeds once empty — never force-deletes
            except OSError:
                pass
        tail = f", {collisions} kept in {legacy}/)" if collisions else ")"
        msgs.append(f"{legacy}/ merged into {canonical}/ ({moved} moved" + tail)
    return msgs

_SPACE_FILES: dict[str, str] = {
    ".navig/GENESIS.md": "# Genesis\n\nCreated with `navig space init`.\n",
    ".navig/plans/CURRENT_PHASE.md": "# Current Phase\n\n> What are you working on right now? Navig reads this first.\n",
    ".navig/plans/VISION.md": "# Vision\n\n> What are you working toward?\n",
    ".navig/plans/ROADMAP.md": "# Roadmap\n\n## Now\n\n## Next\n\n## Later\n",
    ".navig/plans/DEV_PLAN.md": "# Dev Plan\n\n## Active\n\n## Deferred / Later\n\n## After MVP\n",
    "docs/README.md": (
        "# Docs\n\nCurated documentation for this space.\n\n"
        "| Folder | Holds |\n|---|---|\n"
        "| architecture/ | system design, data flow |\n"
        "| setup/ | install & first-run |\n"
        "| operations/ | runbooks, maintenance |\n"
        "| decisions/ | ADRs, conventions |\n"
        "| reference/ | stable facts: commands, env, maps |\n"
        "| archive/ | deprecated / historical |\n\n"
        "> **Plans live in `.navig/plans/`** (linked to `./plans`), not here.\n"
    ),
    ".gitignore": (
        "# ── navig: machine-local / private — never commit ──\n"
        ".navig/\n.lab/\n.backup/\n.local/\n.dev/\n\n"
        "# ── build / cache / IDE artifacts ──\n"
        ".next/\n.open-next/\n.wrangler/\n.venv/\n.pytest_cache/\n"
        ".tmp/\n.core-sync-tmp/\n.idea/\n\n"
        "# ── logs & temp ──\n*.log\n\n"
        "# ── OS / editor junk ──\n.DS_Store\nThumbs.db\n"
    ),
}


def _scaffold_space_skeleton(
    space_path: Path, name: str, owner: str = "", *, dry_run: bool = False
) -> dict[str, list[str]]:
    """Create the canonical space structure — **purely additive, never destructive**.

    Guarantees:
      * An existing file is NEVER overwritten or truncated (left byte-for-byte).
      * An existing directory is reused, never replaced.
      * A path collision (a *file* sitting where a folder belongs, or vice-versa)
        is recorded as a conflict and skipped — never clobbered, never raises.
      * ``dry_run=True`` previews: computes the plan, writes nothing.

    Returns ``{"created": [...], "skipped": [...], "conflicts": [...]}`` (paths
    relative to the space root; created dirs end in ``/``).
    """
    import json
    from datetime import datetime, timezone

    created: list[str] = []
    skipped: list[str] = []
    conflicts: list[str] = []

    def _relpath(p: Path) -> str:
        try:
            return p.relative_to(space_path).as_posix()
        except ValueError:
            return str(p)

    def ensure_dir(d: Path) -> bool:
        """Guarantee *d* is a directory. Return False (and log a conflict) if an
        existing non-directory blocks it — never deletes anything to make room."""
        if d == space_path:
            if space_path.is_dir():
                return True
            if space_path.exists():  # a file occupies the space root → never clobber
                conflicts.append(f"{space_path} (a file exists where the space root is expected)")
                return False
            if not dry_run:  # creatable; dry-run just assumes it will be
                space_path.mkdir(parents=True, exist_ok=True)
            return True
        if not ensure_dir(d.parent):  # an ancestor file blocks the whole branch
            return False
        if d.is_dir():
            return True
        if d.exists():  # a file/symlink occupies the slot → refuse to clobber
            conflicts.append(f"{_relpath(d)}/  (a file exists where a folder is expected)")
            return False
        if not dry_run:
            d.mkdir(exist_ok=True)
        created.append(_relpath(d) + "/")
        return True

    def ensure_file(dest: Path, content: str) -> None:
        if dest.is_dir():
            conflicts.append(f"{_relpath(dest)}  (a folder exists where a file is expected)")
            return
        if dest.exists():  # user's file — leave it exactly as-is
            skipped.append(_relpath(dest))
            return
        if not ensure_dir(dest.parent):
            conflicts.append(f"{_relpath(dest)}  (parent path blocked)")
            return
        if not dry_run:
            atomic_write_text(dest, content)
        created.append(_relpath(dest))

    # 0) migrate legacy plural dotdirs → canonical singular (.labs→.lab, .backups→.backup)
    migrated = _migrate_legacy_dotdirs(space_path, dry_run=dry_run)

    # 1) directories
    for r in _SKELETON_DIRS:
        ensure_dir(space_path / r)

    # 2) template files (README/CLAUDE composed per-space)
    files = dict(_SPACE_FILES)
    files["README.md"] = (
        f"# {name}\n\nA NAVIG space.\n\n"
        "- **Plans:** `.navig/plans/` (→ `./plans`)\n"
        "- **Inbox:** `.navig/inbox/` (→ `./inbox`) — drop any file to capture it\n"
        "- **Dev artifacts:** `.dev/` · **Machine-local:** `.local/` (both gitignored)\n"
        "- **Docs:** `docs/`\n\n"
        f"Activate: `navig space switch {name}`\n"
    )
    files["CLAUDE.md"] = (
        f"# {name} — space guardrails\n\n"
        "Agents working here stay inside this space. Plans live in `.navig/plans/`,\n"
        "captured files in `.navig/inbox/`. Keep machine-local/private material in\n"
        "`.local/`; dev artifacts in `.dev/`. `.navig/`, `.dev/` and `.local/` are\n"
        "all gitignored — only `docs/` and source are committed.\n"
    )
    for r, content in files.items():
        ensure_file(space_path / r, content)

    # 3) JSON configs
    # Canonical first-class workshop manifest (space.json). This is what makes the
    # folder a real workshop the resolver/loader treats as a space — id, root,
    # formation, and the skills/packages/personas allow-lists. Read first by
    # space_manifest.load_space_manifest (MANIFEST_NAMES order).
    ensure_file(space_path / ".navig" / "space.json", json.dumps({
        "id": name,
        "display_name": name.replace("-", " ").replace("_", " ").title(),
        "version": "1.0.0",
        "description": "",
        "license": "UNLICENSED",
        "root": ".",
        "formation": None,
        "skills": [],
        "packages": [],
        "personas": [],
        "tools": [],
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, indent=2) + "\n")
    ensure_file(space_path / ".navig" / "space.config.json", json.dumps({
        "name": name, "version": "1.0.0", "description": "", "owner": owner,
        "packages": [],
        "plans": ".navig/plans", "inbox": ".navig/inbox",
        "memory": ".navig/memory", "state": ".navig/state", "wiki": ".navig/wiki",
        "created": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "crossplatform": True,
    }, indent=2) + "\n")
    ensure_file(space_path / ".navig" / "inbox.config.json", json.dumps({
        "version": "1.0.0", "inbox": ".navig/inbox",
        "docs": ".navig/inbox/docs", "shortcuts": ".navig/inbox/shortcuts",
        "crossplatform": True,
    }, indent=2) + "\n")

    # 4) .gitkeep in still-empty leaf dirs so git preserves the skeleton
    for r in _SKELETON_DIRS:
        d = space_path / r
        if d.is_dir():
            try:
                if not any(d.iterdir()):
                    ensure_file(d / ".gitkeep", "")
            except OSError:
                pass

    return {"created": created, "skipped": skipped, "conflicts": conflicts, "migrated": migrated}


def _link_space_roots(space_path: Path) -> list[str]:
    """Cross-platform root links: ./plans → .navig/plans, ./inbox → .navig/inbox.

    NTFS junctions on Windows (no admin needed), symlinks on POSIX. Best-effort.
    """
    from navig.commands.mount import _create_junction

    msgs: list[str] = []
    for link_name, rel_target in _ROOT_LINKS:
        link = space_path / link_name
        source = space_path / rel_target
        if link.exists() or link.is_symlink():
            msgs.append(f"skip {link_name} (exists)")
            continue
        err = _create_junction(source, link)
        msgs.append(f"{link_name} -> {rel_target}" if err is None else f"{link_name} FAILED: {err}")
    return msgs


# ── Internal helpers ──────────────────────────────────────────────────────────


def _spaces_dir(create: bool = True) -> Path:
    """Return ``~/.navig/spaces/``, creating it when *create* is ``True``."""
    d = Path(get_config_manager().global_config_dir) / "spaces"
    if create:
        d.mkdir(parents=True, exist_ok=True)
    return d


def _suggest_builtins() -> str:
    return "Tip: Common spaces \u2014 default (My Space), " + ", ".join(
        s for s in _BUILTIN_SPACES if s != "default"
    )


def _active_space_cache_file() -> Path:
    return Path(get_config_manager().global_config_dir) / "cache" / "active_space.txt"


def get_active_space() -> str:
    """Return the active space name.

    Respects the ``NAVIG_SPACE`` environment variable so CI/scripting callers
    can override without touching the local state file.
    """
    env = os.environ.get("NAVIG_SPACE", "").strip()
    if env:
        return env

    cache_file = _active_space_cache_file()
    if cache_file.exists():
        try:
            name = cache_file.read_text(encoding="utf-8").strip()
            if name:
                return name
        except OSError:
            pass  # best-effort: skip on IO error
    try:
        cfg = get_config_manager().global_config or {}
        if isinstance(cfg, dict):
            space_cfg = cfg.get("space", {})
            if isinstance(space_cfg, dict):
                name = str(space_cfg.get("active", "")).strip()
                if name:
                    return name

            name = str(cfg.get("active_space", "")).strip()
            if name:
                return name

            spaces_cfg = cfg.get("spaces", {})
            if isinstance(spaces_cfg, dict):
                name = str(spaces_cfg.get("active", "")).strip()
                if name:
                    return name
    except Exception:  # noqa: BLE001
        pass

    return "default"


def _set_active_space(name: str) -> None:
    """Persist *name* as the active space (cache file + best-effort config.yaml)."""
    cache_file = _active_space_cache_file()
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(cache_file, name)

    # Best-effort: mirror into ~/.navig/config.yaml so `navig config show` reflects it
    try:
        from navig.core.yaml_io import atomic_write_yaml

        cm = get_config_manager()
        gc = dict(cm.global_config)
        space_cfg = gc.get("space", {})
        if not isinstance(space_cfg, dict):
            space_cfg = {}
        space_cfg["active"] = name
        gc["space"] = space_cfg
        gc["active_space"] = name

        legacy_spaces = gc.get("spaces", {})
        if isinstance(legacy_spaces, dict):
            legacy_spaces.pop("active", None)
            if legacy_spaces:
                gc["spaces"] = legacy_spaces
            else:
                gc.pop("spaces", None)

        config_file = Path(cm.global_config_dir) / "config.yaml"
        atomic_write_yaml(gc, config_file, allow_unicode=True)
    except Exception:  # noqa: BLE001
        pass  # cache file is the source of truth; config.yaml update is best-effort


def _ensure_default_space() -> None:
    """Create ``~/.navig/spaces/default/`` and scaffold starter files on first use."""
    default_dir = _spaces_dir() / "default"
    default_dir.mkdir(parents=True, exist_ok=True)

    # Only write each file if it does not exist — never overwrite user content
    index_file = default_dir / "index.md"
    if not index_file.exists():
        atomic_write_text(index_file, _DEFAULT_INDEX_MD)

    vision_file = default_dir / "VISION.md"
    if not vision_file.exists():
        atomic_write_text(vision_file, _DEFAULT_VISION_MD)

    phase_file = default_dir / "CURRENT_PHASE.md"
    if not phase_file.exists():
        atomic_write_text(phase_file, _DEFAULT_PHASE_MD)


def _default_hint_file() -> Path:
    return Path(get_config_manager().global_config_dir) / "cache" / ".default_space_hint_shown"


def _maybe_show_default_hint() -> None:
    """Emit a one-time non-blocking prompt when the default space is still uncustomised."""
    hint_file = _default_hint_file()
    if hint_file.exists():
        return

    default_index = _spaces_dir(create=False) / "default" / "index.md"
    if not default_index.exists():
        return

    content = default_index.read_text(encoding="utf-8").strip()
    # Only show hint when file contains only the starter template (no user edits)
    if content and content == _DEFAULT_INDEX_MD.strip():
        ch.info(
            "This is your space \u2014 add context, goals, and notes so Navig works better for you.",
            details=f"Edit: navig file edit {default_index}",
        )
        try:
            hint_file.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_text(hint_file, "shown")
        except OSError:
            pass  # best-effort: skip on IO error


def _validate_slug(name: str) -> str:
    value = (name or "").strip().lower()
    if _SLUG_RE.match(value):
        return value
    raise typer.BadParameter(
        f"Invalid space name `{name}`. Use lowercase letters, digits, hyphens.\n{_suggest_builtins()}"
    )


# ── Default callback — `navig space` → `navig space list` ────────────────────


@space_app.callback()
def _space_callback(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        import os as _os  # noqa: PLC0415

        if _os.environ.get("NAVIG_LAUNCHER", "fuzzy") == "legacy":
            _space_list()
            raise typer.Exit()
        from navig.cli.launcher import smart_launch  # noqa: PLC0415

        smart_launch("space", space_app)


# ── Commands ──────────────────────────────────────────────────────────────────


@space_app.command("list")
def _space_list(
    show_all: bool = typer.Option(False, "--all", "-a", help="Include disabled spaces"),
) -> None:
    """List spaces across every root (with scope + enabled/active indicators)."""
    from navig.spaces import registry as space_registry  # noqa: PLC0415
    from navig.spaces.contracts import normalize_space_name  # noqa: PLC0415
    from navig.spaces.resolver import discover_space_paths  # noqa: PLC0415

    _ensure_default_space()
    active = get_active_space()
    active_canonical = normalize_space_name(active)
    spaces = discover_space_paths(include_disabled=True)

    if not spaces:
        ch.warning("No spaces found.", details="Run `navig space new <name>` to create one.")
        return

    ch.info(f"Active space: {active}")

    table = Table(box=None, show_header=False, padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column(style="dim")

    for canonical, cfg in sorted(spaces.items()):
        enabled = space_registry.is_enabled(cfg.path)
        if not enabled and not show_all:
            continue
        marker = "▸" if canonical == active_canonical else " "
        suffix = "" if enabled else " (disabled)"
        table.add_row(f"{marker} {canonical}{suffix}", f"[{cfg.scope}] {cfg.path}")

    _console.print(table)


@space_app.command("install")
def _space_install(
    spec: str = typer.Argument(
        ...,
        help="github:navig-run/community/spaces/<id>  or  space:owner/repo[@ref]",
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite if already installed."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing files."),
) -> None:
    """Install a space bundle from the community registry (GitHub-backed)."""
    from navig.commands.install import install_asset

    try:
        install_asset(spec, force=force, dry_run=dry_run, default_type="space")
    except (ValueError, SystemExit) as exc:
        raise typer.Exit(1) from exc


@space_app.command("new")
@space_app.command("create")
@space_app.command("init")
def space_create(
    name: str = typer.Argument(..., help="Space name — slug format: a-z0-9 and hyphens"),
    path: Path | None = typer.Option(
        None, "--path", "-p",
        help="Initialize at this directory instead of ~/.navig/spaces/<name> (e.g. D:\\spaces\\company).",
    ),
    no_links: bool = typer.Option(
        False, "--no-links", help="Skip the cross-platform root links (plans/, inbox/)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview exactly what would be created — write nothing."
    ),
) -> None:
    """Create/initialize a space.

    Scaffolds the canonical structure — ``.navig/{plans,inbox,memory,state,wiki}``
    plus the ``.dev/`` · ``.local/`` (both gitignored) · ``docs/`` hygiene
    zones — and links ``./plans`` → ``.navig/plans`` and ``./inbox`` →
    ``.navig/inbox`` (junction on Windows, symlink on POSIX).

    **Purely additive.** Safe to run on an existing project directory: it only
    adds what's missing and never overwrites, truncates, or deletes anything you
    already have. Use ``--dry-run`` to preview first.
    """
    name = _validate_slug(name)
    space_path = path.expanduser().resolve() if path else _spaces_dir() / name

    # Refuse to scaffold "into" a regular file — never clobber it.
    if space_path.exists() and not space_path.is_dir():
        ch.error(
            f"Cannot initialize space at {space_path}",
            details="A file already exists at that path. Choose another --path or remove it yourself.",
        )
        raise typer.Exit(1)

    existed = space_path.is_dir() and any(space_path.iterdir())

    if not dry_run:
        try:
            space_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            ch.error(f"Failed to create {space_path}", details=str(exc))
            raise typer.Exit(1) from exc

    summary = _scaffold_space_skeleton(space_path, name, dry_run=dry_run)
    link_msgs = [] if (no_links or dry_run) else _link_space_roots(space_path)

    # Register the workshop in the brain's index (~/.navig/spaces.json), enabled —
    # so it shows up in the deck + global switcher. A folder given via --path is
    # "external"; an unspecified path lives under ~/.navig/spaces (a "root" space).
    if not dry_run:
        try:
            from navig.spaces import registry as _registry  # noqa: PLC0415

            _registry.register(
                space_path, id=name, name=name,
                source="external" if path else "root", enabled=True,
            )
        except Exception:  # noqa: BLE001 — registry is best-effort, never block init
            pass

    # ── Report — show that existing content was left untouched ────────────────
    nc, ns, nx = len(summary["created"]), len(summary["skipped"]), len(summary["conflicts"])
    for m in summary.get("migrated", []):
        ch.info(f"  {'[dry-run] would migrate' if dry_run else 'migrated'}: {m}")
    if dry_run:
        ch.info(f"[dry-run] Would create {nc} item(s) in {space_path}; {ns} already present (kept).")
        for item in summary["created"]:
            ch.info(f"  + {item}")
    else:
        verb = "Initialized structure in existing" if existed else "Created"
        ch.success(f"{verb} space '{name}'.", details=str(space_path))
        ch.info(f"+{nc} created · {ns} existing left untouched · "
                ".navig/{plans,inbox,memory,state,wiki} · .dev/ · .local/ · docs/ "
                "(.navig/.dev/.local gitignored)")
        for m in link_msgs:
            ch.info(f"  link: {m}")

    if nx:
        ch.warning(
            f"{nx} path conflict(s) skipped — nothing was overwritten:",
            details="\n".join(summary["conflicts"]),
        )


@space_app.command("switch")
def space_switch(
    name: str = typer.Argument(..., help="Space name to activate"),
) -> None:
    """Activate a space — binds the agent's working directory to the workshop."""
    from navig.spaces import registry as space_registry  # noqa: PLC0415
    from navig.spaces.active import set_active_working_dir  # noqa: PLC0415
    from navig.spaces.resolver import discover_space_paths  # noqa: PLC0415
    from navig.spaces.space_manifest import load_space_manifest  # noqa: PLC0415

    name = _validate_slug(name)
    # Resolve the space across all roots (not just ~/.navig/spaces).
    cfg = discover_space_paths(include_disabled=True).get(name)
    space_path = cfg.path if cfg else _spaces_dir(create=False) / name
    if not space_path.exists():
        ch.error(
            f"Space '{name}' does not exist.",
            details=f"Run `navig space new {name}` to create it first.",
        )
        raise typer.Exit(1)

    # Parse the manifest → working dir (default = the space dir); bind + persist it.
    manifest = load_space_manifest(space_path)
    working_dir = (space_path / (manifest.root or ".")).resolve()
    _set_active_space(name)
    set_active_working_dir(working_dir)
    space_registry.ensure_registered(
        space_path, id=name, name=manifest.resolved_name or name,
        source=(cfg.scope if cfg else "global"),
    )
    space_registry.mark_active(space_path)

    ch.success(f"Active space: {name}", details=str(working_dir))

    if name == "default":
        _maybe_show_default_hint()

    kickoff = build_space_kickoff(name, space_path, cwd=Path.cwd(), max_items=3)
    if kickoff.actions:
        ch.info(f"Goal: {kickoff.goal}")
        ch.info("Top next actions:")
        for index, action in enumerate(kickoff.actions, start=1):
            ch.info(f"{index}. {action}")
    else:
        ch.info("No next actions found yet. Add tasks in CURRENT_PHASE.md or .navig/plans/*.md.")


@space_app.command("delete")
def space_delete(
    name: str = typer.Argument(..., help="Space name to delete"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Delete a space and all its contents."""
    name = _validate_slug(name)
    if name == "default":
        ch.error("Cannot delete the 'default' space.")
        raise typer.Exit(1)

    space_path = _spaces_dir(create=False) / name
    if not space_path.exists():
        ch.error(f"Space '{name}' does not exist.")
        raise typer.Exit(1)

    if not yes:
        confirmed = typer.confirm(
            f"Delete space '{name}' at {space_path}? This cannot be undone.",
            default=False,
        )
        if not confirmed:
            ch.info("Aborted.")
            raise typer.Exit()

    try:
        shutil.rmtree(space_path)
    except OSError as exc:
        ch.error(f"Failed to delete space '{name}'.", details=str(exc))
        raise typer.Exit(1) from exc

    ch.success(f"Deleted space '{name}'.")

    # If the deleted space was active, fall back to default
    try:
        cached = _active_space_cache_file().read_text(encoding="utf-8").strip()
    except OSError:
        cached = ""
    if cached == name:
        _set_active_space("default")
        ch.info("Active space reset to 'default'.")


@space_app.command("current")
def space_current() -> None:
    """Show the active space (NAVIG_SPACE override respected)."""
    _ensure_default_space()
    active = get_active_space()
    label = "My Space (default)" if active == "default" else active
    ch.info(f"Active space: {label}")
    if active == "default":
        _maybe_show_default_hint()


@space_app.command("use")
def space_use(
    name: str = typer.Argument(..., help="Space name to activate"),
) -> None:
    """Compatibility alias for `navig space switch <name>`."""
    space_switch(name)


# ── Registry: enable / disable / register / forget ───────────────────────────


@space_app.command("enable")
def space_enable(name: str = typer.Argument(..., help="Space name or path to enable")) -> None:
    """Make a space visible in the deck/switcher and available to activate."""
    from navig.spaces import registry as space_registry  # noqa: PLC0415

    if space_registry.set_enabled(name, True):
        ch.success(f"Enabled space '{name}'.")
    else:
        ch.warning(f"'{name}' is not registered.", details="Run `navig space register <path>` first.")


@space_app.command("disable")
def space_disable(name: str = typer.Argument(..., help="Space name or path to disable")) -> None:
    """Hide a space from the deck/switcher (the folder still works when you're in it)."""
    from navig.spaces import registry as space_registry  # noqa: PLC0415

    if space_registry.set_enabled(name, False):
        ch.success(f"Disabled space '{name}'.")
    else:
        ch.warning(f"'{name}' is not registered.")


@space_app.command("register")
def space_register(
    path: Path = typer.Argument(..., help="Path to a folder with a .navig/ (a workshop)"),
) -> None:
    """Register an external `.navig/` folder so it shows in the deck (enabled)."""
    from navig.spaces import registry as space_registry  # noqa: PLC0415
    from navig.spaces.contracts import normalize_space_name  # noqa: PLC0415
    from navig.spaces.space_manifest import is_space_dir, load_space_manifest  # noqa: PLC0415

    target = path.expanduser().resolve()
    if not target.is_dir() or not is_space_dir(target):
        ch.error(f"Not a space: {target}", details="A space is a folder containing a .navig/ directory.")
        raise typer.Exit(1)
    manifest = load_space_manifest(target)
    sid = normalize_space_name(manifest.resolved_id or target.name)
    entry = space_registry.register(
        target, id=sid, name=manifest.resolved_name or target.name, source="external", enabled=True
    )
    ch.success(f"Registered space '{entry['id']}' (enabled).", details=str(target))


@space_app.command("forget")
def space_forget(name: str = typer.Argument(..., help="Space name or path to forget")) -> None:
    """Remove a space from the registry (does not delete the folder)."""
    from navig.spaces import registry as space_registry  # noqa: PLC0415

    if space_registry.forget(name):
        ch.success(f"Forgot space '{name}' (folder left intact).")
    else:
        ch.warning(f"'{name}' is not registered.")


# Backward-compatible function name used by tests/importers.
space_new = space_create
