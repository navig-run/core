"""navig.commands.wire — wire a folder into the agent ecosystem (link-not-copy).

``navig wire [path]`` turns any directory into a first-class NAVIG **workshop**
*and* makes its capabilities visible to Claude Code (and other agents) by
**junctioning** them under ``.claude/`` — no copying, so editing a skill in one
place is live everywhere.

What it does (idempotent · ``--dry-run`` previews · ``--force`` re-links):
  * Ensures the ``.navig/`` workshop skeleton + a canonical ``space.json``
    (reuses ``space._scaffold_space_skeleton`` — purely additive, never clobbers).
  * Junctions capabilities into ``.claude/`` (NTFS junction on Windows, symlink
    on POSIX — reuses ``mount._create_junction``):
        .claude/skills        → .navig/skills
        .claude/agents        → .navig/agents
        .claude/output-styles → .navig/personas
        .claude/rules         → .navig/rules
  * Root convenience links ``.wiki`` → ``.navig/wiki`` and ``.docs`` → ``docs``
    (``./plans`` and ``./inbox`` are linked by the skeleton).
  * Dev hygiene folders ``.lab/`` · ``.backup/`` · ``tests/`` · ``scripts/``
    (created only if missing — existing folders are left untouched).
  * A ``.claude/settings.json`` (SessionStart space-context hook) when absent.
  * A ``.lab`` rule (``.claude/rules/lab.md``) + a one-line CLAUDE.md note:
    *the lab is the inspiration corpus — copy & improve, don't write from zero*.
  * Registers the workshop in ``~/.navig/spaces.json`` (enabled).

**Never destructive:** it only creates what's missing and links into ``.navig``;
state (memory / plans / inbox) is never touched.
"""
from __future__ import annotations

import json
from pathlib import Path

import typer

from navig import console_helper as ch

# capability link → source (relative to the workshop root)
_CLAUDE_LINKS: tuple[tuple[str, str], ...] = (
    (".claude/skills", ".navig/skills"),
    (".claude/agents", ".navig/agents"),
    (".claude/output-styles", ".navig/personas"),
    (".claude/rules", ".navig/rules"),
)

# root convenience links (./plans + ./inbox are handled by the skeleton)
_ROOT_LINKS: tuple[tuple[str, str], ...] = (
    (".wiki", ".navig/wiki"),
    (".docs", "docs"),
)

# dev hygiene folders created only if missing
_DEV_DIRS: tuple[str, ...] = (".lab", ".backup", "tests", "scripts")

_GITIGNORE_START = "# ── navig wire (managed) ── do not edit between markers"
_GITIGNORE_END = "# ── end navig wire (managed) ──"
_GITIGNORE_BLOCK = (
    "\n" + _GITIGNORE_START + "\n"
    "# linked capability junctions (live under .claude/, sources in .navig/)\n"
    ".claude/skills\n.claude/agents\n.claude/output-styles\n.claude/rules\n"
    "# machine-local / private — never commit\n"
    ".navig/\n.lab/\n.local/\n.dev/\n.backup/\n.wiki\n.docs\n"
    "# build / cache / IDE artifacts\n"
    ".next/\n.open-next/\n.wrangler/\n.venv/\n.pytest_cache/\n"
    ".tmp/\n.core-sync-tmp/\n.idea/\n"
    + _GITIGNORE_END + "\n"
)

_LAB_RULE = """\
# The `.lab/` corpus — copy & improve, don't reinvent

`.lab/` is this workshop's **inspiration corpus**: vendored reference projects
and proven patterns. It is *not* shipped code.

When building something new, browse `.lab/` first. If a lab project already
solved a similar problem, **lift the concept and reimplement it natively** here —
prefer copying & improving proven structure over writing from zero. Never import
lab code directly into shipped paths.
"""

_CLAUDE_LAB_NOTE = (
    "\n## The lab\n\n"
    "`.lab/` is the inspiration corpus — vendored references, not shipped code. "
    "Prefer copying & *improving* proven structure/code over writing from zero "
    "(see `.claude/rules/lab.md`).\n"
)

_SETTINGS_JSON = {
    "$schema": "https://json.schemastore.org/claude-code-settings.json",
    "hooks": {
        "SessionStart": [
            {
                "hooks": [
                    {
                        "type": "command",
                        "command": "navig space current",
                    }
                ]
            }
        ]
    },
}


def _workshop_name(target: Path) -> str:
    """Prefer the manifest id/display name; fall back to the folder name."""
    try:
        from navig.spaces.space_manifest import load_space_manifest  # noqa: PLC0415

        m = load_space_manifest(target)
        return m.resolved_id or m.resolved_name or target.name
    except Exception:  # noqa: BLE001
        return target.name


def wire_command(
    path: Path | None = typer.Argument(
        None, help="Folder to wire (default: current directory)."
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Preview every action — change nothing."
    ),
    force: bool = typer.Option(
        False, "--force", help="Re-create capability junctions even if they exist."
    ),
    no_register: bool = typer.Option(
        False, "--no-register", help="Skip adding the workshop to the spaces registry."
    ),
) -> None:
    """Wire a folder into the agent ecosystem (workshop + .claude capability links)."""
    from navig.commands.mount import _create_junction, _remove_junction  # noqa: PLC0415
    from navig.commands.space import (  # noqa: PLC0415
        _link_space_roots,
        _scaffold_space_skeleton,
    )

    target = (path.expanduser().resolve() if path else Path.cwd())
    if target.exists() and not target.is_dir():
        ch.error(f"Cannot wire {target}", details="A file exists at that path.")
        raise typer.Exit(1)

    name = _workshop_name(target) if (target / ".navig").exists() else target.name
    actions: list[str] = []

    # 1) Ensure the workshop skeleton + space.json (additive).
    summary = _scaffold_space_skeleton(target, name, dry_run=dry_run)
    if summary["created"]:
        actions.append(f"skeleton: +{len(summary['created'])} item(s)")
    if not dry_run:
        for m in _link_space_roots(target):
            actions.append(f"link {m}")

    # 2) Capability junctions into .claude/ + root convenience links.
    def _link(rel_link: str, rel_source: str) -> None:
        link = target / rel_link
        source = target / rel_source
        if not source.exists():
            if not dry_run:
                source.mkdir(parents=True, exist_ok=True)
            actions.append(f"mkdir {rel_source}/ (link source)")
        if link.exists() or link.is_symlink():
            if not force:
                actions.append(f"skip {rel_link} (exists)")
                return
            if not dry_run:
                _remove_junction(link)
        if dry_run:
            actions.append(f"would link {rel_link} → {rel_source}")
            return
        err = _create_junction(source, link)
        actions.append(f"link {rel_link} → {rel_source}" if err is None else f"{rel_link} FAILED: {err}")

    for rel_link, rel_source in (*_CLAUDE_LINKS, *_ROOT_LINKS):
        _link(rel_link, rel_source)

    # 3) Dev hygiene folders (create only if missing).
    for d in _DEV_DIRS:
        p = target / d
        if p.exists():
            actions.append(f"skip {d}/ (exists)")
        elif dry_run:
            actions.append(f"would mkdir {d}/")
        else:
            p.mkdir(parents=True, exist_ok=True)
            actions.append(f"mkdir {d}/")

    # 4) .claude/settings.json (SessionStart space-context hook) — only if absent.
    settings = target / ".claude" / "settings.json"
    if settings.exists():
        actions.append("skip .claude/settings.json (exists)")
    elif dry_run:
        actions.append("would write .claude/settings.json")
    else:
        settings.parent.mkdir(parents=True, exist_ok=True)
        settings.write_text(json.dumps(_SETTINGS_JSON, indent=2) + "\n", encoding="utf-8")
        actions.append("write .claude/settings.json")

    # 5) .lab rule + CLAUDE.md note.
    lab_rule = target / ".claude" / "rules" / "lab.md"
    if lab_rule.exists():
        actions.append("skip .claude/rules/lab.md (exists)")
    elif dry_run:
        actions.append("would write .claude/rules/lab.md")
    else:
        lab_rule.parent.mkdir(parents=True, exist_ok=True)
        lab_rule.write_text(_LAB_RULE, encoding="utf-8")
        actions.append("write .claude/rules/lab.md")

    claude_md = target / "CLAUDE.md"
    if not dry_run and claude_md.is_file():
        try:
            existing = claude_md.read_text(encoding="utf-8")
            if "## The lab" not in existing and ".lab/" not in existing:
                claude_md.write_text(existing.rstrip("\n") + "\n" + _CLAUDE_LAB_NOTE, encoding="utf-8")
                actions.append("append .lab note → CLAUDE.md")
        except OSError:
            pass

    # 6) .gitignore managed block — refresh in place between markers, else append.
    gitignore = target / ".gitignore"
    if not dry_run:
        try:
            existing = gitignore.read_text(encoding="utf-8") if gitignore.is_file() else ""
            if _GITIGNORE_START in existing and _GITIGNORE_END in existing:
                s = existing.index(_GITIGNORE_START)
                e = existing.index(_GITIGNORE_END) + len(_GITIGNORE_END)
                new = (existing[:s].rstrip("\n") + "\n" + _GITIGNORE_BLOCK.strip("\n")
                       + "\n" + existing[e:].lstrip("\n")).strip("\n") + "\n"
                if new != existing:
                    gitignore.write_text(new, encoding="utf-8")
                    actions.append("refresh .gitignore (managed block)")
            elif _GITIGNORE_START not in existing:
                gitignore.write_text(
                    existing.rstrip("\n") + "\n" + _GITIGNORE_BLOCK if existing
                    else _GITIGNORE_BLOCK.lstrip("\n"),
                    encoding="utf-8",
                )
                actions.append("update .gitignore (managed block)")
        except OSError:
            pass

    # 7) Register in the spaces registry (enabled).
    if not no_register and not dry_run:
        try:
            from navig.spaces import registry as _registry  # noqa: PLC0415

            under_home = str(target).startswith(str(Path.home() / ".navig" / "spaces"))
            _registry.register(
                target, id=name, name=name,
                source="root" if under_home else "external", enabled=True,
            )
            actions.append("register in spaces.json (enabled)")
        except Exception:  # noqa: BLE001
            pass

    # ── Report ────────────────────────────────────────────────────────────────
    if dry_run:
        ch.info(f"dry-run · navig wire {target}")
        for a in actions:
            ch.info(f"  · {a}")
        ch.info(f"dry-run · {len(actions)} action(s) — nothing written.")
        return

    ch.success(f"Wired workshop '{name}'.", details=str(target))
    for a in actions:
        ch.info(f"  · {a}")
    ch.info("Capabilities under .claude/ are live links — edit in .navig/ or .claude/, both update.")
