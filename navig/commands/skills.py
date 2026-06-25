"""Skill listing, metadata, show, and execution helpers."""

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from navig import console_helper as ch


@dataclass(frozen=True)
class SkillCommand:
    """A single command declared by a skill."""

    name: str
    syntax: str
    description: str
    risk: str = "safe"
    confirmation_msg: str | None = None


@dataclass(frozen=True)
class SkillInfo:
    name: str
    description: str
    category: str
    rel_path: str
    file_path: Path
    version: str = "0.0.1"
    risk_level: str = "safe"
    user_invocable: bool = True
    commands: tuple = ()  # tuple[SkillCommand, ...]
    examples: tuple = ()  # tuple[dict, ...]
    entrypoint: str | None = None  # e.g. "index.js", "main.py"
    raw_frontmatter: dict[str, Any] = field(default_factory=dict)


def _resolve_skills_dirs(explicit_dir: str | None) -> list[Path]:
    if explicit_dir:
        candidate = Path(explicit_dir).expanduser().resolve()
        if candidate.exists():
            return [candidate]
        return []

    base_dir = Path(__file__).resolve()
    candidates = [
        base_dir.parent / "skills",
        base_dir.parent.parent / "skills",
        base_dir.parent.parent.parent / "store" / "skills",  # canonical store
        base_dir.parent.parent.parent / "skills",  # legacy fallback
    ]

    unique: list[Path] = []
    seen = set()
    for candidate in candidates:
        if not candidate.exists():
            continue
        resolved = candidate.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        unique.append(candidate)

    return unique


def _load_frontmatter(skill_file: Path) -> dict[str, Any]:
    content = skill_file.read_text(encoding="utf-8")
    if not content.startswith("---"):
        return {}

    parts = content.split("---", 2)
    if len(parts) < 3:
        return {}

    try:
        import yaml

        return yaml.safe_load(parts[1]) or {}
    except Exception:
        return {}


def _collect_skills(skills_dirs: Iterable[Path]) -> list[SkillInfo]:
    skills: list[SkillInfo] = []
    seen_files = set()

    for skills_dir in skills_dirs:
        for skill_file in skills_dir.rglob("SKILL.md"):
            resolved = skill_file.resolve()
            if resolved in seen_files:
                continue
            seen_files.add(resolved)

            frontmatter = _load_frontmatter(skill_file)
            rel_path = skill_file.relative_to(skills_dir).as_posix()
            parts = rel_path.split("/")
            category = parts[0] if len(parts) > 1 else "root"
            name = frontmatter.get("name") or skill_file.parent.name
            description = frontmatter.get("description") or ""

            # Parse navig-commands
            raw_cmds = frontmatter.get("navig-commands", [])
            parsed_commands: list[SkillCommand] = []
            for c in raw_cmds:
                if isinstance(c, dict):
                    parsed_commands.append(
                        SkillCommand(
                            name=c.get("name", ""),
                            syntax=c.get("syntax", ""),
                            description=c.get("description", ""),
                            risk=c.get("risk", "safe"),
                            confirmation_msg=c.get("confirmation_msg"),
                        )
                    )
                elif isinstance(c, str):
                    # Simple form: "navig docker ps"
                    parsed_commands.append(
                        SkillCommand(
                            name=c.split()[-1] if c.strip() else c,
                            syntax=c,
                            description="",
                            risk="safe",
                        )
                    )

            # Parse examples
            raw_examples = frontmatter.get("examples", [])
            parsed_examples = tuple(
                e if isinstance(e, dict) else {"user": str(e)} for e in raw_examples
            )

            # Detect entrypoint (script files in same dir)
            skill_dir = skill_file.parent
            entrypoint = None
            for ename in ("main.py", "index.py", "run.py", "index.js", "main.js"):
                if (skill_dir / ename).exists():
                    entrypoint = ename
                    break
            # Check skill.json for entrypoint override
            skill_json = skill_dir / "skill.json"
            if skill_json.exists():
                try:
                    sj = json.loads(skill_json.read_text(encoding="utf-8"))
                    entrypoint = sj.get("entrypoint", entrypoint)
                except Exception:  # noqa: BLE001
                    pass  # best-effort; failure is non-critical

            skills.append(
                SkillInfo(
                    name=name,
                    description=description,
                    category=category,
                    rel_path=rel_path,
                    file_path=skill_file,
                    version=frontmatter.get("version", "0.0.1"),
                    risk_level=frontmatter.get("risk-level", "safe"),
                    user_invocable=frontmatter.get("user-invocable", True),
                    commands=tuple(parsed_commands),
                    examples=parsed_examples,
                    entrypoint=entrypoint,
                    raw_frontmatter=frontmatter,
                )
            )

    skills.sort(key=lambda item: (item.category, item.name))
    return skills


def list_skills_cmd(options: dict[str, Any]) -> list[SkillInfo]:
    skills_dirs = _resolve_skills_dirs(options.get("skills_dir"))
    if not skills_dirs:
        ch.error("Skills directory not found.")
        ch.dim("  Expected a skills/ directory relative to NAVIG, or pass --dir.")
        ch.dim("  Example: navig skills list --dir C:/path/to/skills")
        return []

    skills = _collect_skills(skills_dirs)

    want_json = bool(options.get("json"))
    want_plain = bool(options.get("plain"))

    if want_json:
        payload = {
            "skills": [
                {
                    "name": skill.name,
                    "description": skill.description,
                    "category": skill.category,
                    "path": skill.rel_path,
                }
                for skill in skills
            ],
            "count": len(skills),
        }
        ch.raw_print(json.dumps(payload, indent=2))
        return skills

    if want_plain:
        for skill in skills:
            ch.raw_print(f"{skill.category}/{skill.name}")
        return skills

    if not skills:
        ch.warning("No skills found.")
        ch.dim("  Add SKILL.md files under skills/<category>/<skill-name>/.")
        return skills

    table = ch.Table(title="NAVIG Skills")
    table.add_column("Category", style="cyan")
    table.add_column("Name", style="yellow")
    table.add_column("Description", style="green")

    for skill in skills:
        table.add_row(skill.category, skill.name, skill.description)

    ch.console.print(table)
    ch.dim(f"\nTotal: {len(skills)} skills")
    return skills


def _build_tree(skills: Iterable[SkillInfo]) -> dict[str, list[str]]:
    tree: dict[str, list[str]] = {}
    for skill in skills:
        tree.setdefault(skill.category, []).append(skill.name)

    for category in tree:
        tree[category].sort()

    return dict(sorted(tree.items()))


def tree_skills_cmd(options: dict[str, Any]) -> dict[str, list[str]]:
    skills_dirs = _resolve_skills_dirs(options.get("skills_dir"))
    if not skills_dirs:
        ch.error("Skills directory not found.")
        ch.dim("  Expected a skills/ directory relative to NAVIG, or pass --dir.")
        ch.dim("  Example: navig skills tree --dir C:/path/to/skills")
        return {}

    skills = _collect_skills(skills_dirs)
    tree = _build_tree(skills)

    want_json = bool(options.get("json"))
    want_plain = bool(options.get("plain"))

    if want_json:
        ch.raw_print(json.dumps({"tree": tree, "count": len(skills)}, indent=2))
        return tree

    if want_plain:
        for category, items in tree.items():
            for name in items:
                ch.raw_print(f"{category}/{name}")
        return tree

    if not tree:
        ch.warning("No skills found.")
        ch.dim("  Add SKILL.md files under skills/<category>/<skill-name>/.")
        return tree

    ch.console.print("NAVIG Skills Tree")
    for category, items in tree.items():
        ch.console.print(f"\n{category}:")
        for name in items:
            ch.console.print(f"  - {name}")

    ch.dim(f"\nTotal: {len(skills)} skills")
    return tree


# ============================================================================
# SKILL SHOW
# ============================================================================


def _find_skill(name: str, options: dict[str, Any]) -> SkillInfo | None:
    """Find a skill by name (case-insensitive, partial match)."""
    skills_dirs = _resolve_skills_dirs(options.get("skills_dir"))
    if not skills_dirs:
        return None
    skills = _collect_skills(skills_dirs)
    lower = name.lower()

    # Exact match first
    for s in skills:
        if s.name.lower() == lower:
            return s

    # category/name match
    for s in skills:
        key = f"{s.category}/{s.name}".lower()
        if key == lower:
            return s

    # Partial match (prefix)
    matches = [s for s in skills if s.name.lower().startswith(lower)]
    if len(matches) == 1:
        return matches[0]

    return None


def show_skill_cmd(name: str, options: dict[str, Any]) -> SkillInfo | None:
    """Show detailed information about a skill."""
    skill = _find_skill(name, options)

    want_json = bool(options.get("json"))
    want_plain = bool(options.get("plain"))

    if skill is None:
        ch.error(f"Skill not found: {name}")
        ch.dim("  Use 'navig skills list' to see available skills.")
        return None

    if want_json:
        payload = {
            "name": skill.name,
            "description": skill.description,
            "category": skill.category,
            "version": skill.version,
            "risk_level": skill.risk_level,
            "user_invocable": skill.user_invocable,
            "path": skill.rel_path,
            "entrypoint": skill.entrypoint,
            "commands": [
                {
                    "name": c.name,
                    "syntax": c.syntax,
                    "description": c.description,
                    "risk": c.risk,
                }
                for c in skill.commands
            ],
            "examples": list(skill.examples),
        }
        ch.raw_print(json.dumps(payload, indent=2))
        return skill

    if want_plain:
        ch.raw_print(f"name: {skill.name}")
        ch.raw_print(f"category: {skill.category}")
        ch.raw_print(f"version: {skill.version}")
        ch.raw_print(f"description: {skill.description}")
        ch.raw_print(f"risk: {skill.risk_level}")
        ch.raw_print(f"entrypoint: {skill.entrypoint or 'none'}")
        if skill.commands:
            ch.raw_print("commands:")
            for c in skill.commands:
                ch.raw_print(f"  {c.name}: {c.syntax}")
        return skill

    # Rich output
    from rich.panel import Panel
    from rich.table import Table
    from rich.text import Text

    header = Text()
    header.append(f"{skill.name}", style="bold cyan")
    header.append(f"  v{skill.version}", style="dim")
    header.append(f"  [{skill.category}]", style="yellow")

    lines = [f"[white]{skill.description}[/white]"]
    lines.append(
        f"[dim]Risk: {skill.risk_level} | Invocable: {'yes' if skill.user_invocable else 'no'}[/dim]"
    )
    lines.append(f"[dim]Path: {skill.rel_path}[/dim]")
    if skill.entrypoint:
        lines.append(f"[dim]Entrypoint: {skill.entrypoint}[/dim]")

    ch.console.print(Panel("\n".join(lines), title=header, border_style="cyan"))

    if skill.commands:
        cmd_table = Table(title="Commands", box=None, show_header=True, padding=(0, 2))
        cmd_table.add_column("Name", style="cyan")
        cmd_table.add_column("Syntax", style="green")
        cmd_table.add_column("Risk", style="yellow")
        cmd_table.add_column("Description", style="dim")

        for c in skill.commands:
            cmd_table.add_row(c.name, c.syntax, c.risk, c.description)

        ch.console.print(cmd_table)

    if skill.examples:
        ch.console.print("\n[bold white]Examples[/bold white]")
        for ex in skill.examples:
            user_text = ex.get("user", "")
            cmd_text = ex.get("command", "")
            if user_text:
                ch.console.print(f"  [dim]User:[/dim] {user_text}")
            if cmd_text:
                ch.console.print(f"  [cyan]> {cmd_text}[/cyan]")
            ch.console.print()

    return skill


# ============================================================================
# SKILL RUN
# ============================================================================


def run_skill_cmd(spec: str, extra_args: list[str], options: dict[str, Any]) -> int:
    """
    Run a skill command.

    Spec format: <skill-name>:<command-name>
    OR: <skill-name> (runs entrypoint if available)

    Returns exit code (0 = success).
    """
    # Parse spec
    if ":" in spec:
        skill_name, cmd_name = spec.split(":", 1)
    else:
        skill_name = spec
        cmd_name = None

    skill = _find_skill(skill_name, options)
    if skill is None:
        ch.error(f"Skill not found: {skill_name}")
        ch.dim("  Use 'navig skills list' to see available skills.")
        return 1

    # Case 1: Run a named command from the skill's navig-commands
    if cmd_name and skill.commands:
        target_cmd = None
        lower_cmd = cmd_name.lower()
        for c in skill.commands:
            if c.name.lower() == lower_cmd:
                target_cmd = c
                break

        if target_cmd is None:
            ch.error(f"Command '{cmd_name}' not found in skill '{skill.name}'.")
            available = ", ".join(c.name for c in skill.commands)
            ch.dim(f"  Available: {available}")
            return 1

        # Build the navig command from syntax
        syntax = target_cmd.syntax.strip()

        # Confirmation for risky commands
        if target_cmd.risk in ("destructive", "moderate"):
            msg = target_cmd.confirmation_msg or f"Run {target_cmd.risk} command: {syntax}?"
            if not options.get("yes", False):
                if not ch.confirm(msg):
                    ch.dim("  Cancelled.")
                    return 0

        # The syntax is a navig command like "navig docker ps"
        # Strip leading "navig " and invoke via CLI
        if syntax.lower().startswith("navig "):
            cli_args = syntax[6:].strip()
            # Replace placeholders with positional args
            for _i, arg in enumerate(extra_args):
                # Replace first <placeholder> or {placeholder}
                import re

                cli_args = re.sub(r"[<{][^>}]+[>}]", arg, cli_args, count=1)

            ch.dim(f"  > navig {cli_args}")
            return _invoke_navig(cli_args, options)
        else:
            # Raw shell command
            full_cmd = syntax
            for arg in extra_args:
                full_cmd += f" {shlex.quote(arg)}"
            ch.dim(f"  > {full_cmd}")
            return _invoke_shell(full_cmd, skill.file_path.parent)

    # Case 2: Run the skill's entrypoint (Python/JS script)
    if skill.entrypoint:
        entrypoint_path = skill.file_path.parent / skill.entrypoint
        if not entrypoint_path.exists():
            ch.error(f"Entrypoint not found: {entrypoint_path}")
            return 1

        ext = entrypoint_path.suffix.lower()
        if ext == ".py":
            cmd = [sys.executable, str(entrypoint_path)] + extra_args
        elif ext == ".js":
            cmd = ["node", str(entrypoint_path)] + extra_args
        else:
            ch.error(f"Unsupported entrypoint type: {ext}")
            ch.dim("  Supported: .py, .js")
            return 1

        ch.dim(f"  > {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd,
                cwd=str(skill.file_path.parent),
                capture_output=not sys.stdout.isatty(),
                text=True,
            )
            if result.stdout and not sys.stdout.isatty():
                print(result.stdout, end="")
            if result.stderr:
                print(result.stderr, end="", file=sys.stderr)
            return result.returncode
        except FileNotFoundError as e:
            ch.error(f"Runtime not found: {e}")
            return 1
        except Exception as e:
            ch.error(f"Skill execution failed: {e}")
            return 1

    # Case 3: Command specified but no matching commands list
    if cmd_name:
        ch.error(f"Skill '{skill.name}' has no registered commands.")
        ch.dim("  Add navig-commands to the SKILL.md frontmatter.")
        return 1

    # Case 4: No entrypoint, no command — show info
    ch.warning(f"Skill '{skill.name}' has no entrypoint or runnable commands.")
    ch.dim(f"  Use 'navig skills show {skill.name}' for details.")
    return 1


def _invoke_navig(cli_args: str, options: dict[str, Any]) -> int:
    """Invoke a navig subcommand by re-entering the CLI."""
    import shlex as _shlex

    try:
        # Re-invoke via subprocess to avoid re-entrant Typer issues
        cmd = [sys.executable, "-m", "navig"] + _shlex.split(cli_args)
        # Pass through global flags
        if options.get("host"):
            cmd.extend(["--host", options["host"]])
        if options.get("app"):
            cmd.extend(["--app", options["app"]])
        if options.get("yes"):
            cmd.append("--yes")
        if options.get("dry_run"):
            cmd.append("--dry-run")
        if options.get("json"):
            cmd.append("--json")

        result = subprocess.run(cmd, text=True)
        return result.returncode
    except Exception as e:
        ch.error(f"Failed to invoke navig: {e}")
        return 1


def _invoke_shell(command: str, cwd: Path) -> int:
    """Run a shell command safely in the skill's directory."""
    try:
        result = subprocess.run(  # noqa: S602  # dynamic shell dispatch
            command,
            shell=True,  # noqa: S602  # dynamic shell dispatch
            cwd=str(cwd),
            text=True,
        )
        return result.returncode
    except Exception as e:
        ch.error(f"Shell execution failed: {e}")
        return 1


# ============================================================================
# TYPER SUB-APP — extracted from navig/cli/__init__.py
# ============================================================================

import typer  # noqa: E402

from navig.cli._callbacks import show_subcommand_help  # noqa: E402

skills_app = typer.Typer(
    help="Manage AI skill definitions",
    invoke_without_command=True,
    no_args_is_help=False,
)


@skills_app.callback()
def skills_callback(ctx: typer.Context):
    """Skills management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("skills", ctx)
        raise typer.Exit()


@skills_app.command("install")
def skills_install(
    ctx: typer.Context,
    spec: str = typer.Argument(
        ...,
        help=(
            "Foreign: claude:<name> · openclaw:<name> · hermes:<name> · codex:<name> · "
            "a local path.  Community: github:navig-run/… · skill:owner/repo[@ref]"
        ),
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite if already installed."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing files."),
):
    """Install a skill — from another agent (Claude/OpenClaw/Hermes/Codex/local) or the community registry.

    Foreign skills are normalized into a canonical NAVIG ``SKILL.md`` under
    ``~/.navig/store/skills/<id>/`` (origin recorded in ``.skill.meta.json``).
    """
    from navig.skills import federation

    # 1) Foreign agent / local path / OpenClaw plugin bundle → normalize into the store.
    try:
        targets = federation.install_all(spec, force=force, dry_run=dry_run)
    except (ValueError, FileExistsError) as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from exc

    if targets is not None:
        if dry_run:
            ch.info(f"dry-run · would install {len(targets)} skill(s):")
            for t in targets:
                ch.info(f"  · {t.name} → {t}")
        else:
            ch.success(f"Installed {len(targets)} skill(s).", details=", ".join(t.name for t in targets))
            if len(targets) == 1:
                ch.info("Run it: navig skills run " + targets[0].name)
        return

    # 2) Community registry (GitHub-backed) — unchanged.
    from navig.commands.install import install_asset

    try:
        install_asset(spec, force=force, dry_run=dry_run)
    except (ValueError, SystemExit) as exc:
        raise typer.Exit(1) from exc


@skills_app.command("export")
def skills_export(
    skill_id: str = typer.Argument(..., help="Id of an installed/discovered skill."),
    fmt: str = typer.Option("claude", "--format", "-f", help="claude · hermes · openclaw"),
    dest: Path = typer.Option(
        Path("."), "--dest", "-o", help="Output directory (a <id>/ folder is created inside)."
    ),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing export."),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing files."),
):
    """Export a NAVIG skill in another agent's format so it can consume it."""
    from navig.skills import federation

    try:
        out = federation.export(skill_id, fmt, dest, force=force, dry_run=dry_run)
    except (ValueError, FileExistsError) as exc:
        ch.error(str(exc))
        raise typer.Exit(1) from exc

    if dry_run:
        ch.info(f"dry-run · would export '{skill_id}' ({fmt}) → {out}")
    else:
        ch.success(f"Exported '{skill_id}' as {fmt}.", details=str(out))


_SKILL_TEMPLATE = """\
---
name: {name}
description: {description}
allowed-tools: bash, read
platforms: linux, macos, windows
safety: safe
---

# {title}

Describe what this skill does and, importantly, **when it should be used** — the
description above is what NAVIG/Claude match against to auto-activate this skill.

## Steps
1. ...

## Examples
```bash
# example invocation
```
"""


@skills_app.command("new")
@skills_app.command("create")
def skills_new(
    name: str = typer.Argument(..., help="Skill name (kebab-case)"),
    description: str = typer.Option("", "--description", "-d", help="One-line skill description."),
    here: bool = typer.Option(False, "--here", help="Create ./<name> instead of the user store."),
    force: bool = typer.Option(False, "--force", "-f", help="Overwrite an existing SKILL.md."),
):
    """Scaffold a new Claude-compatible SKILL.md skill."""
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    if not slug:
        ch.error("Invalid skill name.")
        raise typer.Exit(1)

    if here:
        target = Path.cwd() / slug
    else:
        from navig.platform.paths import store_dir

        target = store_dir() / "skills" / slug
    skill_file = target / "SKILL.md"

    if skill_file.exists() and not force:
        ch.warning(f"'{slug}' already exists.", details=str(skill_file))
        raise typer.Exit(1)

    target.mkdir(parents=True, exist_ok=True)
    content = _SKILL_TEMPLATE.format(
        name=slug,
        description=description or f"TODO: when should '{slug}' be used?",
        title=slug.replace("-", " ").title(),
    )
    skill_file.write_text(content, encoding="utf-8")
    ch.success(f"Created skill '{slug}'.", details=str(skill_file))
    ch.info("Edit the description (auto-activation matches it), then: navig skills run " + slug)


def _installed_skill_ids() -> set[str]:
    try:
        from navig.platform.paths import store_dir

        d = store_dir() / "skills"
        return {p.name for p in d.iterdir() if p.is_dir()} if d.exists() else set()
    except Exception:  # noqa: BLE001
        return set()


def _load_community_registry() -> list[dict]:
    """Load the community CLI-skill registry from the workspace, else GitHub."""
    import json

    # 1. Local workspace copy (navig-community/cli-skills/registry.json)
    try:
        here = Path(__file__).resolve()
        for _ in range(8):
            here = here.parent
            cand = here / "navig-community" / "cli-skills" / "registry.json"
            if cand.exists():
                data = json.loads(cand.read_text(encoding="utf-8"))
                return data.get("skills", []) if isinstance(data, dict) else list(data or [])
    except Exception:  # noqa: BLE001
        pass
    # 2. Remote
    try:
        import urllib.request

        url = "https://raw.githubusercontent.com/navig-run/community/main/cli-skills/registry.json"
        req = urllib.request.Request(url, headers={"User-Agent": "navig-cli"})  # noqa: S310 — https
        with urllib.request.urlopen(req, timeout=20) as resp:  # noqa: S310
            data = json.loads(resp.read().decode("utf-8"))
        return data.get("skills", []) if isinstance(data, dict) else list(data or [])
    except Exception:  # noqa: BLE001
        return []


def compute_skill_suggestions(path: str = ".", limit: int = 6) -> tuple[list[str], str, list[dict]]:
    """Detect stack + active space and rank community skills. Returns (stack, space, picks).

    Reused by ``navig skill suggest`` and the onboarding flow. Never raises — on
    any failure it returns whatever it has (possibly an empty pick list).
    """
    import re as _re

    try:
        from navig.commands.project_inspect import inspect_path

        info = inspect_path(path)
    except Exception:  # noqa: BLE001
        info = {}
    stack = [str(s) for s in (info.get("stack") or [])]
    meta = info.get("metadata") or {}

    space = ""
    try:
        from navig.commands.space import get_active_space

        space = get_active_space() or ""
    except Exception:  # noqa: BLE001
        pass

    registry = _load_community_registry()
    if not registry:
        return stack, space, []

    words: set[str] = set()
    for token in stack:
        words.update(_re.split(r"[^a-z0-9]+", token.lower()))
    for v in meta.values():
        if isinstance(v, str):
            words.update(_re.split(r"[^a-z0-9]+", v.lower()))
    words.discard("")

    installed = _installed_skill_ids()
    scored: list[tuple[int, dict]] = []
    for sk in registry:
        sid = str(sk.get("id", ""))
        if not sid or sid in installed:
            continue
        hay = " ".join(str(sk.get(k, "")) for k in ("id", "category", "description")).lower()
        hay += " " + " ".join(str(c) for c in (sk.get("commands") or [])).lower()
        score = sum(1 for w in words if w and w in hay)
        if sk.get("category") in ("developer", "system"):
            score += 1  # generally useful for any code project
        if score > 0:
            scored.append((score, sk))

    scored.sort(key=lambda t: t[0], reverse=True)
    return stack, space, [sk for _, sk in scored[:limit]]


@skills_app.command("suggest")
def skills_suggest(
    path: str = typer.Option(".", "--path", help="Project directory to inspect."),
    install: bool = typer.Option(False, "--install", help="Install the suggested skills."),
    limit: int = typer.Option(6, "--limit", "-n", help="Max suggestions."),
):
    """Detect this project's stack + active space and recommend community skills."""
    stack, space, picks = compute_skill_suggestions(path, limit)

    ch.info(f"Stack: {', '.join(stack) or 'unknown'}" + (f"   ·   Space: {space}" if space else ""))

    if not picks:
        ch.info("No stack-specific suggestions yet. Browse: navig skill install github:navig-run/community/...")
        return

    ch.info("Suggested skills:")
    for sk in picks:
        cmd = sk.get("install") or (
            f"navig skill install github:navig-run/community/cli-skills/{sk.get('category')}/{sk.get('id')}"
        )
        ch.info(f"  • {sk.get('id')} — {sk.get('description', '')}")
        if not install:
            ch.info(f"      {cmd}")

    if install:
        from navig.commands.install import install_asset

        for sk in picks:
            spec = f"github:navig-run/community/cli-skills/{sk.get('category')}/{sk.get('id')}"
            try:
                install_asset(spec, force=False)
            except Exception as exc:  # noqa: BLE001
                ch.warning(f"  failed: {sk.get('id')} ({exc})")


@skills_app.command("list")
def skills_list(
    ctx: typer.Context,
    skills_dir: Path | None = typer.Option(
        None,
        "--dir",
        help="Optional skills directory override",
    ),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """List available AI skills."""
    ctx.obj["plain"] = plain
    if json_output:
        ctx.obj["json"] = True
    if skills_dir:
        ctx.obj["skills_dir"] = str(skills_dir)
    list_skills_cmd(ctx.obj)


@skills_app.command("tree")
def skills_tree(
    ctx: typer.Context,
    skills_dir: Path | None = typer.Option(
        None,
        "--dir",
        help="Optional skills directory override",
    ),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show skills grouped by category."""
    ctx.obj["plain"] = plain
    if json_output:
        ctx.obj["json"] = True
    if skills_dir:
        ctx.obj["skills_dir"] = str(skills_dir)
    tree_skills_cmd(ctx.obj)


@skills_app.command("show")
def skills_show(
    ctx: typer.Context,
    name: str = typer.Argument(
        ...,
        help="Skill name (e.g., 'docker-manage', 'git-basics', 'official/docker-ops')",
    ),
    skills_dir: Path | None = typer.Option(
        None,
        "--dir",
        help="Optional skills directory override",
    ),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show detailed skill information (commands, examples, metadata)."""
    ctx.obj["plain"] = plain
    if json_output:
        ctx.obj["json"] = True
    if skills_dir:
        ctx.obj["skills_dir"] = str(skills_dir)
    show_skill_cmd(name, ctx.obj)


@skills_app.command("run")
def skills_run(
    ctx: typer.Context,
    spec: str = typer.Argument(
        ...,
        help="Skill spec: <skill-name>:<command> or <skill-name> (runs entrypoint)",
    ),
    args: list[str] | None = typer.Argument(None, help="Arguments passed to the skill command"),
    skills_dir: Path | None = typer.Option(
        None,
        "--dir",
        help="Optional skills directory override",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-confirm risky commands"),
    json_output: bool = typer.Option(False, "--json", help="JSON output"),
):
    """
    Run a skill command.

    Spec format:
      <skill>:<command>  — run a named navig-command from the skill
      <skill>            — run the skill's entrypoint (main.py / index.js)

    Examples:
        navig skills run docker-manage:ps
        navig skills run git-basics:git-status
        navig skills run file-operations:list-files /var/log
        navig skills run my-custom-skill   # runs entrypoint
    """
    if json_output:
        ctx.obj["json"] = True
    if yes:
        ctx.obj["yes"] = True
    if skills_dir:
        ctx.obj["skills_dir"] = str(skills_dir)
    exit_code = run_skill_cmd(spec, args or [], ctx.obj)
    if exit_code != 0:
        raise typer.Exit(exit_code)


@skills_app.command("synthesize")
def skills_synthesize(
    ctx: typer.Context,
    min_occurrences: int = typer.Option(
        3,
        "--min-occurrences",
        "-m",
        min=1,
        help="Minimum pattern repetitions to consider.",
    ),
    limit: int = typer.Option(
        10,
        "--limit",
        "-n",
        min=1,
        max=100,
        help="Maximum number of patterns to analyse.",
    ),
    apply: bool = typer.Option(
        False, "--apply", help="Write approved skill YAML to ~/.navig/skills/."
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Preview without writing any files."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Auto-approve all safe drafts."),
) -> None:
    """
    Synthesize new skill YAML files from repeated command patterns.

    Scans ~/.navig/data/pattern_log.sqlite, clusters repeated sequences,
    and generates ready-to-use NAVIG skill definitions.

    Examples:
        navig skills synthesize                  # preview top patterns
        navig skills synthesize --apply          # generate + save skills
        navig skills synthesize --min-occurrences 5 --apply --yes
    """
    try:
        from navig.agent.pattern_analyzer import PatternAnalyzer  # type: ignore
        from navig.agent.pattern_observer import (  # type: ignore
            DEFAULT_DB_PATH,
            PatternObserver,
        )
        from navig.agent.skill_drafter import SkillDrafter  # type: ignore
    except ImportError as exc:
        ch.error(f"Synthesis pipeline not available: {exc}")
        raise typer.Exit(1) from exc

    observer = PatternObserver(DEFAULT_DB_PATH)
    records = observer.get_recent(limit=500)

    if not records:
        ch.warn(
            "No command patterns found in pattern log.\n"
            "  Run a few commands first to build the pattern database.\n"
            f"  Log path: {DEFAULT_DB_PATH}"
        )
        raise typer.Exit(0)

    analyzer = PatternAnalyzer(min_occurrences=min_occurrences, max_results=limit)
    scored = analyzer.score_by_frequency(records)

    if not scored:
        ch.warn(
            f"No patterns found with ≥{min_occurrences} occurrences.\n"
            "  Try lowering --min-occurrences."
        )
        raise typer.Exit(0)

    drafter = SkillDrafter()

    # -- Preview table --------------------------------------------------------
    from rich.table import Table

    table = Table(title=f"Top {len(scored)} Synthesisable Patterns", show_lines=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Sequence", style="cyan")
    table.add_column("Occurrences", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Safe")

    drafts = []
    for idx, pattern in enumerate(scored, 1):
        draft = drafter.draft(pattern)
        drafts.append(draft)
        safe_icon = "[green]✓[/green]" if draft.safe else "[red]✗[/red]"
        table.add_row(
            str(idx),
            " → ".join(list(pattern.sequence)[:4]),
            str(pattern.occurrences),
            f"{pattern.score:.0f}",
            safe_icon,
        )

    ch.console.print(table)

    if dry_run:
        ch.dim("  (dry-run: no files written)")
        raise typer.Exit(0)

    if not apply:
        ch.dim("\nRun with --apply to save skill YAML files.")
        raise typer.Exit(0)

    # -- Apply ----------------------------------------------------------------
    saved = 0
    skipped = 0
    for draft in drafts:
        if not draft.safe:
            if yes:
                ch.warn(f"Skipping unsafe draft: {draft.name}")
                skipped += 1
                continue
            choice = typer.confirm(
                f"Draft '{draft.name}' has safety warnings. Save anyway?", default=False
            )
            if not choice:
                skipped += 1
                continue

        path = drafter.apply(draft)
        ch.success(f"Saved: {path}")
        saved += 1

    ch.print(f"\n[bold]{saved}[/bold] skill(s) saved, {skipped} skipped.")
