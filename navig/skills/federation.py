"""Federated skill hub — install skills from any agent, export to any.

``SKILL.md`` is the shared format: Claude Code, Hermes, OpenClaw and Codex all
read it. We *normalize* a foreign skill into a canonical NAVIG ``SKILL.md``
(a Claude-compatible superset) under ``~/.navig/store/skills/<id>/`` and record
its origin in ``.skill.meta.json``. **Export** re-emits a NAVIG skill in a
target agent's layout so other agents can consume it.

Install specs (``navig skill install <spec>``):
    claude:<name>     ~/.claude/skills/<name>
    openclaw:<name>   ~/.openclaw/workspace/skills/<name>
    hermes:<name>     ~/.hermes/skills/<name>
    codex:<name>      ~/.codex/skills/<name>
    <local path>      a SKILL.md file or a dir containing one
(github:/skill: specs fall through to the community installer.)
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

from navig.skills.loader import Skill, parse_skill_file

SCHEMES = ("claude", "openclaw", "hermes", "codex")
EXPORT_FORMATS = ("claude", "hermes", "openclaw")


def _foreign_root(scheme: str) -> Path | None:
    home = Path.home()
    return {
        "claude": home / ".claude" / "skills",
        "openclaw": home / ".openclaw" / "workspace" / "skills",
        "hermes": home / ".hermes" / "skills",
        "codex": home / ".codex" / "skills",
    }.get(scheme)


def detect_source(spec: str) -> tuple[str, Path] | None:
    """Resolve a *foreign* install spec → ``(scheme, source)``.

    Returns ``None`` when *spec* is not a foreign source (e.g. a ``github:``/
    ``skill:`` community spec) so the caller can fall back to that installer.
    """
    head = spec.split(":", 1)[0] if ":" in spec else ""
    if head in SCHEMES:
        root = _foreign_root(head)
        name = spec.split(":", 1)[1].strip()
        if root is None or not name:
            return None
        return (head, root / name)
    # A local path (file or dir). Schemes like github:/skill: don't resolve here.
    try:
        p = Path(spec).expanduser()
        if p.exists():
            return ("local", p)
    except (OSError, ValueError):
        pass
    return None


def _find_skill_md(src: Path) -> Path | None:
    """Locate the SKILL.md for a source dir/file."""
    if src.is_file():
        return src if src.name == "SKILL.md" else None
    if src.is_dir():
        direct = src / "SKILL.md"
        if direct.is_file():
            return direct
        for f in sorted(src.rglob("SKILL.md")):
            return f  # first nested
    return None


def _collect_skill_sources(location: Path) -> list[Path]:
    """Return every SKILL.md to install from *location*.

    Honors an OpenClaw plugin bundle (``openclaw.plugin.json``/``plugin.json``
    with a ``skills[]`` list); otherwise a single skill, else all nested SKILL.md.
    """
    if location.is_file():
        return [location] if location.name == "SKILL.md" else []
    if not location.is_dir():
        return []

    # OpenClaw plugin manifest with a skills[] bundle. Most real OpenClaw/clawdbot
    # plugins are single-skill (no skills[]) — those fall through to the SKILL.md
    # below; this branch only fires for genuine multi-skill bundles.
    for manifest_name in ("openclaw.plugin.json", "clawdbot.plugin.json", "plugin.json"):
        manifest = location / manifest_name
        if manifest.is_file():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                data = None
            if isinstance(data, dict) and isinstance(data.get("skills"), list):
                out: list[Path] = []
                for entry in data["skills"]:
                    rel = entry.get("path") if isinstance(entry, dict) else (entry if isinstance(entry, str) else None)
                    if not rel:
                        continue
                    p = (location / rel)
                    sk = p if (p.is_file() and p.name == "SKILL.md") else (p / "SKILL.md")
                    if sk.is_file():
                        out.append(sk)
                if out:
                    return out

    direct = location / "SKILL.md"
    if direct.is_file():
        return [direct]
    return sorted(location.rglob("SKILL.md"))


def _fm_block(fm: dict) -> str:
    import yaml  # lazy

    body = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True, default_flow_style=False).strip()
    return f"---\n{body}\n---\n"


def canonical_skill_md(skill: Skill) -> str:
    """Canonical NAVIG SKILL.md — Claude-compatible frontmatter + navig enrichment."""
    fm: dict = {"name": skill.name, "description": skill.description or ""}
    fm["id"] = skill.id
    fm["version"] = skill.version
    if skill.category and skill.category != "general":
        fm["category"] = skill.category
    if skill.tags:
        fm["tags"] = list(skill.tags)
    if skill.platforms:
        fm["platforms"] = list(skill.platforms)
    if skill.tools:
        fm["allowed-tools"] = list(skill.tools)
    if skill.safety and skill.safety != "safe":
        fm["safety"] = skill.safety
    return f"{_fm_block(fm)}\n{skill.body_markdown.strip()}\n"


def _claude_skill_md(skill: Skill) -> str:
    """Minimal Claude-Code SKILL.md — name/description (+ allowed-tools)."""
    fm: dict = {"name": skill.name, "description": skill.description or ""}
    if skill.tools:
        fm["allowed-tools"] = ", ".join(skill.tools)
    return f"{_fm_block(fm)}\n{skill.body_markdown.strip()}\n"


# ── Install ─────────────────────────────────────────────────────────────────

def _install_one(skill_md: Path, scheme: str, spec: str, *, force: bool, dry_run: bool) -> Path:
    """Normalize one SKILL.md into the store; returns the install dir."""
    skill = parse_skill_file(skill_md)
    if skill is None:
        raise ValueError(f"Could not parse a skill at '{skill_md}'.")

    from navig.platform.paths import store_dir  # noqa: PLC0415

    target = store_dir() / "skills" / skill.id
    if target.exists() and not force:
        raise FileExistsError(f"Skill '{skill.id}' already installed at {target} (use --force).")
    if dry_run:
        return target

    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)

    # Copy companion assets (references/, scripts/, assets/ …); normalize SKILL.md.
    src_dir = skill_md.parent
    if src_dir.is_dir():
        for item in src_dir.iterdir():
            if item.name == "SKILL.md":
                continue
            dest = target / item.name
            try:
                if item.is_dir():
                    shutil.copytree(item, dest, dirs_exist_ok=True)
                else:
                    shutil.copy2(item, dest)
            except OSError:
                pass  # best-effort; a bad companion file must not fail the install

    (target / "SKILL.md").write_text(canonical_skill_md(skill), encoding="utf-8")
    meta = {
        "id": skill.id,
        "name": skill.name,
        "origin": {"format": scheme, "spec": spec, "source_path": str(skill_md)},
        "installed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (target / ".skill.meta.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return target


def install_all(spec: str, *, force: bool = False, dry_run: bool = False) -> list[Path] | None:
    """Install a foreign skill *or* an OpenClaw plugin bundle (multiple skills).

    Returns the list of install dirs, or ``None`` if *spec* is not a foreign
    source (caller should then try the community installer).
    """
    resolved = detect_source(spec)
    if resolved is None:
        return None
    scheme, location = resolved
    sources = _collect_skill_sources(location)
    if not sources:
        raise ValueError(f"No SKILL.md found at '{location}'.")
    return [_install_one(s, scheme, spec, force=force, dry_run=dry_run) for s in sources]


def install(spec: str, *, force: bool = False, dry_run: bool = False) -> Path | None:
    """Single-skill convenience wrapper over :func:`install_all`."""
    out = install_all(spec, force=force, dry_run=dry_run)
    if out is None:
        return None
    return out[0] if out else None


# ── Export ──────────────────────────────────────────────────────────────────

def export(skill_id: str, fmt: str, dest: Path, *, force: bool = False, dry_run: bool = False) -> Path:
    """Export a NAVIG skill in *fmt* (claude|hermes|openclaw) under ``dest/<id>/``."""
    if fmt not in EXPORT_FORMATS:
        raise ValueError(f"Unknown format '{fmt}'. Choose: {', '.join(EXPORT_FORMATS)}.")

    from navig.skills.loader import load_all_skills  # noqa: PLC0415

    skill = next((s for s in load_all_skills() if s.id == skill_id), None)
    if skill is None:
        raise ValueError(f"Skill '{skill_id}' not found.")

    out_dir = Path(dest).expanduser() / skill.id
    skill_file = out_dir / "SKILL.md"
    if skill_file.exists() and not force:
        raise FileExistsError(f"{skill_file} already exists (use --force).")
    if dry_run:
        return out_dir

    out_dir.mkdir(parents=True, exist_ok=True)
    # All three read SKILL.md; claude/hermes use the minimal form, openclaw too
    # plus a plugin wrapper so its plugin loader picks the skill up.
    skill_file.write_text(_claude_skill_md(skill), encoding="utf-8")

    if fmt == "openclaw":
        plugin = {
            "name": skill.id,
            "version": skill.version,
            "description": skill.description or skill.name,
            "skills": [{"id": skill.id, "path": "SKILL.md", "description": skill.description or ""}],
        }
        (out_dir / "openclaw.plugin.json").write_text(json.dumps(plugin, indent=2) + "\n", encoding="utf-8")

    return out_dir
