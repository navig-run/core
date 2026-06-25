"""Prompt registry — discover & list reusable prompts across all roots.

Mirrors ``navig.skills.loader`` but for **prompts**, which stay a *distinct
type* from skills (Claude's commands-vs-skills split): a prompt is a reusable
instruction/template a user invokes; a skill is an auto-activated capability.

Discovery roots (low → high priority):
  * builtin store           navig builtin ``store/prompts``
  * packages                ``<pkg>/prompts`` (package == Claude plugin)
  * user store              ``~/.navig/store/prompts``
  * active workshop         ``<space>/.navig/{brain/prompts,prompts}``
  * Claude commands         ``~/.claude/commands`` (Claude slash commands ARE prompts)

Prompts can be **exported** to ``.claude/commands/<id>.md`` so Claude Code can
run them as slash commands. ``navig.prompt_loader.load_prompt`` stays the
low-level builtin reader; this module is the higher-level catalog.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Data contract ───────────────────────────────────────────────────────────

@dataclass
class Prompt:
    """A reusable prompt/command — distinct from a Skill."""

    id: str
    name: str
    description: str
    body: str  # prompt text (frontmatter stripped)
    source_path: Path
    tags: list[str] = field(default_factory=list)
    argument_hint: str = ""  # Claude command `argument-hint`
    scope: str = "user"  # builtin | package | user | space | claude


def _split_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter_dict, body) — handles YAML frontmatter or none."""
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    try:
        import yaml  # lazy
        fm = yaml.safe_load(parts[1]) or {}
    except Exception:  # noqa: BLE001
        fm = {}
    return (fm if isinstance(fm, dict) else {}), parts[2].lstrip()


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def parse_prompt_file(path: Path, root: Path, scope: str = "user") -> Prompt | None:
    """Parse a ``.md``/``.txt`` prompt file. id = path relative to *root* (no ext)."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None

    fm, body = _split_frontmatter(text)
    try:
        rel = path.relative_to(root).with_suffix("").as_posix()
    except ValueError:
        rel = path.stem
    pid = str(fm.get("id") or rel)

    def _list(key: str) -> list[str]:
        v = fm.get(key)
        if isinstance(v, list):
            return [str(x) for x in v]
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return []

    return Prompt(
        id=pid,
        name=str(fm.get("name") or path.stem),
        description=str(fm.get("description", "")),
        body=body.strip(),
        source_path=path,
        tags=_list("tags") or _list("keywords"),
        argument_hint=str(fm.get("argument-hint") or fm.get("argument_hint") or ""),
        scope=scope,
    )


def get_prompt_dirs() -> list[tuple[Path, str]]:
    """Return ``[(dir, scope)]`` roots to scan, low → high priority."""
    roots: list[tuple[Path, str]] = []

    try:
        from navig.platform.paths import (
            builtin_packages_dir,
            builtin_store_dir,
            config_dir,
            packages_dir,
            store_dir,
        )

        for fn in (builtin_store_dir,):
            try:
                roots.append((fn() / "prompts", "builtin"))
            except Exception:  # noqa: BLE001
                pass
        # package-provided prompts (package == Claude plugin)
        for fn in (builtin_packages_dir, packages_dir):
            try:
                base = fn()
                if base.exists():
                    for pkg in sorted(base.iterdir()):
                        if (pkg / "prompts").is_dir():
                            roots.append((pkg / "prompts", "package"))
            except Exception:  # noqa: BLE001
                pass
        for fn in (store_dir, config_dir):
            try:
                roots.append((fn() / "store" / "prompts" if fn is config_dir else fn() / "prompts", "user"))
            except Exception:  # noqa: BLE001
                pass
    except ImportError:
        pass

    # active workshop (space-local)
    try:
        from navig.platform.paths import find_app_root

        root = find_app_root()
        if root is not None:
            roots.append((root / ".navig" / "brain" / "prompts", "space"))
            roots.append((root / ".navig" / "prompts", "space"))
    except Exception:  # noqa: BLE001
        pass

    # Claude slash commands ARE prompts — federate them.
    try:
        roots.append((Path.home() / ".claude" / "commands", "claude"))
    except Exception:  # noqa: BLE001
        pass

    # keep only existing, dedupe by resolved path
    seen: set[Path] = set()
    out: list[tuple[Path, str]] = []
    for d, scope in roots:
        try:
            if not d.exists():
                continue
            r = d.resolve()
        except OSError:
            continue
        if r in seen:
            continue
        seen.add(r)
        out.append((d, scope))
    return out


def load_all_prompts(dirs: list[tuple[Path, str]] | None = None) -> list[Prompt]:
    """Scan roots and return parsed ``Prompt`` objects (deduped by file; id-collision suffixed)."""
    if dirs is None:
        dirs = get_prompt_dirs()

    prompts: list[Prompt] = []
    seen_files: set[Path] = set()
    seen_ids: set[str] = set()

    for d, scope in dirs:
        for f in sorted([*d.rglob("*.md"), *d.rglob("*.txt")]):
            if f.name == "SKILL.md":  # skills are a different type
                continue
            resolved = f.resolve()
            if resolved in seen_files:
                continue
            seen_files.add(resolved)
            p = parse_prompt_file(f, d, scope)
            if p is None:
                continue
            if p.id in seen_ids:
                p.id = f"{p.id}-{str(abs(hash(str(resolved))))[:6]}"
            seen_ids.add(p.id)
            prompts.append(p)
    return prompts


def prompts_by_id(prompts: list[Prompt] | None = None) -> dict[str, Prompt]:
    if prompts is None:
        prompts = load_all_prompts()
    return {p.id: p for p in prompts}


def export_prompt(prompt_id: str, dest: Path, *, force: bool = False) -> Path:
    """Export a prompt as a Claude slash command (``dest/<id>.md``)."""
    prompt = prompts_by_id().get(prompt_id)
    if prompt is None:
        raise ValueError(f"Prompt '{prompt_id}' not found.")

    out = Path(dest).expanduser() / f"{prompt_id.split('/')[-1]}.md"
    if out.exists() and not force:
        raise FileExistsError(f"{out} already exists (use --force).")
    out.parent.mkdir(parents=True, exist_ok=True)

    fm_lines = ["---"]
    if prompt.description:
        fm_lines.append(f"description: {prompt.description}")
    if prompt.argument_hint:
        fm_lines.append(f"argument-hint: {prompt.argument_hint}")
    fm_lines.append("---")
    out.write_text("\n".join(fm_lines) + "\n\n" + prompt.body.strip() + "\n", encoding="utf-8")
    return out
