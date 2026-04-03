"""Centralised SOUL.md loader — single source of truth for identity content.

Consolidates the two divergent load paths that previously existed in:
  - navig/agent/soul.py  → Soul._load_soul_file()
  - navig/agent/conversational.py → ConversationalAgent.load_soul_content()

Search order:
  1. Active persona's soul.md  (~/.navig/personas/<active>/soul.md)
  2. Active space SOUL.md      (~/.navig/spaces/<active_space>/SOUL.md)
  3. Workspace IDENTITY.md     (~/.navig/workspace/IDENTITY.md)  ← RFC #37 Phase 1
  4. Legacy workspace SOUL.md  (~/.navig/workspace/SOUL.md)      ← backward-compat
  5. Package default            navig/resources/SOUL.default.md
  6. Context fallback           navig/agent/context/SOUL.md
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def _try_read(path: Path) -> str | None:
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        logger.debug("Could not read soul file %s: %s", path, exc)
    return None


def load_soul(
    persona_name: str | None = None,
    active_space: str | None = None,
    cwd: Path | None = None,
) -> str:
    """Return soul content following the 6-level priority chain.

    Parameters
    ----------
    persona_name:
        If given, try this persona's soul.md first via the resolver.
    active_space:
        If given, check ~/.navig/spaces/<active_space>/SOUL.md.
    cwd:
        Working directory for project-local persona/space resolution.

    Notes
    -----
    Step 3 (``~/.navig/workspace/IDENTITY.md``) is the Phase-1 landing path for
    the modular identity architecture described in RFC #37.  When present, it is
    used instead of the legacy ``SOUL.md`` monolith.  Existing installations that
    only have ``SOUL.md`` are not affected.
    """
    # 1. Active persona soul.md
    if persona_name:
        try:
            from navig.personas.resolver import resolve_persona  # noqa: PLC0415

            persona_dir = resolve_persona(persona_name, cwd=cwd)
            if persona_dir is not None:
                content = _try_read(persona_dir / "soul.md")
                if content:
                    return content
        except Exception as exc:  # noqa: BLE001
            logger.debug("Failed to load persona soul for '%s': %s", persona_name, exc)

    # 2. Active space SOUL.md
    if active_space:
        space_soul = _try_read(Path.home() / ".navig" / "spaces" / active_space / "SOUL.md")
        if space_soul:
            return space_soul

    # 3. Workspace IDENTITY.md — modular identity file (RFC #37, Phase 1)
    #    Checked before the legacy SOUL.md to allow file-driven identity without
    #    replacing the full SOUL.md monolith. Fully additive: existing SOUL.md
    #    users are not affected.
    identity_md = _try_read(Path.home() / ".navig" / "workspace" / "IDENTITY.md")
    if identity_md:
        return identity_md

    # 4. Legacy workspace SOUL.md (kept for backward compatibility)
    legacy = _try_read(Path.home() / ".navig" / "workspace" / "SOUL.md")
    if legacy:
        return legacy

    # 5. Rich package default
    pkg_default = Path(__file__).parent.parent / "resources" / "SOUL.default.md"
    pkg_content = _try_read(pkg_default)
    if pkg_content:
        return pkg_content

    # 6. Minimal context fallback
    context_soul = Path(__file__).parent.parent / "agent" / "context" / "SOUL.md"
    fallback = _try_read(context_soul)
    if fallback:
        return fallback

    return ""
