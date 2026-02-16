"""Skill listing, metadata, show, and execution helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import json
import subprocess
import shlex
import sys

from navig import console_helper as ch


@dataclass(frozen=True)
class SkillCommand:
    """A single command declared by a skill."""
    name: str
    syntax: str
    description: str
    risk: str = "safe"
    confirmation_msg: Optional[str] = None


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
    commands: tuple = ()          # tuple[SkillCommand, ...]
    examples: tuple = ()          # tuple[dict, ...]
    entrypoint: Optional[str] = None  # e.g. "index.js", "main.py"
    raw_frontmatter: Dict[str, Any] = field(default_factory=dict)


def _resolve_skills_dirs(explicit_dir: Optional[str]) -> List[Path]:
    if explicit_dir:
        candidate = Path(explicit_dir).expanduser().resolve()
        if candidate.exists():
            return [candidate]
        return []

    base_dir = Path(__file__).resolve()
    candidates = [
        base_dir.parent / "skills",
        base_dir.parent.parent / "skills",
        base_dir.parent.parent.parent / "skills",
    ]

    unique: List[Path] = []
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


def _load_frontmatter(skill_file: Path) -> Dict[str, Any]:
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


def _collect_skills(skills_dirs: Iterable[Path]) -> List[SkillInfo]:
    skills: List[SkillInfo] = []
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
            parsed_commands: List[SkillCommand] = []
            for c in raw_cmds:
                if isinstance(c, dict):
                    parsed_commands.append(SkillCommand(
                        name=c.get("name", ""),
                        syntax=c.get("syntax", ""),
                        description=c.get("description", ""),
                        risk=c.get("risk", "safe"),
                        confirmation_msg=c.get("confirmation_msg"),
                    ))
                elif isinstance(c, str):
                    # Simple form: "navig docker ps"
                    parsed_commands.append(SkillCommand(
                        name=c.split()[-1] if c.strip() else c,
                        syntax=c,
                        description="",
                        risk="safe",
                    ))

            # Parse examples
            raw_examples = frontmatter.get("examples", [])
            parsed_examples = tuple(
                e if isinstance(e, dict) else {"user": str(e)}
                for e in raw_examples
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
                except Exception:
                    pass

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


def list_skills_cmd(options: Dict[str, Any]) -> List[SkillInfo]:
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


def _build_tree(skills: Iterable[SkillInfo]) -> Dict[str, List[str]]:
    tree: Dict[str, List[str]] = {}
    for skill in skills:
        tree.setdefault(skill.category, []).append(skill.name)

    for category in tree:
        tree[category].sort()

    return dict(sorted(tree.items()))


def tree_skills_cmd(options: Dict[str, Any]) -> Dict[str, List[str]]:
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

def _find_skill(name: str, options: Dict[str, Any]) -> Optional[SkillInfo]:
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


def show_skill_cmd(name: str, options: Dict[str, Any]) -> Optional[SkillInfo]:
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
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text

    header = Text()
    header.append(f"{skill.name}", style="bold cyan")
    header.append(f"  v{skill.version}", style="dim")
    header.append(f"  [{skill.category}]", style="yellow")

    lines = [f"[white]{skill.description}[/white]"]
    lines.append(f"[dim]Risk: {skill.risk_level} | Invocable: {'yes' if skill.user_invocable else 'no'}[/dim]")
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

def run_skill_cmd(spec: str, extra_args: List[str], options: Dict[str, Any]) -> int:
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
            for i, arg in enumerate(extra_args):
                # Replace first <placeholder> or {placeholder}
                import re
                cli_args = re.sub(r'[<{][^>}]+[>}]', arg, cli_args, count=1)

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
    ch.dim("  Use 'navig skills show {0}' for details.".format(skill.name))
    return 1


def _invoke_navig(cli_args: str, options: Dict[str, Any]) -> int:
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
    """Run a shell command in the skill's directory."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            text=True,
        )
        return result.returncode
    except Exception as e:
        ch.error(f"Shell execution failed: {e}")
        return 1
