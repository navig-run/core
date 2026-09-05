"""Centralised SOUL loader — the single source of truth for identity content.

This module owns the ONE resolution chain. Every surface that needs "who is the
agent right now" resolves through :func:`resolve_soul` (rich) or :func:`load_soul`
(text only):

  * ``navig.agent.conv.soul`` — the live conversational chat path
  * ``navig.gateway.server`` — the deep-agent / heartbeat path
  * ``navig.agent.conversational_legacy`` — the legacy compat path

Search order (``SOURCE_ORDER``, first match wins):

  1. ``persona``       active persona's ``soul.md``   (``~/.navig/personas/<slug>/soul.md``)
  2. ``space``         active space ``SOUL.md``       (``~/.navig/spaces/<space>/SOUL.md``)
  3. ``folder-space``  working-dir space ``SOUL.md``  (``<cwd>/.navig/SOUL.md``)
  4. ``identity``      workspace ``IDENTITY.md``      (``~/.navig/workspace/IDENTITY.md``)
  5. ``workspace``     legacy workspace ``SOUL.md``   (``~/.navig/workspace/SOUL.md``)
  6. ``resources``     package default                ``navig/resources/SOUL.default.md``
  7. ``context``       minimal fallback               ``navig/agent/context/SOUL.md``

Safety note: whichever source wins, it only ever supplies *identity* — voice,
priorities, manner. The non-negotiable operating rules come from
``navig.agent.conv.guardrails``, which is compiled into the package and cannot be
replaced by any file resolved here.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from pathlib import Path

from navig.platform.paths import config_dir

logger = logging.getLogger(__name__)

#: The ONE ordered chain, highest priority first. This tuple is the contract —
#: callers rank, label and report sources by it.
SOURCE_ORDER: tuple[str, ...] = (
    "persona",
    "space",
    "folder-space",
    "identity",
    "workspace",
    "resources",
    "context",
)

#: Human labels for report surfaces (``navig agent context``).
SOURCE_LABELS: dict[str, str] = {
    "persona": "persona soul.md",
    "space": "space SOUL.md",
    "folder-space": "folder space SOUL.md",
    "identity": "workspace IDENTITY.md",
    "workspace": "workspace SOUL.md",
    "resources": "package default",
    "context": "minimal fallback",
}


@dataclass(frozen=True, slots=True)
class ShadowedSource:
    """A source that exists on disk but lost to a higher-priority one."""

    tag: str
    path: Path
    chars: int


@dataclass(frozen=True, slots=True)
class SoulResolution:
    """The outcome of one pass over the chain.

    ``shadowed`` is what makes this auditable: an operator who edits
    ``IDENTITY.md`` while a persona is active gets to *see* that their edit is
    being outranked, instead of concluding the file is broken.
    """

    raw: str = ""
    source: str = ""
    path: Path | None = None
    persona: str = ""
    shadowed: tuple[ShadowedSource, ...] = ()
    revision: str = field(default="", compare=False)

    @property
    def found(self) -> bool:
        return bool(self.raw)


def _revision_of(raw: str, source: str) -> str:
    """Short content hash used as a memoisation key by prompt builders."""
    if not raw:
        return ""
    digest = hashlib.sha256(f"{source}\x00{raw}".encode("utf-8", "replace"))
    return digest.hexdigest()[:16]


def _try_read(path: Path) -> str | None:
    try:
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug("Could not read soul file %s: %s", path, exc)
    return None


def _package_personas_root() -> Path:
    return Path(__file__).parent.parent / "resources" / "personas"


def _is_package_persona(persona_dir: Path) -> bool:
    """True when *persona_dir* is one the wheel ships, not one the user authored."""
    try:
        return persona_dir.resolve().is_relative_to(_package_personas_root().resolve())
    except (OSError, ValueError):
        return False


def _persona_dir(persona_name: str, cwd: Path | None) -> Path | None:
    """Resolve *persona_name* to a directory, applying the package-default guard.

    ``personas.store.get_active_persona()`` returns ``"default"`` for every
    install that never chose a persona, and the *package* default persona ships a
    ~550-char pointer stub whose whole content is "the full identity is drawn
    from SOUL.default.md". Honouring that stub would silently replace the rich
    shipped identity with a summary of itself on every install on earth — so the
    package-shipped ``default`` is skipped. A user-authored ``default``
    (``~/.navig/personas/default/`` or a project-local one) is a deliberate
    choice and still wins.
    """
    try:
        from navig.personas.resolver import resolve_persona  # noqa: PLC0415

        persona_dir = resolve_persona(persona_name, cwd=cwd)
    except Exception as exc:  # noqa: BLE001 — identity must never break a turn
        logger.debug("Failed to resolve persona '%s': %s", persona_name, exc)
        return None
    if persona_dir is None:
        return None
    if persona_name.strip().lower() == "default" and _is_package_persona(persona_dir):
        return None
    return persona_dir


def soul_candidates(
    persona_name: str | None = None,
    active_space: str | None = None,
    cwd: Path | None = None,
) -> list[tuple[Path, str]]:
    """Every file-backed ``(path, tag)`` candidate in ``SOURCE_ORDER`` priority.

    Candidates are returned whether or not they exist — a report surface needs
    the absent ones too, and the resolver simply skips them. A community
    persona's composed ``soul.yaml`` has no single file and is handled by
    :func:`resolve_soul` alone.
    """
    candidates: list[tuple[Path, str]] = []

    if persona_name:
        persona_dir = _persona_dir(persona_name, cwd)
        if persona_dir is not None:
            candidates.append((persona_dir / "soul.md", "persona"))

    cfg = config_dir()
    if active_space:
        candidates.append((cfg / "spaces" / active_space / "SOUL.md", "space"))
    if cwd is not None:
        candidates.append((Path(cwd) / ".navig" / "SOUL.md", "folder-space"))

    candidates.append((cfg / "workspace" / "IDENTITY.md", "identity"))
    candidates.append((cfg / "workspace" / "SOUL.md", "workspace"))

    pkg_root = Path(__file__).parent.parent
    candidates.append((pkg_root / "resources" / "SOUL.default.md", "resources"))
    candidates.append((pkg_root / "agent" / "context" / "SOUL.md", "context"))
    return candidates


def resolve_soul(
    persona_name: str | None = None,
    active_space: str | None = None,
    cwd: Path | None = None,
) -> SoulResolution:
    """Resolve identity via the single chain, tagging the winner and the losers.

    Parameters
    ----------
    persona_name:
        Active persona slug. The package-shipped ``default`` is ignored — see
        :func:`_persona_dir`.
    active_space:
        Checks ``~/.navig/spaces/<active_space>/SOUL.md``.
    cwd:
        Working directory for project-local persona resolution and the
        folder-space ``<cwd>/.navig/SOUL.md`` probe. When ``None`` the
        folder-space step is skipped entirely (never guess a project root).
    """
    candidates = soul_candidates(persona_name, active_space, cwd)

    winner: tuple[str, str, Path | None] | None = None  # (raw, tag, path)
    shadowed: list[ShadowedSource] = []

    for path, tag in candidates:
        text = _try_read(path)
        if not text:
            continue
        if winner is None:
            winner = (text, tag, path)
        else:
            shadowed.append(ShadowedSource(tag=tag, path=path, chars=len(text)))

    # A community persona ships ``soul.yaml`` instead of ``soul.md``; compose the
    # injected identity from it so a registry-installed persona actually applies.
    if persona_name and (winner is None or winner[1] != "persona"):
        composed = _persona_soul_yaml(persona_name, cwd)
        if composed:
            if winner is not None:
                shadowed.insert(
                    0,
                    ShadowedSource(tag=winner[1], path=winner[2] or Path(), chars=len(winner[0])),
                )
            winner = (composed, "persona", None)

    if winner is None:
        return SoulResolution()

    raw, tag, path = winner
    return SoulResolution(
        raw=raw,
        source=tag,
        path=path,
        persona=persona_name.strip().lower() if (persona_name and tag == "persona") else "",
        shadowed=tuple(shadowed),
        revision=_revision_of(raw, tag),
    )


def _persona_soul_yaml(persona_name: str, cwd: Path | None) -> str:
    """Compose identity text from a community persona's ``soul.yaml`` (or "")."""
    persona_dir = _persona_dir(persona_name, cwd)
    if persona_dir is None:
        return ""
    try:
        from navig.personas.loader import read_soul_yaml  # noqa: PLC0415

        adapted = read_soul_yaml(persona_dir)
    except Exception as exc:  # noqa: BLE001
        logger.debug("Failed to adapt soul.yaml for '%s': %s", persona_name, exc)
        return ""
    if adapted is not None and adapted[1]:
        return str(adapted[1]).strip()
    return ""


def load_soul(
    persona_name: str | None = None,
    active_space: str | None = None,
    cwd: Path | None = None,
) -> str:
    """Return the winning source's raw text (``""`` when nothing resolves).

    Thin wrapper over :func:`resolve_soul` kept for the many callers that only
    need the text. Prefer ``resolve_soul`` when you also want to know *which*
    source won or what it shadowed.
    """
    return resolve_soul(persona_name, active_space, cwd).raw
