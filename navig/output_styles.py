"""
Output Styles — user-defined AI response format profiles.

Ported from .lab/claude/outputStyles/ (MIT, Anthropic).  Adapted to Python.

Users drop ``.md`` files with YAML frontmatter into:
  - ``.navig/output-styles/``  (project-local, highest priority)
  - ``~/.navig/output-styles/`` (user-global)

Each file becomes a named style that prepends a formatting instruction to
every LLM prompt when active.  Zero-code extensibility.

Example style file (``.navig/output-styles/concise.md``)::

    ---
    name: concise
    description: Keep every response under 5 sentences.
    keep-coding-instructions: true
    ---
    Respond as concisely as possible: no preamble, no filler phrases,
    no trailing "let me know" sentences.  Code blocks are fine.

Usage::

    from navig.output_styles import get_active_style, load_output_styles

    styles = load_output_styles()               # all discovered styles
    style  = get_active_style()                 # currently-active style or None
    if style:
        system_prompt = style.prompt + "\\n\\n" + base_system_prompt
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger("navig.output_styles")

# ---------------------------------------------------------------------------
# Pydantic schema with graceful fallback (mirrors skills_renderer.py pattern)
# ---------------------------------------------------------------------------

try:
    from pydantic import BaseModel

    _PYDANTIC_OK = True
except ImportError:
    _PYDANTIC_OK = False

if _PYDANTIC_OK:

    class OutputStyleConfig(BaseModel):
        """A parsed output style definition."""

        name: str
        description: str = ""
        prompt: str = ""
        source: str = "user"   # "user" | "project" | "builtin"
        keep_coding_instructions: bool = True

else:
    # Plain dataclass fallback when Pydantic is not installed.
    @dataclass
    class OutputStyleConfig:  # type: ignore[no-redef]
        name: str
        description: str = ""
        prompt: str = ""
        source: str = "user"
        keep_coding_instructions: bool = True


# ---------------------------------------------------------------------------
# Example / built-in styles shipped in config/output-styles/
# ---------------------------------------------------------------------------

_BUILTIN_STYLES_DIR_NAME = "output-styles"

# ---------------------------------------------------------------------------
# Frontmatter parsing (no external YAML library required)
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(
    r"^---[\r\n]+(.*?)[\r\n]+---[\r\n]*(.*)",
    re.DOTALL,
)


def _parse_style_file(path: Path, source: str) -> OutputStyleConfig | None:
    """
    Parse a ``.md`` file with optional YAML frontmatter into an
    ``OutputStyleConfig``.  Returns ``None`` on parse failure.

    Supported frontmatter keys:
      name, description, keep-coding-instructions
    """
    try:
        raw = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError as exc:
        logger.debug("output_styles: cannot read %s: %s", path, exc)
        return None

    frontmatter: dict[str, str] = {}
    body = raw

    m = _FRONTMATTER_RE.match(raw)
    if m:
        fm_block, body = m.group(1), m.group(2).strip()
        for line in fm_block.splitlines():
            if ":" in line:
                key, _, val = line.partition(":")
                frontmatter[key.strip().lower()] = val.strip().strip('"').strip("'")

    name = frontmatter.get("name") or path.stem
    description = frontmatter.get("description", "")
    keep_coding = frontmatter.get("keep-coding-instructions", "true").lower() not in (
        "false",
        "0",
        "no",
    )
    prompt = body.strip()

    if not prompt:
        logger.debug("output_styles: %s has no prompt body — skipped", path)
        return None

    try:
        return OutputStyleConfig(
            name=name,
            description=description,
            prompt=prompt,
            source=source,
            keep_coding_instructions=keep_coding,
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("output_styles: failed to build config for %s: %s", path, exc)
        return None


# ---------------------------------------------------------------------------
# Loader — scans project dir then user dir; project wins on name collision
# ---------------------------------------------------------------------------


def load_output_styles(cwd: Path | None = None) -> list[OutputStyleConfig]:
    """
    Discover and parse all output style files.

    Resolution order (project wins on name collision):
    1. ``.navig/output-styles/`` relative to *cwd*
    2. ``~/.navig/output-styles/`` (user-global)
    3. ``config/output-styles/`` bundled with navig (built-in examples)

    Args:
        cwd: Project root.  Defaults to ``Path.cwd()``.

    Returns:
        List of ``OutputStyleConfig`` sorted by name; project styles first.
    """
    if cwd is None:
        cwd = Path.cwd()

    cfg = _load_styles_config()
    project_dir = (cwd / cfg.get("project_dir", ".navig/output-styles")).expanduser()
    user_dir = Path(cfg.get("user_dir", "~/.navig/output-styles")).expanduser()

    seen: dict[str, OutputStyleConfig] = {}

    # Built-in examples (lowest priority; ship useful defaults)
    builtin_dir = _get_builtin_styles_dir()
    if builtin_dir and builtin_dir.is_dir():
        for md in sorted(builtin_dir.glob("*.md")):
            style = _parse_style_file(md, source="builtin")
            if style and style.name not in seen:
                seen[style.name] = style

    # User-global (overrides builtins)
    if user_dir.is_dir():
        for md in sorted(user_dir.glob("*.md")):
            style = _parse_style_file(md, source="user")
            if style:
                seen[style.name] = style

    # Project-local (highest priority — wins on name collision)
    if project_dir.is_dir():
        for md in sorted(project_dir.glob("*.md")):
            style = _parse_style_file(md, source="project")
            if style:
                seen[style.name] = style

    # Sort: project styles first, then user, then builtin; alpha within tier
    order = {"project": 0, "user": 1, "builtin": 2}
    return sorted(seen.values(), key=lambda s: (order.get(s.source, 9), s.name))


def get_active_style(cwd: Path | None = None) -> OutputStyleConfig | None:
    """
    Return the currently-active ``OutputStyleConfig`` or ``None``.

    Reads ``output_styles.active`` from config.  If the named style exists
    in the discovered styles it is returned; otherwise ``None``.
    """
    cfg = _load_styles_config()
    active_name = cfg.get("active")
    if not active_name:
        return None

    styles = {s.name: s for s in load_output_styles(cwd)}
    found = styles.get(active_name)
    if found is None:
        logger.debug(
            "output_styles: active style %r not found in discovered styles", active_name
        )
    return found


def set_active_style(name: str | None) -> None:
    """
    Persist *name* as the active style to `output_styles.active` in config.

    Pass ``None`` to clear the active style.
    """
    try:
        from navig.config import get_config_manager

        cm = get_config_manager()
        cm.set("output_styles.active", name)
    except Exception as exc:  # noqa: BLE001
        logger.debug("output_styles: failed to persist active style: %s", exc)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_styles_config() -> dict[str, Any]:
    try:
        from navig.config import get_config_manager

        cm = get_config_manager()
        raw = cm.get("output_styles")
        if isinstance(raw, dict):
            return raw
    except Exception as exc:  # noqa: BLE001
        logger.debug("output_styles: config load failed: %s", exc)
    return {}


def _get_builtin_styles_dir() -> Path | None:
    """Return the path to the bundled config/output-styles/ directory."""
    try:
        # Walk up from this file to the project root.
        here = Path(__file__).parent  # navig/
        candidate = here.parent / "config" / _BUILTIN_STYLES_DIR_NAME
        if candidate.is_dir():
            return candidate
    except Exception:  # noqa: BLE001
        pass
    return None
