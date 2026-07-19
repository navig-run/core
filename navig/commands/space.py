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
# zones follow the repo-cleanup convention (.navig, .dev and .local are all
# gitignored — machine-local/private; only docs/ and source are committed).

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
    ".gitignore": (
        "# ── navig: machine-local / private — never commit ──\n"
        ".navig/\n.inbox\n.lab/\n.backup/\n.local/\n.dev/\n\n"
        "# ── build / cache / IDE artifacts ──\n"
        ".next/\n.open-next/\n.wrangler/\n.venv/\n.pytest_cache/\n"
        ".tmp/\n.core-sync-tmp/\n.idea/\n\n"
        "# ── logs & temp ──\n*.log\n\n"
        "# ── OS / editor junk ──\n.DS_Store\nThumbs.db\n"
    ),
}

# NAVIG.md is the canonical project-context file. Assistant files (CLAUDE.md,
# GEMINI.md, …) are thin pointers to it, so there is one source of truth and no
# drift. Generated regions inside these files are fenced by markers so appends
# (e.g. `navig wire`) never depend on a heading surviving edits.
_CTX_MARKER_START = "<!-- navig:context:start -->"
_CTX_MARKER_END = "<!-- navig:context:end -->"
_AGENT_MARKER_START = "<!-- navig:agent-instructions:start -->"
_AGENT_MARKER_END = "<!-- navig:agent-instructions:end -->"


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
    """Resolve a doctor target: a path, a registered space name, or (default) the cwd."""
    if not target:
        return Path.cwd()
    p = Path(target).expanduser()
    if p.is_dir():
        return p.resolve()
    try:
        from navig.spaces.resolver import discover_space_paths  # noqa: PLC0415

        cfg = discover_space_paths(include_disabled=True).get(_validate_slug(target))
        if cfg and cfg.path.is_dir():
            return cfg.path
    except Exception:  # noqa: BLE001
        pass
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
        reg.append(chk(registered, "registered in ~/.navig/spaces.json",
                       "" if registered else "not indexed — run --fix to add it", warn=not registered))
    except Exception:  # noqa: BLE001
        reg.append(chk(True, "registry", "check skipped", warn=True))
    groups.append({"name": "Registry", "checks": reg})

    # 5) AI assistants — is the space legible to each agent tool (Claude/Copilot/Cursor/…)?
    agents = []
    for display, present in _agent_status(space_path, name):
        agents.append(chk(present, display,
                          "" if present else "not set up (optional)",
                          warn=not present, action="agents"))
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
    # Auto-wire capability junctions so the space's own skills (e.g. /inbox) are
    # immediately visible to Claude Code — no separate `navig wire` needed for the basics.
    cap_msgs = [] if (no_links or dry_run) else _link_space_capabilities(space_path)

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
        ch.info(f"  {'[dry-run] would migrate' if dry_run else 'migrated'}: {m}")
    if dry_run:
        ch.info(f"[dry-run] Would create {nc} item(s) in {space_path}; {ns} already present (kept).")
        for item in summary["created"]:
            ch.info(f"  + {item}")
        if book:
            ch.info(f"[dry-run] Would set the finance book: {book}")
    else:
        verb = "Initialized structure in existing" if existed else "Created"
        ch.success(f"{verb} space '{name}'.", details=str(space_path))
        if book:
            ch.info(f"  book: {book} — separate finance ledger (Harbor)")
        ch.info(f"+{nc} created · {ns} existing left untouched · "
                ".navig/{plans,inbox,memory,state,wiki} · .dev/ · .local/ · docs/ "
                "(.navig/.dev/.local gitignored)")
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

    issue_count = (
        len(bare_pairs) + len(dup_workspace_ids) + len(dup_registry_ids) + len(orphans)
    )
    return {
        "roots": [str(r) for r in roots],
        "spaces_scanned": len(inventory),
        "issue_count": issue_count,
        "bare_vs_space_pairs": bare_pairs,
        "duplicate_workspace_ids": dup_workspace_ids,
        "duplicate_registry_ids": dup_registry_ids,
        "orphan_registry_paths": orphans,
    }


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
    try:
        from navig.spaces import registry as _registry  # noqa: PLC0415

        _registry.ensure_registered(space_path, id=name, name=name, source="root")
    except Exception:  # noqa: BLE001
        pass
    return len(summary["created"])


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
            candidate = Path(space).expanduser()
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


# `navig wire` is a flat top-level command, but users reasonably expect it under
# the space group too — register the same implementation as `navig space wire`.
# Import lazily at module tail so space.py is fully defined before wire.py (which
# reuses this module's scaffold helpers) is imported.
try:  # pragma: no cover - registration glue
    from navig.commands.wire import wire_command as _wire_command

    space_app.command("wire", help="Wire this folder into the agent ecosystem (alias of `navig wire`).")(_wire_command)
except Exception:  # noqa: BLE001
    pass
