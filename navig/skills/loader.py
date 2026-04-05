"""Unified skill discovery and loading.

Supports three SKILL.md formats:
  1. Full NAVIG frontmatter – id, name, version, category, tags, platforms, tools, safety
  2. Legacy NAVIG frontmatter – name, description, category, navig-commands, risk-level
  3. Plain SKILL.md – no frontmatter, id/name derived from folder name

Also supports `.skill.yaml` companion files for code-generated skills.

Usage::

    from navig.skills.loader import load_all_skills

    skills = load_all_skills()
    for s in skills:
        print(s.id, s.safety, s.tags)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from loguru import logger

if TYPE_CHECKING:
    from navig.tools.interfaces import SkillSpec

# ---------------------------------------------------------------------------
# Security
# ---------------------------------------------------------------------------


class SkillSecurityError(ValueError):
    """Raised when a SKILL.md install spec fails security validation."""

    def __init__(self, field_name: str, value: str, reason: str) -> None:
        super().__init__(f"Skill security violation in '{field_name}': {reason} (value={value!r})")
        self.field_name = field_name
        self.value = value
        self.reason = reason


# Safe pattern for brew/apt package names (no version specifiers)
_SAFE_PACKAGE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9@+._/\-]*$")
# Safe pattern for pip/npm/cargo — allows PEP 440 / semver version specifiers
_SAFE_VERSIONED_RE = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9@+._/\-]*([ \t]*[!<>=~^]{1,2}[A-Za-z0-9._*+-]*)*$"
)


def _validate_install_spec(spec: dict[str, Any]) -> None:
    """
    Validate an ``install:`` frontmatter block against security constraints.

    Raises:
        SkillSecurityError: on the first violation found.
    """
    if not isinstance(spec, dict):
        return

    for mgr in ("brew", "apt"):
        pkg = spec.get(mgr)
        if pkg and isinstance(pkg, str):
            if not _SAFE_PACKAGE_RE.match(pkg):
                raise SkillSecurityError(
                    f"install.{mgr}",
                    pkg,
                    "package name contains disallowed characters",
                )

    for mgr in ("pip", "npm", "cargo"):
        pkg = spec.get(mgr)
        if pkg and isinstance(pkg, str):
            if not _SAFE_VERSIONED_RE.match(pkg):
                raise SkillSecurityError(
                    f"install.{mgr}",
                    pkg,
                    "package name contains disallowed characters",
                )

    go_pkg = spec.get("go")
    if go_pkg and isinstance(go_pkg, str):
        if "://" in go_pkg:
            raise SkillSecurityError(
                "install.go",
                go_pkg,
                "Go module path must not contain a URL scheme (://)",
            )

    dl = spec.get("download", {})
    if isinstance(dl, dict):
        url = dl.get("url", "")
        if url and not str(url).startswith("https://"):
            raise SkillSecurityError(
                "install.download.url",
                str(url),
                "download URL must use https://",
            )


# ---------------------------------------------------------------------------
# Public data contract
# ---------------------------------------------------------------------------


@dataclass
class Skill:
    """Canonical internal representation of a NAVIG skill."""

    id: str
    name: str
    version: str
    category: str
    tags: list[str]
    platforms: list[str]
    tools: list[str]
    safety: str  # safe | elevated | destructive
    body_markdown: str  # raw Markdown body (after the frontmatter)
    examples: list[str]  # extracted fenced bash blocks from Examples section
    source_path: Path

    # Optional enrichment fields (absent in legacy format = empty string)
    description: str = ""
    user_invocable: bool = True

    def to_spec(self) -> SkillSpec:
        """Convert this loaded text-definition into a typed runtime pipeline abstraction."""
        from navig.tools.interfaces import SkillSpec

        return SkillSpec(
            id=self.id,
            name=self.name,
            version=self.version,
            description=self.description,
            # For now tools are just defined as strings,
            # we'd instantiate actual ToolSpecs if we parsed them
            tools=[],
        )


# Internal helpers
# ---------------------------------------------------------------------------


def _load_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return (frontmatter_dict, body_markdown) from a SKILL.md string.

    Handles:
    - Full/YAML frontmatter (starts with ``---``)
    - No frontmatter (empty dict returned, full text as body)
    """
    if not text.startswith("---"):
        return {}, text

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text

    try:
        import yaml  # lazy — navig help must stay <50ms
    except ImportError:
        return {}, parts[2].lstrip()

    try:
        fm = yaml.safe_load(parts[1]) or {}
    except Exception:
        fm = {}

    return fm, parts[2].lstrip()


def _extract_examples(body: str) -> list[str]:
    """Pull fenced ```bash ... ``` blocks (or bare ``` ... ```) from an Examples section."""
    # Find the Examples section
    section_match = re.search(r"^#+ Examples?\s*\n(.*?)(?=^#+ |\Z)", body, re.MULTILINE | re.DOTALL)
    if not section_match:
        return []

    section = section_match.group(1)
    # Extract fenced code blocks
    return re.findall(r"```(?:bash|sh)?\s*\n(.*?)```", section, re.DOTALL)


def _slug(text: str) -> str:
    """Convert human name to kebab-case id."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


# ---------------------------------------------------------------------------
# Full frontmatter → Skill (format 1)
# ---------------------------------------------------------------------------


def _from_full_fm(fm: dict[str, Any], body: str, path: Path) -> Skill:
    """Parse format-1 SKILL.md: id, name, version, category, tags, platforms, tools, safety."""
    folder_slug = _slug(path.parent.name)

    def _list(key: str) -> list[str]:
        val = fm.get(key, [])
        if isinstance(val, list):
            return [str(v) for v in val]
        if isinstance(val, str):
            return [v.strip() for v in val.split(",") if v.strip()]
        return []

    return Skill(
        id=fm.get("id") or folder_slug,
        name=fm.get("name") or path.parent.name,
        version=str(fm.get("version", "1.0.0")),
        category=fm.get("category", "general"),
        tags=_list("tags"),
        platforms=_list("platforms") or ["linux", "macos", "windows"],
        tools=_list("tools"),
        safety=fm.get("safety", "safe"),
        body_markdown=body,
        examples=_extract_examples(body),
        source_path=path,
        description=fm.get("description", ""),
        user_invocable=bool(fm.get("user_invocable", True)),
    )


# ---------------------------------------------------------------------------
# Legacy frontmatter → Skill (format 2)
# ---------------------------------------------------------------------------


def _from_legacy_fm(fm: dict[str, Any], body: str, path: Path) -> Skill:
    """Parse format-2 SKILL.md: name, description, category, risk-level, navig-commands."""
    folder_slug = _slug(path.parent.name)

    # Map legacy risk-level → safety
    risk = fm.get("risk-level", fm.get("risk_level", "safe"))
    safety_map = {"safe": "safe", "elevated": "elevated", "destructive": "destructive"}
    safety = safety_map.get(str(risk).lower(), "safe")

    # Extract platform hints from frontmatter
    raw_plat = fm.get("platforms")
    if isinstance(raw_plat, list):
        platforms = [str(p) for p in raw_plat]
    else:
        platforms = ["linux", "macos", "windows"]

    return Skill(
        id=fm.get("id") or folder_slug,
        name=fm.get("name") or path.parent.name,
        version=str(fm.get("version", "0.0.1")),
        category=fm.get("category", "general"),
        tags=[],
        platforms=platforms,
        tools=[],
        safety=safety,
        body_markdown=body,
        examples=_extract_examples(body),
        source_path=path,
        description=fm.get("description", ""),
        user_invocable=bool(fm.get("user-invocable", fm.get("user_invocable", True))),
    )


# ---------------------------------------------------------------------------
# No frontmatter → Skill (format 3)
# ---------------------------------------------------------------------------


def _from_plain_md(body: str, path: Path) -> Skill:
    """Parse format-3 SKILL.md: plain markdown, no frontmatter block."""
    folder_slug = _slug(path.parent.name)

    # Best-effort: pull first H1 as name
    name_match = re.search(r"^#\s+(.+)$", body, re.MULTILINE)
    name = name_match.group(1).strip() if name_match else path.parent.name

    return Skill(
        id=folder_slug,
        name=name,
        version="0.0.1",
        category="general",
        tags=[],
        platforms=["linux", "macos", "windows"],
        tools=[],
        safety="safe",
        body_markdown=body,
        examples=_extract_examples(body),
        source_path=path,
    )


# ---------------------------------------------------------------------------
# Public: parse a single SKILL.md file
# ---------------------------------------------------------------------------


def parse_skill_file(path: Path) -> Skill | None:
    """Parse a SKILL.md file into a ``Skill``.  Returns None on unrecoverable parse error."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("skills.loader: cannot read {}: {}", path, exc)
        return None

    fm, body = _load_frontmatter(text)

    # --- Security: validate install spec before accepting the skill ---
    if fm and isinstance(fm.get("install"), dict):
        try:
            _validate_install_spec(fm["install"])
        except SkillSecurityError as sec_err:
            logger.warning("skills.loader: rejecting {} — {}", path, sec_err)
            return None

    if not fm:
        return _from_plain_md(body, path)

    # Distinguish full-format vs legacy by checking for a NAVIG-specific field
    is_full = any(k in fm for k in ("id", "tags", "platforms", "tools", "safety"))
    is_legacy = any(k in fm for k in ("navig-commands", "risk-level", "risk_level"))

    if is_full:
        return _from_full_fm(fm, body, path)
    if is_legacy:
        return _from_legacy_fm(fm, body, path)

    # Has frontmatter but unknown shape — attempt full, fall back to legacy
    try:
        return _from_full_fm(fm, body, path)
    except Exception:
        return _from_legacy_fm(fm, body, path)


# ---------------------------------------------------------------------------
# Public: discover skill directories
# ---------------------------------------------------------------------------


def get_skill_dirs() -> list[Path]:
    """Return all skill directory roots to scan, deduped and validated.

    Looks in:
    - navig package built-in skills (store + packages)
    - workspace ``navig-hub/skill-library`` and ``navig-hub/skills``
    - project-local ``.navig/skills``
    """
    candidates: list[Path] = []

    # Platform roots (builtin store, user store, packages)
    try:
        from navig.platform.paths import (
            builtin_packages_dir,
            builtin_store_dir,
            packages_dir,
            store_dir,
        )

        for root_fn in (builtin_store_dir, store_dir):
            try:
                d = root_fn() / "skills"
                if d.exists():
                    candidates.append(d)
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
        for root_fn in (builtin_packages_dir, packages_dir):
            try:
                root = root_fn()
                if root.exists():
                    for pkg in root.iterdir():
                        s = pkg / "skills"
                        if s.is_dir():
                            candidates.append(s)
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
    except ImportError:
        pass  # optional dependency not installed; feature disabled

    # Walk up from this file to find workspace root
    here = Path(__file__).resolve()
    for _ in range(6):  # up to 6 levels
        here = here.parent
        community = here / "navig-hub"
        if community.exists():
            for sub in ("skill-library", "skills"):
                d = community / sub
                if d.exists():
                    candidates.append(d)
            break

    # Project-local skills
    try:
        from navig.platform.paths import project_root

        local_skills = project_root() / ".navig" / "skills"
        if local_skills.exists():
            candidates.append(local_skills)
    except Exception:  # noqa: BLE001
        pass  # best-effort; failure is non-critical

    # Deduplicate while preserving order
    seen: set[Path] = set()
    unique: list[Path] = []
    for p in candidates:
        resolved = p.resolve()
        if resolved not in seen:
            seen.add(resolved)
            unique.append(p)

    return unique


# ---------------------------------------------------------------------------
# Public: load all skills
# ---------------------------------------------------------------------------


def load_all_skills(dirs: list[Path] | None = None) -> list[Skill]:
    """Scan ``dirs`` (or auto-discover) and return all parsed ``Skill`` objects.

    Deduplicates by ``(id, source_path.resolve())``.
    """
    if dirs is None:
        dirs = get_skill_dirs()

    skills: list[Skill] = []
    seen_files: set[Path] = set()
    seen_ids: set[str] = set()

    for d in dirs:
        for skill_file in sorted(d.rglob("SKILL.md")):
            resolved = skill_file.resolve()
            if resolved in seen_files:
                continue
            seen_files.add(resolved)

            skill = parse_skill_file(skill_file)
            if skill is None:
                continue

            # If id collision, suffix with folder path hash to keep both
            if skill.id in seen_ids:
                suffix = str(abs(hash(str(resolved))))[:6]
                skill = Skill(
                    **{**skill.__dict__, "id": f"{skill.id}-{suffix}"}  # type: ignore[arg-type]
                )
            seen_ids.add(skill.id)
            skills.append(skill)

    logger.debug("skills.loader: loaded {} skills from {} dir(s)", len(skills), len(dirs))
    return skills


# ---------------------------------------------------------------------------
# Convenience: index by id
# ---------------------------------------------------------------------------


def skills_by_id(skills: list[Skill] | None = None) -> dict[str, Skill]:
    """Return {skill.id: skill} mapping."""
    if skills is None:
        skills = load_all_skills()
    return {s.id: s for s in skills}
