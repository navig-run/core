"""NAVIG MIND — the owner's personal cognitive/language preferences.

MIND is a *behavioral preference* layer, not a new runtime, database, or
orchestration service. It resolves how the agent talks to the owner and drafts
personal correspondence — never how a product, codebase, or published artifact
is written. The artifact-language firewall is a mandatory invariant and is never
disabled by these settings (or by any ordinary style toggle).

Preference hierarchy (highest wins):

    1. Mandatory security + artifact-language rules  (always on, not configurable)
    2. Explicit user request in the conversation     (handled by the prompt)
    3. Session override                              (passed by the caller / OS session)
    4. Project preference                            (``.navig/config.yaml`` ``mind`` block)
    5. Global preference                             (``~/.navig/config.yaml`` ``mind`` block)
    6. Built-in defaults
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

logger = logging.getLogger(__name__)

# The artifact firewall is mandatory. It is reported in the effective profile for
# inspection but is NOT a user-facing toggle: nothing here may disable it, and no
# caller is allowed to turn it off through a style setting.
ARTIFACT_LANGUAGE_FIREWALL: bool = True

CONVERSATION_LANGUAGES = {"auto", "en", "ru", "fr"}
LEXICON_DENSITIES = {"off", "minimal", "balanced", "detailed", "intensive"}
DIGITALGHOST_FREQUENCIES = {"off", "rare", "balanced", "deep", "retrospective"}
DIGITALGHOST_ROLES = ("critic", "witness", "strategist", "narrator", "guardian", "linguist")


def _coerce_bool(value: Any, default: bool) -> bool:
    """Read a config boolean via the canonical coerced (string "false" → False)."""
    try:
        from navig.core.coerce import coerce_bool

        return bool(coerce_bool(value, default))
    except Exception:  # noqa: BLE001 — a caveat must never break preference resolution
        return default


def _norm_lang(value: Any, default: str) -> str:
    lang = str(value or default).strip().lower()
    return lang if lang in CONVERSATION_LANGUAGES else default


def _norm_choice(value: Any, allowed: set[str], default: str) -> str:
    choice = str(value or default).strip().lower()
    return choice if choice in allowed else default


@dataclass
class LexiconConfig:
    """Contextual vocabulary-learning preferences."""

    enabled: bool = True
    density: str = "balanced"  # off | minimal | balanced | detailed | intensive

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "LexiconConfig":
        d = data or {}
        return cls(
            enabled=_coerce_bool(d.get("enabled"), True),
            density=_norm_choice(d.get("density"), LEXICON_DENSITIES, "balanced"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "density": self.density}


@dataclass
class DigitalGhostConfig:
    """Companion editorial-commentary preferences."""

    enabled: bool = True
    frequency: str = "balanced"  # off | rare | balanced | deep | retrospective
    roles: tuple[str, ...] = DIGITALGHOST_ROLES

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "DigitalGhostConfig":
        d = data or {}
        roles = tuple(str(r).strip().lower() for r in (d.get("roles") or ()) if str(r).strip())
        if not roles:
            roles = DIGITALGHOST_ROLES
        return cls(
            enabled=_coerce_bool(d.get("enabled"), True),
            frequency=_norm_choice(d.get("frequency"), DIGITALGHOST_FREQUENCIES, "balanced"),
            roles=roles,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "frequency": self.frequency,
            "roles": list(self.roles),
        }


@dataclass
class MindConfig:
    """The resolved MIND profile."""

    enabled: bool = True
    conversation_language: str = "auto"  # auto | en | ru | fr
    creative_language: str = "ru"
    engineering_language: str = "en"
    commentary_language: str = "ru"
    lexicon: LexiconConfig = field(default_factory=LexiconConfig)
    digitalghost: DigitalGhostConfig = field(default_factory=DigitalGhostConfig)

    @classmethod
    def from_dict(cls, data: Mapping[str, Any] | None) -> "MindConfig":
        d = data or {}
        return cls(
            enabled=_coerce_bool(d.get("enabled"), True),
            conversation_language=_norm_lang(d.get("conversation_language"), "auto"),
            creative_language=_norm_lang(d.get("creative_language"), "ru"),
            engineering_language=_norm_lang(d.get("engineering_language"), "en"),
            commentary_language=_norm_lang(d.get("commentary_language"), "ru"),
            lexicon=LexiconConfig.from_dict(d.get("lexicon")),
            digitalghost=DigitalGhostConfig.from_dict(d.get("digitalghost")),
        )

    def to_dict(self) -> dict[str, Any]:
        """Effective profile, including the mandatory firewall for inspection."""
        return {
            "enabled": self.enabled,
            "conversation_language": self.conversation_language,
            "creative_language": self.creative_language,
            "engineering_language": self.engineering_language,
            "commentary_language": self.commentary_language,
            "lexicon": self.lexicon.to_dict(),
            "digitalghost": self.digitalghost.to_dict(),
            # Always True by construction; exposed so callers can show it.
            "artifact_language_firewall": ARTIFACT_LANGUAGE_FIREWALL,
        }


def _merge_onto(base: dict, ours: Mapping[str, Any]) -> dict:
    """Deep-merge *ours* over *base*; our values win, base-only keys survive."""
    out = dict(base)
    for key, val in ours.items():
        if isinstance(val, Mapping) and isinstance(out.get(key), dict):
            out[key] = _merge_onto(out[key], val)
        else:
            out[key] = val
    return out


def resolve_mind(
    global_mind: Mapping[str, Any] | None = None,
    project_mind: Mapping[str, Any] | None = None,
    session_override: Mapping[str, Any] | None = None,
) -> MindConfig:
    """Resolve the effective profile across the preference hierarchy.

    ``defaults ← global ← project ← session``, each layer deep-merged over the
    previous. A session override never mutates the stored global/project config —
    it only shadows it for this resolution.
    """
    merged: dict[str, Any] = {}
    for layer in (global_mind, project_mind, session_override):
        if layer:
            merged = _merge_onto(merged, layer)
    return MindConfig.from_dict(merged)


def _global_mind() -> Mapping[str, Any]:
    """Read the global ``mind`` block, or ``{}`` when absent/unreadable."""
    try:
        from navig.config import get_config_manager

        raw = get_config_manager().get("mind", {})
        return raw if isinstance(raw, Mapping) else {}
    except Exception as exc:  # noqa: BLE001 — a caveat never breaks prompt assembly
        logger.debug("mind: global config read skipped: %s", exc)
        return {}


def _project_mind(cwd: Path | None) -> Mapping[str, Any]:
    """Read the nearest ``.navig/config.yaml`` ``mind`` block above *cwd*."""
    start = cwd or Path.cwd()
    try:
        cur = start.resolve()
        for _ in range(20):  # bounded walk-up
            candidate = cur / ".navig" / "config.yaml"
            if candidate.is_file():
                import yaml

                data = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
                if isinstance(data, Mapping):
                    mind = data.get("mind", {})
                    if isinstance(mind, Mapping):
                        return mind
            if cur.parent == cur:
                break
            cur = cur.parent
    except Exception as exc:  # noqa: BLE001
        logger.debug("mind: project config read skipped: %s", exc)
    return {}


def get_effective_mind(
    session_override: Mapping[str, Any] | None = None,
    *,
    cwd: Path | None = None,
) -> MindConfig:
    """The resolved profile for the current process/session (inspectable).

    Reads global + project preference and applies *session_override* on top,
    then the built-in defaults underneath. Cheap: the global config is cached by
    ``ConfigManager`` and this returns a small dataclass.
    """
    return resolve_mind(
        global_mind=_global_mind(),
        project_mind=_project_mind(cwd),
        session_override=session_override,
    )


def mind_prompt_context(mind: MindConfig | None = None) -> str:
    """Render the resolved profile as a stable ``<mind>`` system-prompt block.

    Returns ``""`` when MIND is disabled, so the underlying agent workflow is
    byte-identical to today's when the feature is off. The block is deterministic
    for a given profile (no clock, no session state), so it preserves the
    prompt-stability invariant the soul loader depends on.
    """
    if mind is None:
        mind = get_effective_mind()
    if not mind.enabled:
        return ""

    lines: list[str] = [
        "You are operating under the owner's NAVIG MIND communication preferences. These "
        "are defaults for how you communicate, and they are overridable by the owner's "
        "explicit request in this conversation."
    ]

    lines.append(
        f"Language defaults: conversation follows the active conversation and the owner's "
        f"explicit choice ({mind.conversation_language}); creative exploration is in "
        f"{mind.creative_language}; engineering and technical artifacts are in "
        f"{mind.engineering_language}; professional correspondence to French-speaking "
        f"recipients is in French; editorial commentary is in {mind.commentary_language}."
    )
    lines.append(
        "Input language never dictates every output language: the deliverable's language "
        "is decided by the task and the owner's explicit instruction (a French letter stays "
        "French even when requested in English)."
    )

    if mind.lexicon.enabled and mind.lexicon.density != "off":
        lines.append(
            f"Lexicon (density: {mind.lexicon.density}): explain genuinely difficult English "
            "terms in Russian, with optional French equivalents, ONLY inside explanatory "
            "prose — never inside code, data, config, commands, or copyable deliverables."
        )

    if mind.digitalghost.enabled and mind.digitalghost.frequency != "off":
        roles = " · ".join(mind.digitalghost.roles)
        lines.append(
            f"DIGITALGHOST is your independent editorial voice (roles: {roles}). Commentary "
            f"frequency: {mind.digitalghost.frequency}. When a response warrants it, add a "
            f"concise {mind.commentary_language}-language observation — critical rather than "
            "decorative, exposing contradictions or missed trade-offs — clearly separated and "
            "OUTSIDE any copyable deliverable. Distinguish interpretation from verified fact. "
            "Never fabricate facts, memories, decisions, or completed work; stay silent when "
            "there is nothing meaningful to add. This is one voice inside the same reply, never "
            "a second agent and never an extra model call."
        )
        if mind.digitalghost.frequency == "retrospective":
            lines.append(
                "DIGITALGHOST retrospective is grounded: consult `navig recall` (session "
                "records, decisions, unfinished work, conflicts) or other accessible evidence "
                "before stating a fact about the past. Report only what the evidence supports — "
                "never an invented progress percentage, decision, or memory."
            )

    lines.append(
        "Lore & creative terminology: when asked about a creative term, offer literal, semantic, "
        "creative, and canonical renderings in English, Russian, and French with tone notes — a "
        "creative-assistance feature, not automated localization. Never auto-rename canonical "
        "terms or modify localization catalogs."
    )

    lines.append(
        "Artifact-language firewall (non-negotiable): never auto-translate or modify code, "
        "identifiers, comments, docs, commits, config, JSON/SQL keys, locale files, UI copy, "
        "or marketing based on language preferences. Personal communication preferences never "
        "change product or repository language; technical deliverables stay clean English by "
        "default."
    )

    return "<mind>\n" + "\n".join(lines) + "\n</mind>"
