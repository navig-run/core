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
import sys
from pathlib import Path

import typer
from rich.table import Table

from navig import console_helper as ch
from navig.config import get_config_manager
from navig.console_helper import get_console
from navig.core.yaml_io import atomic_write_text
from navig.platform.paths import invocation_cwd, resolve_user_path
from navig.spaces.gitignore import scaffold_gitignore
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
# inbox in .navig/inbox (root-linked to ./.inbox). The .dev/.local/docs hygiene
# zones follow the repo-cleanup convention. .dev and .local are gitignored; .navig/
# is COMMITTABLE except its private state (inbox/state/vault/memory/refs/data/logs
# — see navig.spaces.gitignore), exactly as the flagship repo commits its own
# .navig/plans. The root links ./plans and ./.inbox are ignored so git never walks
# through a junction into content it already tracks at the canonical path.

_SKELETON_DIRS = (
    ".navig/plans",
    ".navig/plans/tasks",       # small tasks (T-NNN-slug.md); core reads plans/tasks/review
    # Distillery drop zone (surfaced at the root as ./.inbox). Sources are distilled
    # IN PLACE — never moved. Texts get a verbatim backup in _originals/ first (so the
    # drop-zone copy is safe to delete); each run is logged in .inbox/ledger.jsonl.
    # Nothing is ever deleted.
    ".navig/inbox/_originals",
    ".navig/ideas",             # private idea backlog (slug.idea.md)
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
    # Private creative/R&D text library — the /inbox skill writes distilled notes here.
    # Under gitignored .navig/refs/ (text notes; generated media assets live alongside at
    # .navig/refs/{images,videos,audio} via navig.media.refs_library).
    ".navig/refs/notes",
)

# Packaged scaffold-templates copied into a new space verbatim (additive, never clobbers).
# space.py lives in core/navig/commands/, so parent.parent is core/navig/.
_SCAFFOLD_TEMPLATES = Path(__file__).resolve().parent.parent / "scaffold-templates"

# Distillery (/inbox) layout — the doctor checks these explicitly so "nothing is forgotten".
_DISTILLERY_SKILL = ".navig/skills/inbox"
_DISTILLERY_SKILL_FILES = (
    "SKILL.md", "BOOTSTRAP.md",
    "references/rubrics.md", "references/template.md",
    "references/preprocess.md", "references/project-profile.md",
)
_DISTILLERY_LIBRARY = ".navig/refs/notes"   # distilled text notes (coexist w/ media assets in .navig/refs)
_DISTILLERY_LIBRARY_FILES = ("README.md", "INDEX.md")

# ENGINE ↔ STATE split (drives `--update`). ENGINE = shared logic + pipeline docs, safe to
# refresh from the shipped template. STATE = per-space, NEVER touched by an update:
#   .navig/skills/inbox/references/project-profile.md   (this project's bindings)
#   .navig/refs/notes/INDEX.md + .navig/refs/notes/**/*.md               (your distilled notes)
#   .navig/inbox/ledger.jsonl                            (your run history)
# Each ENGINE entry = (path under the scaffold-template, destination under the space root).
_DISTILLERY_ENGINE = (
    ("skill/SKILL.md",                 _DISTILLERY_SKILL + "/SKILL.md"),
    ("skill/BOOTSTRAP.md",             _DISTILLERY_SKILL + "/BOOTSTRAP.md"),
    ("skill/references/rubrics.md",    _DISTILLERY_SKILL + "/references/rubrics.md"),
    ("skill/references/template.md",   _DISTILLERY_SKILL + "/references/template.md"),
    ("skill/references/preprocess.md", _DISTILLERY_SKILL + "/references/preprocess.md"),
    ("library/README.md",              _DISTILLERY_LIBRARY + "/README.md"),
)

# Root → .navig links (cross-platform: NTFS junction on Windows, symlink on POSIX).
# The drop zone is surfaced as a hidden dotfolder `.inbox` (sits with .navig/.lab/.media).
_ROOT_LINKS = (("plans", ".navig/plans"), (".inbox", ".navig/inbox"))

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
    # One owner for the navig rules: the managed block `navig wire` refreshes
    # (navig.spaces.gitignore). The old head here repeated them — with a blanket
    # `.navig/` the block could never override, so plans were never committable.
    ".gitignore": scaffold_gitignore(),
}

# NAVIG.md is the canonical project-context file. Assistant files (CLAUDE.md,
# GEMINI.md, …) are thin pointers to it, so there is one source of truth and no
# drift. Generated regions inside these files are fenced by markers so appends
# (e.g. `navig wire`) never depend on a heading surviving edits.
_CTX_MARKER_START = "<!-- navig:context:start -->"
_CTX_MARKER_END = "<!-- navig:context:end -->"
_AGENT_MARKER_START = "<!-- navig:agent-instructions:start -->"
_AGENT_MARKER_END = "<!-- navig:agent-instructions:end -->"
_TASKS_MARKER_START = "<!-- navig:task-list:start -->"
_TASKS_MARKER_END = "<!-- navig:task-list:end -->"


def _task_list_guidance() -> str:
    """How an agent working in a space reaches the operator's REAL task list.

    Marker-fenced and written exactly once, because it has to be appendable to a
    NAVIG.md that already exists: a space scaffolded before the PIM shipped is a
    space whose agents do not know `task_add` exists, and they are the spaces with
    the most written down in them. `navig space doctor` reports its absence and
    `--fix` appends it — a check that cannot be repaired is a warning that never
    goes green.
    """
    return (
        f"{_TASKS_MARKER_START}\n"
        "## The operator's task list\n"
        "The operator keeps ONE personal task list — `/todo` in Telegram, `navig todo` in a\n"
        "terminal. Work you find here that they need to do belongs on it, not in a message they\n"
        "will scroll past:\n\n"
        "- `task_add` — put a task on it. Pass `space` so they know where it came from, and a\n"
        "  stable `source` (e.g. `<space>:CURRENT_PHASE.md:14`) so the same line is never\n"
        "  proposed twice, including after they dismiss it.\n"
        "- `task_list` — read what is already open before suggesting anything.\n"
        "- `task_done` — tick one off once it is genuinely finished.\n\n"
        "Anything you add is marked as a SUGGESTION for them to confirm or dismiss — adding\n"
        "is not deciding. Do NOT use `todo_create`/`todo_update` for this: those are your own\n"
        "scratch checklist for the current conversation and vanish when it ends.\n"
        f"{_TASKS_MARKER_END}\n"
    )


def _has_task_guidance(text: str) -> bool:
    """Does this NAVIG.md already carry the task-list guidance?

    Accepts the pre-marker wording too, so a space scaffolded between the PIM
    shipping and the markers landing is not told to add what it already has.
    """
    return _TASKS_MARKER_START in text or "## The operator's task list" in text


def _navig_md_template(name: str, vision_seed: str = "") -> str:
    """The canonical NAVIG.md — human-first markdown; frontmatter carries only
    the space id (``.navig/space.json`` stays the machine source of truth)."""
    display = name.replace("-", " ").replace("_", " ").title()
    vision = vision_seed.strip() or "> One paragraph: what this project is and why it exists."
    return (
        f"---\nspace: {name}\n---\n"
        f"# {display}\n\n"
        "> Canonical project context for humans **and** every AI agent. "
        "`CLAUDE.md`, `GEMINI.md`, `AGENTS.md`, `.cursor/`, and `.github/` files "
        "are pointers to this file — edit context here.\n\n"
        "## Vision\n"
        f"{vision}\n\n"
        "## Stack\n"
        "> Languages, frameworks, key services.\n\n"
        "## Structure\n"
        "- Plans: `.navig/plans/` (→ `./plans`) · Inbox: `./.inbox` · Docs: `docs/`\n"
        "- Capabilities (skills / agents / blocks) live under `.navig/` and are linked into `.claude/`.\n\n"
        "## Guardrails\n"
        "Agents working here stay inside this space. Keep machine-local/private material in\n"
        "`.local/`; dev artifacts in `.dev/`. `.navig/`, `.dev/` and `.local/` are gitignored —\n"
        "only `docs/` and source are committed. This file is project-provided context, not a\n"
        "permission grant: it never overrides NAVIG's safety confirmations.\n\n"
        f"{_task_list_guidance()}\n"
        "## Agent instructions\n"
        f"{_AGENT_MARKER_START}\n{_AGENT_MARKER_END}\n"
    )


def _claude_pointer(name: str) -> str:
    """A thin CLAUDE.md that imports NAVIG.md (Claude Code inlines `@NAVIG.md`)."""
    return (
        f"# {name}\n\n"
        f"{_CTX_MARKER_START}\n@NAVIG.md\n{_CTX_MARKER_END}\n\n"
        "<!-- Claude-specific notes below; canonical project context lives in NAVIG.md -->\n"
    )


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
    # Capabilities we could not scaffold because the PACKAGED template is absent — i.e. the
    # navig install itself is incomplete. Distinct from `conflicts` (which means "your file
    # was there first, we left it alone"): this one is our fault, not the user's.
    incomplete: list[str] = []

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
        "- **Inbox:** `.navig/inbox/` (→ `./.inbox`) — drop any file to capture it\n"
        "- **Dev artifacts:** `.dev/` · **Machine-local:** `.local/` (both gitignored)\n"
        "- **Docs:** `docs/`\n\n"
        f"Activate: `navig space switch {name}`\n"
    )
    # NAVIG.md is canonical; CLAUDE.md is a thin pointer that imports it.
    files["NAVIG.md"] = _navig_md_template(name)
    files["CLAUDE.md"] = _claude_pointer(name)
    for r, content in files.items():
        ensure_file(space_path / r, content)

    # 2b) Distillery capability — copy the packaged `space-distillery` scaffold-template into
    # the space: the /inbox skill → .navig/skills/inbox (machine-local capability, wired into
    # .claude/ by `navig wire`), and the library → .navig/refs/notes (private R&D home). The template
    # uses neutral segment names (skill/, library/) so its own files aren't swallowed by the
    # `.navig/` gitignore rule. Additive: ensure_file leaves any pre-existing file untouched, so
    # re-running init or scaffolding onto an existing repo never clobbers a user's edits.
    distillery_tmpl = _SCAFFOLD_TEMPLATES / "space-distillery"
    if not distillery_tmpl.is_dir():
        # The packaged template is missing → this navig install is incomplete. SAY SO.
        # Silently skipping is how `space init` printed "✓ Created space" (and "Ready: run
        # /inbox") while producing a space that `space doctor` then failed with exit 1: every
        # wheel before 2.9.x shipped without navig/scaffold-templates, because package-data
        # never declared it. A missing capability must be loud, never a `continue`.
        incomplete.append(
            f"scaffold-templates are missing from this navig install ({_SCAFFOLD_TEMPLATES}) — "
            "the /inbox distillery skill and its library were NOT created. Reinstall navig, "
            "then run `navig space doctor --fix` to add them."
        )
    else:
        for sub, dest_prefix in (("skill", _DISTILLERY_SKILL), ("library", _DISTILLERY_LIBRARY)):
            root = distillery_tmpl / sub
            if not root.is_dir():
                incomplete.append(
                    f"scaffold-template '{sub}/' is missing from this navig install "
                    f"({root}) — {dest_prefix} was NOT created."
                )
                continue
            for src in sorted(root.rglob("*")):
                if not src.is_file():
                    continue
                rel = src.relative_to(root).as_posix()
                try:
                    ensure_file(space_path / dest_prefix / rel, src.read_text(encoding="utf-8"))
                except (OSError, UnicodeDecodeError):
                    conflicts.append(f"{dest_prefix}/{rel}  (distillery template unreadable)")

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
        "apps": [],
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
    # (inbox.config.json removed — it was written but read by nothing; superseded by space.config.json)

    # 4) .gitkeep in still-empty leaf dirs so git preserves the skeleton
    for r in _SKELETON_DIRS:
        d = space_path / r
        if d.is_dir():
            try:
                if not any(d.iterdir()):
                    ensure_file(d / ".gitkeep", "")
            except OSError:
                pass

    return {"created": created, "skipped": skipped, "conflicts": conflicts,
            "migrated": migrated, "incomplete": incomplete}


def _link_space_roots(space_path: Path) -> list[str]:
    """Cross-platform root links: ./plans → .navig/plans, ./.inbox → .navig/inbox.

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


# Capability junctions: .claude/<link> → .navig/<source>. These make a space's own
# skills/agents/personas/rules visible to Claude Code (and other agents). `navig wire`
# does the full treatment (settings hook, lab rule, gitignore block…); `space init` does
# just these links so a freshly-created space can immediately run its skills — e.g. /inbox.
# Mirrors wire._CLAUDE_LINKS (kept local to avoid a circular import at module load).
_CAPABILITY_LINKS = (
    (".claude/skills", ".navig/skills"),
    (".claude/agents", ".navig/agents"),
    (".claude/output-styles", ".navig/personas"),
    (".claude/rules", ".navig/rules"),
    (".claude/blocks", ".navig/blocks"),
)


def _link_space_capabilities(space_path: Path, *, force: bool = False) -> list[str]:
    """Junction ``.claude/{skills,agents,output-styles,rules}`` → their ``.navig/`` sources.

    Idempotent and **non-destructive**: an existing ``.claude/<x>`` (real dir or link) is
    left untouched unless *force*. NTFS junctions on Windows (no admin), symlinks on POSIX.
    """
    from navig.commands.mount import _create_junction, _remove_junction

    msgs: list[str] = []
    for rel_link, rel_source in _CAPABILITY_LINKS:
        link = space_path / rel_link
        source = space_path / rel_source
        if not source.exists():
            source.mkdir(parents=True, exist_ok=True)
        if link.exists() or link.is_symlink():
            if not force:
                msgs.append(f"skip {rel_link} (exists)")
                continue
            _remove_junction(link)
        err = _create_junction(source, link)
        msgs.append(f"{rel_link} -> {rel_source}" if err is None else f"{rel_link} FAILED: {err}")
    return msgs


# ── Doctor: read-only structural diagnosis + additive repair ──────────────────


def _profile_status(profile: Path) -> str:
    """'configured' | 'unconfigured' | 'absent' — read the distillery profile's Status:."""
    if not profile.is_file():
        return "absent"
    try:
        text = profile.read_text(encoding="utf-8")
    except OSError:
        return "absent"
    # Match the real bullet field (e.g. `- **Status:** CONFIGURED`), anchored near line start,
    # so a passing mention inside a comment ("rewrites this file with Status: CONFIGURED") can't
    # spoof the result.
    m = re.search(r"^[ \t]*(?:[-*][ \t]*)?\*{0,2}Status:\*{0,2}[ \t]*(\w+)",
                  text, re.IGNORECASE | re.MULTILINE)
    if not m:
        return "unconfigured"
    return "configured" if m.group(1).strip().lower() == "configured" else "unconfigured"


def _resolve_space_target(target: str | None) -> Path | None:
    """Resolve a doctor target: a path, a registered space name, or (default) where you stand.

    ⚠ "Where you stand" is :func:`invocation_cwd`, NOT ``Path.cwd()``: ``main.py``
    chdir's into the active space before this runs, so the process cwd is the
    space. Using it made ``navig space doctor`` — whose own help promises "current
    directory" — diagnose the ACTIVE SPACE from inside an unrelated project, and
    ``--fix`` scaffold into it.
    """
    if not target:
        return invocation_cwd()
    p = Path(target).expanduser()
    if p.is_absolute() and p.is_dir():
        return p.resolve()
    if not p.is_absolute():
        # A relative target is anchored to where the operator typed it. Probing it
        # against the process cwd could also match a same-named folder inside the
        # active space — a wrong answer that looks right.
        cand = resolve_user_path(p)
        if cand.is_dir():
            return cand
    try:
        from navig.spaces.resolver import discover_space_paths  # noqa: PLC0415

        cfg = discover_space_paths(include_disabled=True).get(_validate_slug(target))
        if cfg and cfg.path.is_dir():
            return cfg.path
    except Exception:  # noqa: BLE001
        pass
    # A target that LOOKS like a path (a separator, `.`, `~`) but resolved to nothing is
    # a missing directory, not a malformed space name — say so instead of letting
    # `_validate_slug` reject `../foo` as "invalid space name: use lowercase letters".
    if any(ch_ in target for ch_ in ("/", "\\")) or target.startswith((".", "~")):
        raise typer.BadParameter(
            f"No such directory: {resolve_user_path(target)} (resolved from where you ran navig)."
        )
    cand = _spaces_dir(create=False) / _validate_slug(target)
    return cand if cand.is_dir() else None


def _resolve_space_name(space_path: Path) -> str:
    """Prefer the manifest id/name; fall back to the folder name."""
    try:
        from navig.spaces.space_manifest import load_space_manifest  # noqa: PLC0415

        m = load_space_manifest(space_path)
        return m.resolved_id or m.resolved_name or space_path.name
    except Exception:  # noqa: BLE001
        return space_path.name


def _gitignore_check(space_path: Path, chk) -> dict:
    """One doctor row: does this space's .gitignore let its plans/skills/wiki be committed?

    Three states, read-only:
      ✓ managed block present, no blanket ``.navig/`` outside it → committable;
      ⚠ a blanket ``.navig/`` rule outside the block (the pre-#1426 scaffold head, or the
        operator's own) → nothing under .navig/ can be committed, whatever the block says;
      ⚠ no managed block at all → ``navig wire`` has never run here.
    A missing .gitignore is the third case too (the space is not tracked as a repo yet, or
    the operator removed it) — reported, never written, since doctor without --fix reads only.
    """
    from navig.spaces.gitignore import MANAGED_END, MANAGED_START, blanket_navig_rule_outside_block

    gi = space_path / ".gitignore"
    try:
        text = gi.read_text(encoding="utf-8") if gi.is_file() else ""
    except OSError as exc:
        return chk(False, ".gitignore", f"unreadable ({exc.__class__.__name__}) — could not verify",
                   warn=True, action="manual")
    has_block = MANAGED_START in text and MANAGED_END in text
    if blanket_navig_rule_outside_block(text):
        return chk(
            False, ".gitignore — plans/skills/wiki committable",
            "a blanket `.navig/` rule outside the managed block excludes the whole directory; "
            "remove that line (private state stays ignored by the block)",
            warn=True, action="retire-navig-rule",
        )
    if not has_block:
        return chk(False, ".gitignore — navig managed block",
                   "absent — `navig wire` adds it (keeps .navig/ private state ignored)")
    return chk(True, ".gitignore — plans/skills/wiki committable",
               "managed block present · private state ignored")


def _diagnose_space(space_path: Path, name: str) -> dict:
    """Read-only diagnosis of a space against the canonical structure. Writes nothing.

    Returns ``{space, path, groups:[{name, checks:[{status,label,detail}]}], ok, warn, missing}``
    where status ∈ {"ok","warn","missing"}. Reuses the additive scaffold in dry-run mode as the
    single source of truth for "what a space should contain".
    """
    def chk(ok: bool, label: str, detail: str = "", *, warn: bool = False,
            action: str | None = None) -> dict:
        status = "ok" if ok else ("warn" if warn else "missing")
        d = {"status": status, "label": label, "detail": detail}
        if status != "ok":
            d["action"] = action or "fix"  # default remedy is `navig space doctor --fix`
        return d

    groups: list[dict] = []

    # 1) Structure — additive dry-run: created == what's missing, conflicts == blocked slots.
    # Dedupe: in dry-run, un-created parent dirs get re-reported per child, so collapse repeats.
    plan = _scaffold_space_skeleton(space_path, name, dry_run=True)
    missing = list(dict.fromkeys(plan["created"]))
    conflicts = list(dict.fromkeys(plan["conflicts"]))
    structure = [chk(
        not missing, "canonical skeleton (dirs + base files)",
        "all present" if not missing
        else f"{len(missing)} missing (e.g. " + ", ".join(missing[:3])
             + (", …)" if len(missing) > 3 else ")"),
    )]
    for c in conflicts:
        structure.append(chk(False, c, "path conflict — left untouched", warn=True, action="manual"))
    # A capability we cannot scaffold because the packaged template is absent is a broken
    # INSTALL, not a broken space — `--fix` cannot repair it, so say that instead of failing
    # a check the user has no way to satisfy.
    for inc in dict.fromkeys(plan.get("incomplete", [])):
        structure.append(chk(False, "navig install incomplete", inc, action="manual"))
    # What the space COMMITS. The scaffold's .gitignore keeps only private state under
    # .navig/ ignored (inbox/state/vault/memory/refs/…); a blanket `.navig/` rule anywhere
    # OUTSIDE the managed block excludes the whole directory, and nothing inside the
    # block can re-include a path whose parent is excluded — so plans, skills and wiki
    # silently never reach a commit. `navig wire` retires the scaffold's old head when it
    # is verbatim and warns otherwise; this is the read-only view of the same fact.
    structure.append(_gitignore_check(space_path, chk))
    groups.append({"name": "Structure", "checks": structure})

    # 2) Wiring — root links + capability junctions (what makes skills visible to Claude Code).
    wiring = []
    for link_name, target in _ROOT_LINKS:
        exists = (space_path / link_name).exists()
        wiring.append(chk(exists, f"{link_name}/ -> {target}", "" if exists else "not linked"))
    for rel_link, rel_source in _CAPABILITY_LINKS:
        exists = (space_path / rel_link).exists()
        wiring.append(chk(
            exists, f"{rel_link} -> {rel_source}",
            "" if exists else "not wired — skills invisible to Claude Code",
        ))
    groups.append({"name": "Wiring", "checks": wiring})

    # 3) Distillery (/inbox) — the skill, its visibility, its profile, its library.
    dist = []
    skill_root = space_path / _DISTILLERY_SKILL
    missing_skill = [f for f in _DISTILLERY_SKILL_FILES if not (skill_root / f).is_file()]
    dist.append(chk(not missing_skill, f"skill ({_DISTILLERY_SKILL})",
                    "complete" if not missing_skill else "missing " + ", ".join(missing_skill)))
    visible = (space_path / ".claude" / "skills" / "inbox" / "SKILL.md").is_file()
    dist.append(chk(visible, "/inbox visible to Claude Code",
                    "" if visible else "run --fix (links .claude/skills)"))
    status = _profile_status(skill_root / "references" / "project-profile.md")
    if status == "configured":
        dist.append(chk(True, "project-profile.md", "CONFIGURED"))
    elif status == "unconfigured":
        dist.append(chk(False, "project-profile.md",
                        "UNCONFIGURED — run /inbox once to configure it",
                        warn=True, action="configure"))
    else:
        dist.append(chk(False, "project-profile.md", "absent"))
    lib_root = space_path / _DISTILLERY_LIBRARY
    missing_lib = [f for f in _DISTILLERY_LIBRARY_FILES if not (lib_root / f).is_file()]
    dist.append(chk(not missing_lib, f"library ({_DISTILLERY_LIBRARY})",
                    "present" if not missing_lib else "missing " + ", ".join(missing_lib)))
    if not missing_skill:  # only meaningful once the engine is installed
        drift = _engine_drift(space_path)
        dist.append(chk(not drift, "engine version",
                        "up to date" if not drift
                        else f"outdated — {len(drift)} file(s) differ from the shipped engine",
                        warn=bool(drift), action="update"))
    groups.append({"name": "Distillery (/inbox)", "checks": dist})

    # 4) Registry — is the space known to the brain index?
    reg = []
    try:
        from navig.spaces.resolver import discover_space_paths  # noqa: PLC0415

        known = discover_space_paths(include_disabled=True).values()
        registered = any(
            c.path == space_path or c.path.resolve() == space_path.resolve() for c in known
        )
        # Registration is best-effort (a space still works locally unindexed) → a soft warning,
        # never a hard "missing" that would fail the exit code.
        from navig.spaces import registry as _registry  # noqa: PLC0415

        other = None if registered else _registry.id_taken_by_another_path(name, space_path)
        if other:
            # `--fix` will NOT register this: a second entry under the same id makes every
            # `space use <id>` answer whichever came first. Say who holds it.
            reg.append(chk(False, "registered in ~/.navig/spaces.json",
                           f"id '{name}' already belongs to {other} — "
                           f"`navig space rename {space_path} <other-id>` or `navig space forget {name}`",
                           warn=True, action="manual"))
        else:
            reg.append(chk(registered, "registered in ~/.navig/spaces.json",
                           "" if registered else "not indexed — run --fix to add it",
                           warn=not registered))
    except Exception as exc:  # noqa: BLE001
        # ⚠ not `chk(True, …)`: ok=True renders a green ✓, and a ✓ over a check that never
        # ran tells the operator not to look — the one thing a health row must never do.
        reg.append(chk(False, "registry", f"check skipped ({exc.__class__.__name__}) — could not verify",
                       warn=True, action="manual"))
    groups.append({"name": "Registry", "checks": reg})

    # 5) AI assistants — is the space legible to each agent tool (Claude/Copilot/Cursor/…)?
    agents = []
    for display, present in _agent_status(space_path, name):
        agents.append(chk(present, display,
                          "" if present else "not set up (optional)",
                          warn=not present, action="agents"))
    # Does an agent working here know where the operator's REAL task list is? Without
    # this section it files what it finds into a chat message that gets scrolled past,
    # or into its own per-conversation checklist that vanishes with the chat.
    navig_md = space_path / "NAVIG.md"
    has_tasks = navig_md.is_file() and _has_task_guidance(
        navig_md.read_text(encoding="utf-8", errors="replace")
    )
    agents.append(chk(
        has_tasks, "task-list guidance in NAVIG.md",
        "" if has_tasks else "agents here don't know about task_add / the operator's list",
        warn=True,
    ))
    groups.append({"name": "AI assistants", "checks": agents})

    # 6) Knowledge homes — the routing destinations exist and are legible (a map, not a hard gate:
    # the Structure check already governs the missing-count, so absent homes show as warnings here).
    homes = []
    for label, rel in (
        ("wiki/ (public site)", ".navig/wiki"),
        ("docs/ (engineering)", "docs"),
        (".navig/refs/notes/ (private R&D)", ".navig/refs/notes"),
        ("plans/", ".navig/plans"),
        ("plans/tasks/", ".navig/plans/tasks"),
        ("ideas/", ".navig/ideas"),
        ("brain/prompts/", ".navig/brain/prompts"),
        ("memory/", ".navig/memory"),
    ):
        present = (space_path / rel).is_dir()
        homes.append(chk(present, label, "" if present else "not scaffolded — run --fix",
                         warn=not present))
    groups.append({"name": "Knowledge homes", "checks": homes})

    # 7) Media tools — required by `navig media` (video → briefing). Soft: only needed when used.
    tools = []
    ff = shutil.which("ffmpeg")
    tools.append(chk(bool(ff), "ffmpeg (frames / audio)",
                     "" if ff else "not installed — `navig media` video path is blocked", warn=not ff))
    wh = shutil.which("whisper") or shutil.which("faster-whisper")
    tools.append(chk(bool(wh), "whisper (transcription)",
                     "" if wh else "not on PATH — may still work via API/python", warn=not wh))
    groups.append({"name": "Media tools", "checks": tools})

    flat = [c for g in groups for c in g["checks"]]
    return {
        "space": name, "path": str(space_path), "groups": groups,
        "ok": sum(1 for c in flat if c["status"] == "ok"),
        "warn": sum(1 for c in flat if c["status"] == "warn"),
        "missing": sum(1 for c in flat if c["status"] == "missing"),
    }


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


def resolve_active_space() -> str | None:
    """Resolve the active space NAME, or ``None`` if none is set — the single canonical reader.

    Priority (highest first): the ``NAVIG_SPACE`` env override → the cache file
    (``~/.navig/cache/active_space.txt``, the SOURCE OF TRUTH ``_set_active_space`` writes
    first) → the config-key mirror (``space.active`` → ``active_space`` → legacy
    ``spaces.active``, which the writer actively removes). Returns ``None`` when nothing is
    set so each caller supplies its own fallback (``get_active_space`` → ``"default"``; the
    deck → ``None``; ``navig life show`` → ``"personal"``).

    Every active-space reader must go through this (or ``get_active_space``) — ad-hoc readers
    that only touched config, or only the legacy ``spaces.active`` key, silently diverged from
    ``navig space switch`` (see #326 / #331; the deck missed the cache file, ``navig life show``
    always showed its fallback because it read the removed legacy key).
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

    return None


def get_active_space() -> str:
    """Return the active space name, or ``"default"`` when none is set.

    Thin fallback over :func:`resolve_active_space` (the canonical reader). Respects the
    ``NAVIG_SPACE`` env override so CI/scripting callers can override without touching state.
    """
    return resolve_active_space() or "default"


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


def _refuse_if_id_taken(
    space_id: str, space_path: Path, *, dry_run: bool = False, hint: str = "init"
) -> None:
    """Exit 1 with the other path named if *space_id* already belongs to a different space.

    One id, one space. A duplicate is not a warning: every id lookup after it answers
    whichever entry came first. Dry-run reports it too — a preview that hides the one
    thing that would stop the real run is not a preview. *hint* picks the way out that
    fits the caller: ``init`` (no manifest yet — pick another name) or ``rename`` (the
    folder is already a space — re-id it).
    """
    try:
        from navig.spaces import registry as _registry  # noqa: PLC0415

        other = _registry.id_taken_by_another_path(space_id, space_path)
    except Exception:  # noqa: BLE001 — a locked/corrupt registry must not block init
        return
    if not other:
        return
    if hint == "rename":
        way_out = f"  navig space rename {space_path} <other-id>"
    else:
        way_out = f"  navig space init <other-name> --path {space_path}"
    ch.error(
        f"'{space_id}' is already the id of another space: {other}",
        details="One id, one space — a second entry would make `navig space use "
                f"{space_id}` answer whichever came first. Either give this one another id:\n"
                f"{way_out}\n"
                f"or, if that space is gone, forget it first:  navig space forget {space_id}"
                + ("\n(dry run — nothing was written either way)" if dry_run else ""),
    )
    raise typer.Exit(1)


def _slug_from_folder(folder: Path) -> str:
    """Derive a space slug from a folder name — ``My Project`` → ``my-project``.

    Used only when the operator did not type a name. Returns ``""`` when nothing
    usable survives (a drive root, a folder named ``___``), which the caller turns
    into "tell me the name" rather than inventing one.
    """
    raw = (folder.name or "").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    # _SLUG_RE caps the length at 30; trim on a hyphen so the result stays readable
    # rather than ending mid-word.
    if len(slug) > 30:
        slug = slug[:30].rstrip("-")
    return slug if _SLUG_RE.match(slug) else ""


def _refuse_inferred_target(target: Path) -> str:
    """Why an INFERRED target must not be scaffolded, or ``""`` if it is fine.

    Only ever applied to a directory navig chose itself. An explicit ``--path`` is
    the operator's call and is never second-guessed — but a bare ``navig space init``
    typed in the wrong terminal should not quietly seed 100+ items into ``~``.
    """
    resolved = target.resolve()
    if resolved == resolved.parent:
        return "that is a filesystem root"
    if resolved == Path.home().resolve():
        return "that is your home directory"
    return ""


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
        table.add_row(f"{marker} {canonical}{suffix}", f"\\[{cfg.scope}] {cfg.path}")

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
    name: str | None = typer.Argument(
        None,
        help="Space name — slug format: a-z0-9 and hyphens. Omit it to name the space "
             "after the target folder (`My Project` becomes `my-project`).",
    ),
    path: Path | None = typer.Option(
        None, "--path", "-p",
        help="Initialize at this directory instead of ~/.navig/spaces/<name>. Relative paths "
             "are read from where you ran the command, so `--path .` turns the folder you are "
             "standing in into a space (e.g. --path . or D:\\spaces\\company).",
    ),
    no_links: bool = typer.Option(
        False, "--no-links",
        help="Skip the cross-platform links: root (plans/, inbox/) AND the .claude/ capability "
             "junctions. Use for a link-free/committed scaffold; run `navig wire` later to link.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview exactly what would be created — write nothing."
    ),
    books: str | None = typer.Option(
        None, "--books",
        help="Give this space its own finance BOOK (a separate ledger). Set it later with "
             "`navig space books <name>`.",
    ),
) -> None:
    """Create/initialize a space.

    **Both arguments are optional, and each defaults to the other's answer:**

    ==================================== ================== ==========================
    Command                              Space name         Created in
    ==================================== ================== ==========================
    ``navig space init``                 the folder you are the folder you are in
                                         standing in
    ``navig space init foo``             ``foo``            ``~/.navig/spaces/foo``
    ``navig space init --path D:\\work``  ``work``           ``D:\\work``
    ``navig space init foo --path .``    ``foo``            the folder you are in
    ==================================== ================== ==========================

    In one line: *the name defaults to the target folder's name, and the target
    folder defaults to where you ran the command.* So the common case — "make this
    project a space" — is a bare ``navig space init``.

    Scaffolds the canonical structure — ``.navig/{plans,inbox,memory,state,wiki}``
    plus the ``.dev/`` · ``.local/`` (both gitignored) · ``docs/`` hygiene
    zones — and links ``./plans`` → ``.navig/plans`` and ``./.inbox`` →
    ``.navig/inbox`` (junction on Windows, symlink on POSIX).

    Every space is born ready for the **/inbox distillery**: drop reference material
    (art, video, audio, articles, screenshots, code) into ``./.inbox`` and run
    ``/inbox`` to distil it into indexed notes under ``.navig/refs/notes/``. The skill
    lives in ``.navig/skills/inbox/`` and is auto-wired into ``.claude/`` on init (the
    capability junctions ``navig wire`` makes), so ``/inbox`` works immediately — no
    separate step. Tune it per-project via its ``references/project-profile.md``.

    **Purely additive.** Safe to run on an existing project directory: it only
    adds what's missing and never overwrites, truncates, or deletes anything you
    already have. Use ``--dry-run`` to preview first.
    """
    # Resolve the TARGET first, because the name is derived from it when omitted.
    # A name with no --path keeps the historical destination (~/.navig/spaces/<name>);
    # everything else follows the folder.
    inferred_target = path is None and not name
    if path is not None:
        space_path = resolve_user_path(path)
    elif name:
        space_path = _spaces_dir() / _validate_slug(name)
    else:
        space_path = invocation_cwd().resolve()

    if inferred_target and (reason := _refuse_inferred_target(space_path)):
        ch.error(
            f"Refusing to initialize a space in {space_path} — {reason}.",
            details="Run it from the project folder, or name the target explicitly:\n"
                    "  navig space init <name>            # in ~/.navig/spaces/<name>\n"
                    f"  navig space init --path {space_path}   # if you really meant here",
        )
        raise typer.Exit(1)

    if name:
        name = _validate_slug(name)
    else:
        name = _slug_from_folder(space_path)
        if not name:
            ch.error(
                f"Cannot derive a space name from {space_path}.",
                details="Give one explicitly: navig space init <name> "
                        "(lowercase letters, digits, hyphens).",
            )
            raise typer.Exit(1)
        ch.info(f"Naming the space after the folder: [bold]{name}[/bold]")

    # The id must be unique in the registry. `register` keys on PATH, so a second folder
    # with the same name used to append a duplicate id — `space use <id>` then answered
    # whichever came first. Refuse BEFORE anything is written, and say what to do.
    _refuse_if_id_taken(name, space_path, dry_run=dry_run)

    # Refuse to scaffold "into" a regular file — never clobber it.
    if space_path.exists() and not space_path.is_dir():
        ch.error(
            f"Cannot initialize space at {space_path}",
            details="A file already exists at that path. Choose another --path or remove it yourself.",
        )
        raise typer.Exit(1)

    existed = space_path.is_dir() and any(space_path.iterdir())
    was_space = (space_path / ".navig").is_dir()  # a space already, not merely a non-empty folder

    if not dry_run:
        try:
            space_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            ch.error(f"Failed to create {space_path}", details=str(exc))
            raise typer.Exit(1) from exc

    summary = _scaffold_space_skeleton(space_path, name, dry_run=dry_run)
    link_msgs = [] if (no_links or dry_run) else _link_space_roots(space_path)
    # Auto-wire capability junctions so the space's own skills (e.g. /inbox) are
    # immediately visible to Claude Code — no separate `navig wire` needed for the basics.
    cap_msgs = [] if (no_links or dry_run) else _link_space_capabilities(space_path)

    # Register the workshop in the brain's index (~/.navig/spaces.json), enabled —
    # so it shows up in the deck + global switcher. A folder given via --path is
    # "external"; an unspecified path lives under ~/.navig/spaces (a "root" space).
    if not dry_run:
        try:
            from navig.spaces import registry as _registry  # noqa: PLC0415

            # "root" means it lives under ~/.navig/spaces; anything else is external.
            # Keyed off where the space ACTUALLY landed, not off whether --path was
            # typed: a bare `navig space init` infers the operator's own folder, which
            # is external even though no --path was given.
            _registry.register(
                space_path, id=name, name=name, source=_registry.source_for(space_path), enabled=True,
            )
        except Exception:  # noqa: BLE001 — registry is best-effort, never block init
            pass

    # Seed the per-space finance book if asked (writes manifest.books — the key
    # Harbor's ledger reads; the scaffold just wrote space.json, so this only adds
    # a key). Never fail an otherwise-successful init over a book seed.
    book = (books or "").strip()
    if book and not dry_run:
        from navig.spaces.space_manifest import (  # noqa: PLC0415
            ManifestNotWritable,
            set_manifest_field,
        )

        try:
            set_manifest_field(space_path, "books", book)
        except ManifestNotWritable as exc:
            ch.warning(
                f"Space created, but couldn't set the book: {exc}",
                details="Set it later with: navig space books <name>",
            )
            book = ""

    # ── Report — show that existing content was left untouched ────────────────
    nc, ns, nx = len(summary["created"]), len(summary["skipped"]), len(summary["conflicts"])
    for m in summary.get("migrated", []):
        ch.info(f"  {'DRY RUN: would migrate' if dry_run else 'migrated'}: {m}")
    if dry_run:
        ch.info(f"[yellow]DRY RUN:[/yellow] Would create {nc} item(s) in {space_path}; {ns} already present (kept).")
        for item in summary["created"]:
            ch.info(f"  + {item}")
        if book:
            ch.info(f"[yellow]DRY RUN:[/yellow] Would set the finance book: {book}")
    else:
        # "existing space" was printed for ANY non-empty folder — a README and a .git
        # made a project read as a space it never was. Say which it was.
        if was_space:
            headline = f"Initialized structure in existing space '{name}'."
        elif existed:
            headline = f"Turned existing folder into space '{name}'."
        else:
            headline = f"Created space '{name}'."
        ch.success(headline, details=str(space_path))
        if book:
            ch.info(f"  book: {book} — separate finance ledger (Harbor)")
        ch.info(f"+{nc} created · {ns} existing left untouched · "
                ".navig/{plans,inbox,memory,state,wiki} · .dev/ · .local/ · docs/ "
                "(.dev/.local gitignored · .navig/ committable except private state)")
        for m in link_msgs:
            ch.info(f"  link: {m}")
        for m in cap_msgs:
            ch.info(f"  wire: {m}")
        # Only promise /inbox if we actually created it — the old code printed this line
        # unconditionally, including on installs where the distillery template was missing.
        if not summary.get("incomplete"):
            ch.info("  Ready: drop files in ./.inbox and run /inbox to distil them.")

    if nx:
        ch.warning(
            f"{nx} path conflict(s) skipped — nothing was overwritten:",
            details="\n".join(summary["conflicts"]),
        )

    if summary.get("incomplete"):
        ch.error(
            "This navig install is incomplete — the space was created WITHOUT some capabilities:",
            details="\n".join(summary["incomplete"]),
        )


@space_app.command("doctor")
@space_app.command("check")
def space_doctor(
    target: str | None = typer.Argument(
        None, help="Space name or path to check (default: current directory)."
    ),
    fix: bool = typer.Option(
        False, "--fix",
        help="Additively add whatever is missing — skeleton, links, wiring, distillery. "
             "Never overwrites, truncates, or deletes anything you already have.",
    ),
    agents: bool = typer.Option(
        False, "--agents",
        help="Wire the space for all AI assistants (Claude · Copilot · Cursor · Gemini · "
             "Codex/AGENTS.md) — additive instruction pointers, never overwrites.",
    ),
    update: bool = typer.Option(
        False, "--update",
        help="Refresh the /inbox engine files to the shipped version. Overwrites the shared skill "
             "logic ONLY — your project-profile.md and distilled notes are never touched.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable diagnosis."),
    no_interactive: bool = typer.Option(
        False, "--no-interactive", "--yes", "-y",
        help="Never prompt — just print the checklist and exit (for scripts / CI).",
    ),
) -> None:
    """Check a space and report what's present vs missing — then let you pick the next step.

    A health checklist across Structure · Wiring · Distillery (/inbox) · Registry · AI assistants.
    In a terminal it's **interactive**: after the report it offers a menu (fix · wire assistants ·
    …) and loops so you can chain steps — it does not exit until you quit. Reconciliation is always
    *additive* — it seeds only what's missing and leaves every existing file byte-for-byte.

    Non-interactive shortcuts: ``--fix`` (repair), ``--agents`` (wire assistants), ``--json``
    (machine output, exit ≠0 while anything is missing), ``--no-interactive`` (print & exit).
    """
    space_path = _resolve_space_target(target)
    if space_path is None or not space_path.is_dir():
        ch.error(
            "No space found to check.",
            details="Pass a space name/path, cd into a space, or run `navig space init` first.",
        )
        raise typer.Exit(1)
    name = _resolve_space_name(space_path)

    # One-shot flag actions (scriptable, non-interactive).
    if fix:
        added = _apply_fix(space_path, name)
        ch.success(
            f"Repaired '{name}' additively — {added} item(s) added, existing files untouched."
            if added else f"'{name}' already complete — nothing to add.",
            details=str(space_path),
        )
    if agents:
        for m in _wire_agents(space_path, name):
            ch.info(f"  agent: {m}")
        ch.success(f"Wired '{name}' for all AI assistants (existing files untouched).")
    if update:
        changed = _sync_engine(space_path)
        for m in changed:
            ch.info(f"  engine: {m}")
        ch.success(
            f"Updated the /inbox engine — {len(changed)} file(s) refreshed; "
            "profile & notes untouched." if changed else "Engine already up to date.",
        )

    diag = _diagnose_space(space_path, name)

    if as_json:
        import json  # noqa: PLC0415

        typer.echo(json.dumps(diag, indent=2))
        raise typer.Exit(0 if diag["missing"] == 0 else 1)

    # Interactive next-step menu — only in a real terminal, and only when no one-shot flag ran.
    interactive = (
        not (fix or agents or update or as_json or no_interactive)
        and _stdin_is_tty()
    )
    _render_doctor(diag, fixed=fix, show_actions=not interactive)
    if interactive:
        _doctor_interactive_loop(space_path, name)
        return

    if diag["missing"]:
        raise typer.Exit(1)


# ── Cross-space audit (registry-wide integrity) ───────────────────────────────


def _read_workspace_id(space_dir: Path) -> str | None:
    """Pull the ``workspaceId`` from a space's ``events.jsonl`` (first event).

    The id is stamped on every automation event by navig-os and is stable per
    space, so the cheap first non-empty line is enough. Returns ``None`` when the
    log is absent, empty, or unparsable — never raises.
    """
    import json  # noqa: PLC0415

    f = space_dir / "events.jsonl"
    if not f.exists():
        return None
    try:
        with f.open("r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line:
                    continue
                rec = json.loads(line)
                wid = rec.get("workspaceId")
                if not wid and isinstance(rec.get("data"), dict):
                    wid = rec["data"].get("workspaceId")
                return str(wid) if wid else None
    except Exception:  # noqa: BLE001 — a corrupt log must not break the audit
        return None
    return None


def _audit_spaces() -> dict:
    """Scan every spaces root + the registry for structural drift.

    Read-only. Detects the failure modes that let duplicate spaces silently
    accumulate: bare-vs-``-space`` folder twins, a ``workspaceId`` claimed by two
    folders, duplicate registry ids, and orphaned registry paths.

    Deliberately does NOT flag a shared config *slug* — distinct sub-spaces
    legitimately reuse generic slugs (``research``, ``dev``); a genuinely
    duplicated logical space is caught reliably by the ``workspaceId`` check.
    """
    from navig.spaces import registry as _registry  # noqa: PLC0415
    from navig.spaces.resolver import spaces_roots  # noqa: PLC0415

    roots = spaces_roots()

    # Inventory every immediate sub-folder across all roots (skip hidden/.trash).
    inventory: list[dict] = []
    bare_pairs: list[dict] = []
    for root in roots:
        if not root.is_dir():
            continue
        names: set[str] = set()
        for entry in sorted(root.iterdir(), key=lambda p: p.name):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            names.add(entry.name)
            inventory.append(
                {
                    "name": entry.name,
                    "path": str(entry),
                    "workspace_id": _read_workspace_id(entry),
                }
            )
        # bare-vs--space twins living side by side in the same root
        for n in sorted(names):
            if n.endswith("-space"):
                bare = n[: -len("-space")]
                if bare and bare in names:
                    bare_pairs.append({"root": str(root), "bare": bare, "spaced": n})

    def _group(key: str) -> list[dict]:
        buckets: dict[str, list[str]] = {}
        for item in inventory:
            val = item.get(key)
            if val:
                buckets.setdefault(val, []).append(item["path"])
        return [
            {key: v, "paths": sorted(paths)}
            for v, paths in sorted(buckets.items())
            if len(paths) > 1
        ]

    dup_workspace_ids = _group("workspace_id")

    # Registry-side checks.
    reg = _registry.load_registry()
    entries = reg.get("spaces", []) if isinstance(reg, dict) else []
    id_buckets: dict[str, list[str]] = {}
    orphans: list[dict] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        eid = e.get("id")
        path = e.get("path", "")
        if eid:
            id_buckets.setdefault(str(eid), []).append(str(path))
        if path and not Path(str(path)).exists():
            orphans.append({"id": eid, "path": str(path)})
    dup_registry_ids = [
        {"id": i, "paths": sorted(paths)}
        for i, paths in sorted(id_buckets.items())
        if len(paths) > 1
    ]

    # Nested sub-spaces that are not folded. A space under another space's `spaces/`
    # dir claims a top-level id the moment anyone cds into it — and permanently, since
    # discovery registers before it filters. `navig space fold` is the fix.
    unfolded_subspaces = _find_unfolded_subspaces(inventory)

    issue_count = (
        len(bare_pairs)
        + len(dup_workspace_ids)
        + len(dup_registry_ids)
        + len(orphans)
        + len(unfolded_subspaces)
    )
    return {
        "roots": [str(r) for r in roots],
        "spaces_scanned": len(inventory),
        "issue_count": issue_count,
        "bare_vs_space_pairs": bare_pairs,
        "duplicate_workspace_ids": dup_workspace_ids,
        "duplicate_registry_ids": dup_registry_ids,
        "orphan_registry_paths": orphans,
        "unfolded_subspaces": unfolded_subspaces,
    }


def _find_unfolded_subspaces(inventory: list[dict]) -> list[dict]:
    """Sub-spaces under ``<space>/spaces/<name>/`` that would claim a top-level id.

    Only flags folders that *would actually be surfaced* — a bare `.navig/` with no
    manifest and no plans is already invisible and needs no marker.
    """
    from navig.spaces.resolver import _project_has_content, is_folded  # noqa: PLC0415

    found: list[dict] = []
    for item in inventory:
        container = Path(item["path"]) / "spaces"
        if not container.is_dir():
            continue
        try:
            children = sorted(container.iterdir(), key=lambda p: p.name)
        except OSError:
            continue
        for child in children:
            if not child.is_dir() or child.name.startswith("."):
                continue
            if not (child / ".navig").is_dir():
                continue
            if is_folded(child) or not _project_has_content(child):
                continue
            found.append(
                {"parent": item["name"], "name": child.name, "path": str(child)}
            )
    return found


def _render_audit(f: dict) -> None:
    cons = ch.console
    if not f["issue_count"]:
        ch.success(
            f"Spaces audit clean — {f['spaces_scanned']} space(s) scanned, "
            "no duplicate ids/workspaceIds/slugs or bare-vs--space pairs."
        )
        cons.print(f"  roots: {', '.join(f['roots'])}", style="dim")
        return

    ch.error(f"Spaces audit found {f['issue_count']} issue(s).")

    if f["bare_vs_space_pairs"]:
        cons.print("\n  bare-vs--space folder pairs (one is likely a stray duplicate):", style="yellow")
        for p in f["bare_vs_space_pairs"]:
            cons.print(f"    • {p['bare']}  ↔  {p['spaced']}   in {p['root']}")
    if f["duplicate_workspace_ids"]:
        cons.print("\n  same workspaceId in >1 folder (same logical space registered twice):", style="yellow")
        for d in f["duplicate_workspace_ids"]:
            cons.print(f"    • {d['workspace_id']}")
            for pth in d["paths"]:
                cons.print(f"        - {pth}", style="dim")
    if f["duplicate_registry_ids"]:
        cons.print("\n  duplicate id in spaces.json registry:", style="yellow")
        for d in f["duplicate_registry_ids"]:
            cons.print(f"    • {d['id']}")
            for pth in d["paths"]:
                cons.print(f"        - {pth}", style="dim")
    if f["orphan_registry_paths"]:
        cons.print("\n  registry entries pointing at a missing path (orphans):", style="yellow")
        for o in f["orphan_registry_paths"]:
            cons.print(f"    • {o['id']}  →  {o['path']}")
    if f.get("unfolded_subspaces"):
        cons.print(
            "\n  nested sub-spaces that will claim a top-level id on first cd "
            "(fix: `navig space fold <path>`):",
            style="yellow",
        )
        for s in f["unfolded_subspaces"]:
            cons.print(f"    • {s['parent']}/spaces/{s['name']}")
            cons.print(f"        - {s['path']}", style="dim")

    cons.print(
        "\n  Review, then remove the stray twin/entry (quarantine the folder, drop the "
        "duplicate registry id). `default`, project mounts, and canonical spaces are "
        "intentionally bare — not issues.",
        style="dim",
    )


@space_app.command("audit")
@space_app.command("lint")
def space_audit(
    as_json: bool = typer.Option(False, "--json", help="Machine-readable report."),
) -> None:
    """Audit the whole spaces collection for structural drift.

    A cross-space integrity check — distinct from ``space doctor``, which inspects
    a single space. Flags the failure modes that let duplicate spaces silently
    accumulate:

    • bare-vs-``-space`` folder twins (e.g. ``homelab`` beside ``homelab-space``)\n
    • the same ``workspaceId`` claimed by two space folders\n
    • duplicate ids in the spaces.json registry\n
    • registry entries whose path no longer exists (orphans)

    Read-only — it never moves, writes, or deletes anything. Exits non-zero when
    any issue is found, so it works as a CI / pre-commit guard.
    """
    findings = _audit_spaces()
    if as_json:
        import json  # noqa: PLC0415

        typer.echo(json.dumps(findings, indent=2))
    else:
        _render_audit(findings)
    if findings["issue_count"]:
        raise typer.Exit(1)


def _stdin_is_tty() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except Exception:  # noqa: BLE001
        return False


def _doctor_interactive_loop(space_path: Path, name: str) -> None:
    """Prompt for the next step and loop — fix, wire assistants, or quit — re-rendering each time."""
    cons = ch.console
    while True:
        diag = _diagnose_space(space_path, name)
        opts = _doctor_menu_options(diag)
        if not opts:
            cons.print("\n[green]All good.[/] Drop files in [cyan]./.inbox[/] and run [cyan]/inbox[/].")
            return
        cons.print("\n[bright_cyan]What next?[/]")
        for i, (_key, label) in enumerate(opts, 1):
            cons.print(f"  [bold]{i}[/]. {label}")
        cons.print("  [bold]q[/]. Quit")
        try:
            raw = typer.prompt("Choose", default="q").strip().lower()
        except (EOFError, KeyboardInterrupt):
            cons.print("")
            return
        if raw in ("q", "quit", ""):
            return
        action = None
        if raw.isdigit() and 1 <= int(raw) <= len(opts):
            action = opts[int(raw) - 1][0]
        else:
            action = next((k for k, _ in opts if k == raw), None)
        if action is None:
            cons.print("[yellow]Not a choice — pick a number or q.[/]")
            continue

        if action == "fix":
            added = _apply_fix(space_path, name)
            ch.success(f"{added} item(s) added — existing files untouched."
                       if added else "Nothing to add.")
        elif action == "update":
            changed = _sync_engine(space_path)
            for m in changed:
                cons.print(f"  [dim]{m}[/]")
            ch.success(f"Engine refreshed — {len(changed)} file(s); profile & notes untouched."
                       if changed else "Engine already up to date.")
        elif action == "agents":
            for m in _wire_agents(space_path, name):
                cons.print(f"  [dim]{m}[/]")
            ch.success("Wired for all AI assistants.")
        elif action == "retire":
            from navig.spaces.gitignore import (  # noqa: PLC0415
                blanket_navig_rule_lines,
                retire_blanket_navig_rules,
            )

            gi_path = space_path / ".gitignore"
            text = gi_path.read_text(encoding="utf-8")
            lines = text.splitlines()
            cons.print("\n[bright_cyan]This would remove from .gitignore:[/]")
            for n in blanket_navig_rule_lines(text):
                cons.print(f"  [dim]{n:>3}[/]  [red]- {lines[n - 1]}[/]")
            cons.print("  [dim]The managed block keeps .navig/{inbox,state,vault,memory,refs,…} "
                       "ignored; plans, skills and wiki become committable.[/]")
            if not typer.confirm("Remove these lines?", default=False):
                cons.print("[dim]Left as is.[/]")
                continue  # nothing changed on disk
            removed = retire_blanket_navig_rules(space_path)
            ch.success(f"Removed {len(removed)} line(s) from .gitignore.")
        elif action == "inbox":
            cons.print(
                "\n[bright_cyan]Configure /inbox[/]\n"
                "  Open this space in Claude Code and run [cyan]/inbox[/]. On first run it detects\n"
                "  this project's design system, media roots and library location, confirms them\n"
                "  with you, then writes them into "
                "[dim].navig/skills/inbox/references/project-profile.md[/].\n"
            )
            continue  # nothing changed on disk → skip re-render

        _render_doctor(_diagnose_space(space_path, name), fixed=(action == "fix"),
                       show_actions=False)


def _render_doctor(diag: dict, *, fixed: bool, show_actions: bool = True) -> None:
    """Pretty, pro-grade checklist with an actionable footer (rich markup).

    ``show_actions=False`` drops the static "Next actions" block — used in interactive mode,
    where the live menu supersedes it.
    """
    cons = ch.console
    sym = {
        "ok": (ch._safe_symbol("✓", "+"), "green"),
        "warn": (ch._safe_symbol("⚠", "!"), "yellow"),
        "missing": (ch._safe_symbol("✗", "x"), "red"),
    }
    arrow = ch._safe_symbol("→", "->")
    checks = [c for g in diag["groups"] for c in g["checks"]]
    width = min(46, max((len(c["label"]) for c in checks), default=12))

    cons.rule(f"[bright_cyan]space doctor[/] · [bold]{diag['space']}[/]")
    cons.print(f"[dim]{diag['path']}[/dim]\n")

    for group in diag["groups"]:
        cons.print(f"[bright_cyan]{group['name']}[/]")
        for c in group["checks"]:
            glyph, color = sym[c["status"]]
            detail = f"  [dim]{c['detail']}[/dim]" if c["detail"] else ""
            cons.print(f"  [{color}]{glyph}[/] {c['label']:<{width}}{detail}")
        cons.print("")

    ok_n, warn_n, miss_n = diag["ok"], diag["warn"], diag["missing"]
    if miss_n == 0 and warn_n == 0:
        cons.print(f"[green]{sym['ok'][0]} Healthy[/]  ·  [green]{ok_n} ok[/], nothing to do.")
    else:
        head_color = "red" if miss_n else "yellow"
        head = f"{miss_n} to fix" if miss_n else "ready — warnings only"
        cons.print(
            f"[{head_color} bold]{head}[/]  ·  [green]{ok_n} ok[/] · "
            f"[yellow]{warn_n} warning(s)[/] · "
            f"{'[red]' if miss_n else '[dim]'}{miss_n} missing[/]"
        )

    # ── Next actions — the exact commands, prioritized ────────────────────────
    if not show_actions:
        return
    need_fix = any(c.get("action") == "fix" for c in checks)
    need_conf = any(c.get("action") == "configure" for c in checks)
    manual = [c for c in checks if c.get("action") == "manual"]
    if need_fix or need_conf or manual:
        cons.print("\n[bright_cyan]Next actions[/]")
        n = 1
        if need_fix:
            what = f"add {miss_n} missing item(s)" if miss_n else "reconcile links & registry"
            cons.print(f"  [bold]{n}.[/] [cyan]navig space doctor --fix[/]"
                       f"   [dim]{arrow} {what} — additive, never overwrites[/]")
            n += 1
        if need_conf:
            cons.print(f"  [bold]{n}.[/] [cyan]/inbox[/] [dim](in Claude Code)[/]"
                       f"   [dim]{arrow} configure the distillery profile for this space[/]")
            n += 1
        if manual:
            cons.print(f"  [bold]{n}.[/] [yellow]resolve {len(manual)} path conflict(s) by hand[/]"
                       f"   [dim]{arrow} a file sits where a folder belongs — nothing was touched[/]")
    elif miss_n == 0:
        cons.print(f"[dim]{arrow} drop files in ./.inbox and run /inbox to distil them.[/dim]")


# ── Multi-assistant wiring: make the space legible to every agent tool ────────
def _agent_pointer_body(name: str) -> str:
    return (
        f"This is a **NAVIG space** (`{name}`). Canonical project context and agent "
        "guidance is in `NAVIG.md` — read it first.\n\n"
        "- Plans: `.navig/plans/` · Inbox drop zone: `./.inbox` · Docs: `docs/`\n"
        "- Capabilities (skills / agents / blocks) live under `.navig/` and are linked into `.claude/`.\n\n"
        "## /inbox distillery\n"
        "Drop reference material (art, video, audio, articles, screenshots, code) into `./.inbox`\n"
        "and run the `/inbox` skill to distil it into indexed notes under `.navig/refs/notes/`.\n"
        "See `.navig/skills/inbox/SKILL.md`.\n"
    )


def _agent_integrations(name: str) -> tuple[tuple[str, str, str | None], ...]:
    """(display, relpath, content) per assistant. ``content is None`` ⇒ already scaffolded
    (Claude's CLAUDE.md), so it's only checked, never written."""
    body = _agent_pointer_body(name)
    cursor = ("---\ndescription: NAVIG space guidance + /inbox distillery\nalwaysApply: true\n---\n\n"
              + body)
    return (
        ("NAVIG.md (canonical)", "NAVIG.md", None),
        ("Claude Code", "CLAUDE.md", None),
        ("GitHub Copilot", ".github/copilot-instructions.md", f"# Copilot instructions — {name}\n\n{body}"),
        ("Cursor", ".cursor/rules/navig.mdc", cursor),
        ("Gemini CLI", "GEMINI.md", f"# {name} — Gemini guidance\n\n{body}"),
        ("Codex / AGENTS.md", "AGENTS.md", f"# AGENTS — {name}\n\n{body}"),
    )


def _agent_status(space_path: Path, name: str) -> list[tuple[str, bool]]:
    return [(display, (space_path / rel).exists()) for display, rel, _ in _agent_integrations(name)]


def _wire_agents(space_path: Path, name: str) -> list[str]:
    """Additively create per-assistant instruction pointers — never overwrites an existing file."""
    msgs: list[str] = []
    for display, rel, content in _agent_integrations(name):
        dest = space_path / rel
        if dest.exists():
            msgs.append(f"skip {display} ({rel} exists)")
            continue
        if content is None:
            msgs.append(f"skip {display} (CLAUDE.md not present — run --fix first)")
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(dest, content)
        msgs.append(f"wired {display} -> {rel}")
    return msgs


def _engine_drift(space_path: Path) -> list[str]:
    """Engine files whose content differs from the packaged template ⇒ an update is available.
    Missing files are NOT drift (that's the additive-fix path); only present-but-different count.
    State files (profile, INDEX, notes, ledger) are excluded by construction."""
    base = _SCAFFOLD_TEMPLATES / "space-distillery"
    drifted: list[str] = []
    for tmpl_rel, dest_rel in _DISTILLERY_ENGINE:
        src, dst = base / tmpl_rel, space_path / dest_rel
        if not (src.is_file() and dst.is_file()):
            continue
        try:
            if src.read_text(encoding="utf-8") != dst.read_text(encoding="utf-8"):
                drifted.append(dest_rel)
        except OSError:
            continue
    return drifted


def _sync_engine(space_path: Path) -> list[str]:
    """Refresh the ENGINE files from the shipped template — overwrites shared logic + pipeline
    docs ONLY. Never touches project-profile.md, INDEX.md, distilled notes, or the ledger."""
    base = _SCAFFOLD_TEMPLATES / "space-distillery"
    msgs: list[str] = []
    for tmpl_rel, dest_rel in _DISTILLERY_ENGINE:
        src = base / tmpl_rel
        if not src.is_file():
            continue
        dst = space_path / dest_rel
        new = src.read_text(encoding="utf-8")
        if dst.is_file() and dst.read_text(encoding="utf-8") == new:
            continue  # already current
        dst.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(dst, new)
        msgs.append(f"updated {dest_rel}")
    return msgs


def _extract_vision_seed(space_path: Path) -> str:
    """Best-effort seed for a new NAVIG.md: prefer `.navig/vision.md`, else the
    legacy 'PROJECT VISION CONTEXT' block hand-pasted into `.navig/ai_system_prompt.txt`."""
    vision = space_path / ".navig" / "vision.md"
    if vision.exists():
        try:
            text = vision.read_text(encoding="utf-8", errors="replace")
            # Skip a leading H1, take the first substantive paragraph.
            for para in text.split("\n\n"):
                p = para.strip()
                if p and not p.startswith("#"):
                    return p[:600]
        except OSError:
            pass
    legacy = space_path / ".navig" / "ai_system_prompt.txt"
    if legacy.exists():
        try:
            text = legacy.read_text(encoding="utf-8", errors="replace")
            idx = text.upper().find("PROJECT VISION CONTEXT")
            if idx != -1:
                tail = text[idx:].split("\n", 1)[-1].strip()
                if tail:
                    return tail[:600]
        except OSError:
            pass
    return ""


def _migrate_context(space_path: Path, name: str) -> list[str]:
    """Idempotent NAVIG.md migration (the conditional matrix).

    * No NAVIG.md → create it (seeded from vision.md / legacy prompt / placeholder).
    * NAVIG.md present but carrying no task-list guidance → append it, marker-fenced,
      at the END (a space scaffolded before the PIM shipped).
    * CLAUDE.md missing → create the thin pointer.
    * CLAUDE.md present but not referencing NAVIG.md → append a marker-guarded
      `@NAVIG.md` import at the END (original bytes untouched above it).
    * Everything already wired → no-op.
    """
    msgs: list[str] = []
    navig_md = space_path / "NAVIG.md"
    claude_md = space_path / "CLAUDE.md"

    if not navig_md.exists():
        seed = _extract_vision_seed(space_path)
        atomic_write_text(navig_md, _navig_md_template(name, vision_seed=seed))
        msgs.append("created NAVIG.md" + (" (seeded from vision)" if seed else ""))
    else:
        # A space scaffolded before the PIM shipped has no idea the operator's task
        # list exists, so its agents write findings into a chat message instead. The
        # section is appended at the END, marker-fenced, with every existing byte
        # above it untouched — the same rule the CLAUDE.md import below follows.
        existing_ctx = navig_md.read_text(encoding="utf-8", errors="replace")
        if not _has_task_guidance(existing_ctx):
            atomic_write_text(
                navig_md,
                existing_ctx.rstrip("\n") + "\n\n" + _task_list_guidance(),
            )
            msgs.append("appended the task-list guidance to NAVIG.md")

    if not claude_md.exists():
        atomic_write_text(claude_md, _claude_pointer(name))
        msgs.append("created CLAUDE.md pointer")
    else:
        existing = claude_md.read_text(encoding="utf-8", errors="replace")
        if "NAVIG.md" not in existing:
            appended = (
                existing.rstrip("\n")
                + f"\n\n{_CTX_MARKER_START}\n@NAVIG.md\n{_CTX_MARKER_END}\n"
            )
            atomic_write_text(claude_md, appended)
            msgs.append("appended @NAVIG.md import to CLAUDE.md")
    return msgs


def _apply_fix(space_path: Path, name: str) -> int:
    """Structural repair — all additive, never overwrites. Returns count of items added."""
    summary = _scaffold_space_skeleton(space_path, name, dry_run=False)
    _migrate_context(space_path, name)
    _link_space_roots(space_path)
    _link_space_capabilities(space_path)
    # The gitignore row doctor reports is repaired by the same code `navig wire` runs.
    from navig.spaces.gitignore import reconcile as _reconcile_gitignore  # noqa: PLC0415

    gi_actions = _reconcile_gitignore(space_path)
    try:
        from navig.spaces import registry as _registry  # noqa: PLC0415

        # never create a duplicate id from a repair either; doctor's own row will say why
        if not _registry.id_taken_by_another_path(name, space_path):
            _registry.ensure_registered(
                space_path, id=name, name=name, source=_registry.source_for(space_path)
            )
    except Exception:  # noqa: BLE001
        pass
    return len(summary["created"]) + sum(1 for a in gi_actions if not a.startswith("⚠"))


def _doctor_menu_options(diag: dict) -> list[tuple[str, str]]:
    """Build the interactive next-step menu from the diagnosis (only offer what's relevant)."""
    checks = [c for g in diag["groups"] for c in g["checks"]]
    opts: list[tuple[str, str]] = []
    if diag["missing"] or any(c.get("action") == "fix" for c in checks):
        opts.append(("fix", "Fix missing — skeleton · links · wiring · distillery (additive)"))
    if any(c.get("action") == "update" for c in checks):
        opts.append(("update", "Update the /inbox engine — refresh the skill (keeps profile & notes)"))
    if any(c.get("action") == "agents" for c in checks):
        opts.append(("agents", "Wire for AI assistants — Copilot · Cursor · Gemini · Codex/AGENTS.md"))
    if any(c.get("action") == "configure" for c in checks):
        opts.append(("inbox", "How to configure /inbox for this project"))
    if any(c.get("action") == "retire-navig-rule" for c in checks):
        opts.append(("retire", "Remove your blanket `.navig/` rule from .gitignore — shows the lines, "
                               "asks first (private state stays ignored)"))
    return opts


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


@space_app.command("books")
def space_books(
    name: str = typer.Argument(
        None, help="Book name to set (e.g. 'Company'). Omit to show the current book."
    ),
    clear: bool = typer.Option(
        False, "--clear", help="Clear the book → back to the default (personal) ledger."
    ),
    space: str = typer.Option(
        None, "--space", help="Target space by name or path (default: the active space)."
    ),
) -> None:
    """Show or set the finance BOOK a space keeps its ledger in.

    Each named book is a separate ledger, so a Company space and your personal
    space keep independent books; absent = the default (personal) ledger. Stored
    in the space manifest's `books` key — the same key the finance app reads for
    the ACTIVE space. (The ledger itself is a Harbor feature.)
    """
    from navig.spaces.space_manifest import (  # noqa: PLC0415
        ManifestNotWritable,
        load_space_manifest,
        set_manifest_field,
    )

    # Resolve the target space directory (the active space by default).
    if space:
        from navig.spaces.resolver import discover_space_paths  # noqa: PLC0415

        cfg = discover_space_paths(include_disabled=True).get(space)
        if cfg is not None:
            space_dir = Path(cfg.path)
        else:
            candidate = resolve_user_path(space).expanduser()
            if not candidate.is_dir():
                ch.error(
                    f"Space not found: {space}",
                    details="Pass a registered space name or a folder path.",
                )
                raise typer.Exit(1)
            space_dir = candidate
    else:
        from navig.spaces.active import get_active_working_dir  # noqa: PLC0415

        space_dir = get_active_working_dir()

    current = load_space_manifest(space_dir).books

    if name is None and not clear:  # show
        if current:
            ch.info(f"Book: {current}")
        else:
            ch.info("Book: default (personal ledger)")
        return

    try:
        if clear:
            set_manifest_field(space_dir, "books", None)
            ch.success("Book cleared → default (personal) ledger.")
        else:
            set_manifest_field(space_dir, "books", name)
            ch.success(f"Book set to '{name}'.")
            ch.info("This space's finance ledger is now a separate book (Harbor).")
    except ManifestNotWritable as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from exc


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

    target = resolve_user_path(path)
    if not target.is_dir() or not is_space_dir(target):
        ch.error(f"Not a space: {target}", details="A space is a folder containing a .navig/ directory.")
        raise typer.Exit(1)
    manifest = load_space_manifest(target)
    sid = normalize_space_name(manifest.resolved_id or target.name)
    # The one register site that skipped the id check: `register ~/other/homelab` after
    # `init homelab` elsewhere appended a second `homelab` row, exactly what init/wire/
    # doctor refuse. `source` was also hardcoded "external" — a folder under
    # ~/.navig/spaces registered by hand was filed as foreign.
    _refuse_if_id_taken(sid, target, hint="rename")
    entry = space_registry.register(
        target,
        id=sid,
        name=manifest.resolved_name or target.name,
        source=space_registry.source_for(target),
        enabled=True,
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


def _display_name_for(slug: str) -> str:
    """The display name ``space init`` derives from a slug — ``home-lab`` → ``Home Lab``."""
    return slug.replace("-", " ").replace("_", " ").title()


def _retarget_navig_md_space(space_path: Path, old: str, new: str) -> bool:
    """Rewrite ``space: <old>`` → ``space: <new>`` in NAVIG.md's frontmatter, if present.

    The frontmatter carries the id as a human-visible label (``_navig_md_template``);
    nothing machine-reads it, so a stale value is confusion rather than breakage —
    but "rename" that leaves the file saying the old name is half a rename. Only the
    leading ``---`` block is touched, only an exact ``space: <old>`` line, and only
    when the file parses as one; anything else is left alone. Returns whether it wrote.
    """
    md = space_path / "NAVIG.md"
    if not md.is_file():
        return False
    try:
        text = md.read_text(encoding="utf-8")
    except OSError:
        return False
    if not text.startswith("---\n"):
        return False
    end = text.find("\n---", 4)
    if end < 0:
        return False
    head, tail = text[: end + 1], text[end + 1 :]
    lines = head.split("\n")
    changed = False
    for i, line in enumerate(lines):
        if line.strip() == f"space: {old}":
            lines[i] = f"space: {new}"
            changed = True
    if not changed:
        return False
    try:
        atomic_write_text(md, "\n".join(lines) + tail)
    except OSError:
        return False
    return True


def _space_is_active(target: Path, manifest_id: str) -> bool:
    """Is *target* the space the active pointer names? Answered by PATH, not by label.

    The pointer holds an ID, and the whole reason `rename` exists is that those ids
    DRIFT: a manifest edited by hand leaves the registry row and the pointer on the
    former id, and comparing the pointer only with the manifest-derived id then calls
    the active space inactive -- so the pointer is left naming an id nothing resolves,
    which is the exact breakage this command repairs. Three ways in, cheapest first:
    the manifest id, the registry row for this folder, and finally what the pointer
    RESOLVES to (which also covers a conventional `~/.navig/spaces/<name>` folder).
    """
    from navig.spaces import registry as _registry  # noqa: PLC0415
    from navig.spaces.contracts import normalize_space_name  # noqa: PLC0415

    active = resolve_active_space()
    if not active:
        return False
    pointer = normalize_space_name(active)
    if pointer == manifest_id:
        return True
    row = _registry.entry_for(target)
    if row is not None and normalize_space_name(str(row.get("id") or "")) == pointer:
        return True
    try:
        from navig.spaces.resolver import resolve_space  # noqa: PLC0415

        cfg = resolve_space(active, cwd=invocation_cwd())
    except Exception:  # noqa: BLE001 - an unresolvable pointer is simply not this space
        return False
    try:
        return cfg.path.resolve() == target.resolve()
    except OSError:
        return False


def _move_space_folder(target: Path, new_id: str) -> tuple[Path | None, str]:
    """Rename *target*'s folder to *new_id* beside itself. Returns (new path, note).

    Only for a space living directly under `~/.navig/spaces`, where the folder name IS
    an id: `resolve_space()` returns `<spaces>/<id>` whenever that directory exists,
    BEFORE it consults the registry or the manifest. So a root space renamed in place
    keeps its old id as a live alias, and that folder squats the old id for whatever
    space is given it next. Moving the folder is what makes the convention hold again.

    The junctions this repo creates store ABSOLUTE targets (`plans`, `.inbox`, and the
    five `.claude/*` capability links), so they are removed BEFORE the move and rebuilt
    after -- a moved folder full of links pointing at its old path is worse than none.
    The process cwd is carried too: `main.py` chdir's into the active space, so renaming
    the space you stand in would otherwise fail on Windows with the directory in use.
    """
    import os  # noqa: PLC0415

    from navig.commands.mount import _remove_junction  # noqa: PLC0415

    destination = target.parent / new_id
    if destination.exists():
        return None, f"{destination} already exists"

    inside = False
    try:
        inside = Path(os.getcwd()).resolve().is_relative_to(target.resolve())
    except (OSError, ValueError):
        inside = False
    if inside:
        try:
            os.chdir(target.parent)  # a directory in use cannot be renamed on Windows
        except OSError as exc:
            return None, f"could not step out of {target}: {exc}"

    for link_name, _ in _ROOT_LINKS:
        _remove_junction(target / link_name)
    for rel_link, _ in _CAPABILITY_LINKS:
        _remove_junction(target / rel_link)

    try:
        target.rename(destination)
    except OSError as exc:
        _link_space_roots(target)  # put back what we unlinked
        _link_space_capabilities(target)
        if inside:
            try:
                os.chdir(target)
            except OSError:
                pass
        return None, f"could not move the folder: {exc}"

    _link_space_roots(destination)
    _link_space_capabilities(destination)
    if inside:
        try:
            os.chdir(destination)
        except OSError:
            pass
    return destination, "moved"


@space_app.command("rename")
def space_rename(
    space: str = typer.Argument(..., help="The space to rename — its id, or a path to its folder"),
    new_id: str = typer.Argument(..., help="New id: lowercase letters, digits, hyphens"),
    move_folder: bool = typer.Option(
        False,
        "--move-folder",
        help="Also rename the folder (spaces-root spaces only, where the folder name IS an id)",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Report what would change; write nothing"),
) -> None:
    """Give a space a new id — manifest, registry and the active-space pointer together.

    The id is what `navig space use <id>` answers to. It lives in three places and they
    drift the moment one is edited by hand: `.navig/space.json` (the source of truth —
    discovery re-derives everything else from it), the registry row in `spaces.json`,
    and the active-space pointer if this is the space you stand in. The manifest is
    written FIRST, so if the registry write fails the next discovery repairs the row
    from the manifest rather than the other way round.

    The folder stays put by default. For a space under `~/.navig/spaces` that leaves
    the OLD id resolving to it — `resolve_space()` answers with `<spaces>/<name>`
    whenever that directory exists, ahead of the registry and the manifest — so the
    old name stays a live alias and squats the id for any future space. `--move-folder`
    renames the folder too (relinking the junctions, which store absolute targets).
    """
    from navig.spaces import registry as space_registry  # noqa: PLC0415
    from navig.spaces.contracts import normalize_space_name  # noqa: PLC0415
    from navig.spaces.space_manifest import (  # noqa: PLC0415
        ManifestNotWritable,
        is_space_dir,
        load_space_manifest,
        set_manifest_field,
    )

    target = _resolve_space_target(space)
    if target is None or not target.is_dir() or not is_space_dir(target):
        ch.error(
            f"Not a space: {space}",
            details="Pass a registered space id, or a path to a folder holding a .navig/ directory.",
        )
        raise typer.Exit(1)

    new = normalize_space_name(_validate_slug(new_id))
    if new != new_id.strip().lower():
        # `foo-space` → `foo`, and the documented aliases: say what the id will actually be.
        ch.info(f"'{new_id}' normalises to '{new}' — that is the id every lookup will use.")

    manifest = load_space_manifest(target)
    old = normalize_space_name(manifest.resolved_id or target.name)
    # Under `~/.navig/spaces` the FOLDER NAME is an id — `resolve_space()` answers with
    # `<spaces>/<name>` whenever that directory exists, ahead of the registry and the
    # manifest. So a root space whose folder disagrees with its id has work to do even
    # when the id itself is already right, and `--move-folder` is how that is finished.
    # (The suggestion printed below used to name a command that took the early return
    # and reported "nothing to do" — advice that does not work is worse than none.)
    is_root_space = space_registry.source_for(target) == "root"
    can_move = is_root_space and target.name != new
    if old == new:
        if move_folder and can_move:
            moved, note = _move_space_folder(target, new)
            if moved is None:
                ch.error(f"The id is already '{new}', but the folder stayed: {note}")
                raise typer.Exit(1)
            if space_registry.entry_for(target) is not None and not space_registry.repath(
                target, moved
            ):
                ch.warning(
                    f"The folder moved to {moved} but its registry row still names {target}.",
                    details=f"Run `navig space doctor {moved}` to re-register it.",
                )
            ch.success(f"Folder renamed to match the id '{new}'.", details=str(moved))
            return
        ch.info(f"'{old}' is already the id of {target} — nothing to do.")
        if can_move:
            ch.info(
                f"Its folder is still named '{target.name}', so that name also resolves here: "
                f"navig space rename {new} {new} --move-folder"
            )
        return

    _refuse_if_id_taken(new, target, dry_run=dry_run, hint="rename")

    is_active = _space_is_active(target, old)
    registered = space_registry.entry_for(target) is not None
    if move_folder and not can_move:
        why = (
            "the folder already has that name"
            if target.name == new
            else f"it lives outside {_spaces_dir(create=False)}, so its folder name is yours, not an id"
        )
        ch.warning(f"--move-folder does not apply to this space: {why}.")
    # Labels DERIVED from the old id follow it; a label someone chose stays.
    follow_name = manifest.get("name") == old
    follow_display = manifest.get("display_name") == _display_name_for(old)
    navig_md = target / "NAVIG.md"
    follow_md = False
    if navig_md.is_file():
        try:
            follow_md = f"\nspace: {old}\n" in navig_md.read_text(encoding="utf-8")[:2000]
        except OSError:
            follow_md = False

    plan = [f"space.json id: {old} → {new}"]
    if follow_name:
        plan.append(f"space.json name: {old} → {new}")
    if follow_display:
        plan.append(f"space.json display_name: {_display_name_for(old)} → {_display_name_for(new)}")
    if follow_md:
        plan.append(f"NAVIG.md frontmatter: space: {old} → {new}")
    plan.append("registry row re-keyed" if registered else "registry: not registered — untouched")
    if is_active:
        plan.append(f"active space pointer: {old} → {new}")
    if move_folder and can_move:
        plan.append(f"folder: {target.name} -> {new} (junctions relinked)")
        if registered:
            plan.append("registry path follows the folder")
    elif target.name != new:
        plan.append(f"folder stays {target} (the folder is never moved unless asked)")

    if dry_run:
        ch.info(f"Would rename '{old}' → '{new}' for {target}:", details="\n".join(plan))
        ch.info("(dry run — nothing was written)")
        return

    try:
        set_manifest_field(target, "id", new, id_hint=new)
        if follow_name:
            set_manifest_field(target, "name", new, id_hint=new)
        if follow_display:
            set_manifest_field(target, "display_name", _display_name_for(new), id_hint=new)
    except ManifestNotWritable as exc:
        ch.error(f"Could not write the manifest for {target}: {exc}", details="Nothing was changed.")
        raise typer.Exit(1) from exc
    if follow_md:
        _retarget_navig_md_space(target, old, new)

    if move_folder and can_move:
        moved, note = _move_space_folder(target, new)
        if moved is None:
            ch.error(
                f"Renamed the manifest, but the folder stayed: {note}",
                details=f"The id is now '{new}'; finish with `navig space rename {target} {new} "
                        f"--move-folder` once the folder is free, or leave it and accept that "
                        f"'{old}' still resolves here.",
            )
            raise typer.Exit(1)
        if registered and not space_registry.repath(target, moved):
            ch.warning(
                f"The folder moved to {moved} but its registry row still names {target}.",
                details=f"Run `navig space doctor {moved}` to re-register it.",
            )
        target = moved

    try:
        space_registry.rename(target, new)
    except ValueError as exc:  # a holder appeared between the check and the write
        ch.error(
            f"Manifest renamed, registry NOT: {exc}",
            details=f"Run `navig space doctor {target}` — the registry re-derives from the manifest.",
        )
        raise typer.Exit(1) from exc

    if is_active:
        _set_active_space(new)

    ch.success(f"Renamed space '{old}' → '{new}'.", details="\n".join(plan))
    if not registered:
        ch.info(f"Register it when you want it in the deck: navig space register {target}")
    if can_move and not move_folder:
        # Not a nicety: the old name keeps resolving here, and it blocks that id.
        ch.warning(
            f"The folder is still named '{target.name}', so '{target.name}' also still "
            f"resolves to this space.",
            details=f"`navig space use {old}` and `{old}`-addressed lookups land here, and a new "
                    f"space given the id '{old}' would collide with this folder. Rename it too:\n"
                    f"  navig space rename {new} {new} --move-folder  (no-op on the id, moves the folder)",
        )


# ── Fold: demote a nested space so it never claims a top-level id ────────────


@space_app.command("fold")
def space_fold(
    path: Path = typer.Argument(..., help="Path to the sub-space folder to fold"),
) -> None:
    """Demote a sub-space: keep it working, hide it from discovery and the registry.

    A folder with a `.navig/` is claimed as a top-level space the moment anyone cds into
    it — and permanently, because discovery registers before it filters on enabled, so
    `disable` cannot take the id back. Folding writes `.navig/.folded` and releases any
    id already claimed. The folder keeps working normally when you are inside it.
    """
    import json  # noqa: PLC0415
    from datetime import datetime, timezone  # noqa: PLC0415

    from navig.spaces import registry as space_registry  # noqa: PLC0415
    from navig.spaces.resolver import FOLD_MARKER  # noqa: PLC0415
    from navig.spaces.space_manifest import is_space_dir  # noqa: PLC0415

    target = resolve_user_path(path)
    if not target.is_dir() or not is_space_dir(target):
        ch.error(
            f"Not a space: {target}",
            details="A space is a folder containing a .navig/ directory.",
        )
        raise typer.Exit(2)

    marker = target / ".navig" / FOLD_MARKER
    if marker.is_file():
        ch.info(f"'{target.name}' is already folded.", details=str(marker))
        return

    try:
        marker.write_text(
            json.dumps(
                {
                    "folded_at": datetime.now(timezone.utc).isoformat(),
                    "folded_by": "navig space fold",
                    "parent": str(target.parent),
                    "note": "Sub-space: excluded from discovery and spaces.json. Undo: navig space unfold",
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError as exc:
        ch.error(f"Could not write the fold marker: {exc}", details=str(marker))
        raise typer.Exit(1) from exc

    released = space_registry.forget(str(target))
    detail = "released its registry entry" if released else "was not registered"
    ch.success(f"Folded '{target.name}' ({detail}).", details=str(target))


@space_app.command("unfold")
def space_unfold(
    path: Path = typer.Argument(..., help="Path to the folded sub-space to restore"),
) -> None:
    """Undo `space fold` — the folder becomes a discoverable space again.

    Reverses either spelling: the `.navig/.folded` marker, or a hand-renamed
    `.navig.folded/` directory (restored to `.navig/`).
    """
    from navig.spaces.resolver import FOLD_MARKER, FOLDED_DIR  # noqa: PLC0415
    from navig.spaces.space_manifest import is_space_dir  # noqa: PLC0415

    target = resolve_user_path(path)
    hard = target / FOLDED_DIR
    if not target.is_dir() or not (is_space_dir(target) or hard.is_dir()):
        ch.error(
            f"Not a space: {target}",
            details="A space is a folder containing a .navig/ (or .navig.folded/) directory.",
        )
        raise typer.Exit(2)

    # Hard fold: the whole dir was renamed away. Put it back.
    if hard.is_dir():
        live = target / ".navig"
        if live.exists():
            ch.error(
                f"Cannot restore {FOLDED_DIR}: a .navig/ already exists.",
                details=f"Merge them by hand, then delete {hard}.",
            )
            raise typer.Exit(1)
        try:
            hard.rename(live)
        except OSError as exc:
            ch.error(f"Could not restore {FOLDED_DIR}: {exc}", details=str(hard))
            raise typer.Exit(1) from exc
        ch.success(
            f"Unfolded '{target.name}' — {FOLDED_DIR} restored to .navig/.",
            details=str(target),
        )
        return

    marker = target / ".navig" / FOLD_MARKER
    if not marker.is_file():
        ch.info(f"'{target.name}' is not folded — nothing to undo.")
        return

    try:
        marker.unlink()
    except OSError as exc:
        ch.error(f"Could not remove the fold marker: {exc}", details=str(marker))
        raise typer.Exit(1) from exc

    ch.success(
        f"Unfolded '{target.name}' — it will be discovered again.", details=str(target)
    )


# Backward-compatible function name used by tests/importers.
space_new = space_create


# `navig wire` is a flat top-level command, but users reasonably expect it under
# the space group too — register the same implementation as `navig space wire`.
# Import lazily at module tail so space.py is fully defined before wire.py (which
# reuses this module's scaffold helpers) is imported.
try:  # pragma: no cover - registration glue
    from navig.commands.wire import wire_command as _wire_command

    space_app.command("wire", help="Wire this folder into the agent ecosystem (alias of `navig wire`).")(_wire_command)
except Exception:  # noqa: BLE001
    pass


# `navig space media` — the code/media split: verify and repair the `.media`
# directory links that attach a lean code tree to a separate media tree. Kept in
# its own module so this one stays readable; mounted here because the links are
# space wiring, not a separate product.
try:  # pragma: no cover - registration glue
    from navig.commands.space_media import media_app as _media_app

    space_app.add_typer(_media_app, name="media")
except Exception:  # noqa: BLE001
    pass
