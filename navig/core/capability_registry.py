"""
NAVIG Capability Registry
=========================

Single source of truth for what tier every subsystem belongs to.

Tiers:
  CORE      — always active, no optional dependency, never gated
  OPTIONAL  — registered only when config key = true (ships disabled by default)
  LABS      — not loaded in default runtime; excluded from default help output

This module is a DATA FILE only in MVP1.
It is not wired to startup loading or CLI filtering yet — that is MVP2 work.

Current value:
  - navig doctor / diagnostics can read it
  - CI can assert no LABS module is imported on startup
  - Developers have a single place to understand the capability map
  - Reactivation paths are documented per entry

MVP2 wiring plan: see .navig/plans/CAPABILITY_REGISTRY.md
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# ---------------------------------------------------------------------------
# Tier definition
# ---------------------------------------------------------------------------


class CapabilityTier(str, Enum):
    CORE = "core"  # Always active
    OPTIONAL = "optional"  # Gated by config key; ships disabled
    LABS = "labs"  # Not in default runtime; excluded from help


# ---------------------------------------------------------------------------
# Capability entry
# ---------------------------------------------------------------------------


@dataclass
class CapabilityEntry:
    tier: CapabilityTier
    module: str | None = None  # Python import path (None = external/scripts)
    config_key: str | None = None  # e.g. "mesh.enabled" — None = no gate needed
    optional_dep: str | None = None  # pyproject.toml optional group name, if any
    cli_commands: list[str] = field(default_factory=list)  # navig subcommands for this cap
    notes: str = ""  # Human notes for diagnostics / docs


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

REGISTRY: dict[str, CapabilityEntry] = {
    # ── CORE — always loaded, never gated ──────────────────────────────────
    "daemon": CapabilityEntry(
        tier=CapabilityTier.CORE,
        module="navig.daemon",
        cli_commands=["service"],
        notes="Process supervisor — the runtime spine",
    ),
    "telegram": CapabilityEntry(
        tier=CapabilityTier.CORE,
        module="navig.gateway.channels.telegram",
        cli_commands=["telegram", "tg"],
        notes="Primary control channel; every AI interaction flows here",
    ),
    "vault": CapabilityEntry(
        tier=CapabilityTier.CORE,
        module="navig.vault",
        cli_commands=["vault", "cred", "cred-profile"],
        notes="Secrets store — required by nearly every feature",
    ),
    "memory": CapabilityEntry(
        tier=CapabilityTier.CORE,
        module="navig.memory",
        cli_commands=["memory"],
        notes="Conversation history, fact persistence, RAG pipeline",
    ),
    "tools": CapabilityEntry(
        tier=CapabilityTier.CORE,
        module="navig.tools",
        cli_commands=["tools"],
        notes="Tool registry and execution engine — the actual doing",
    ),
    "agent": CapabilityEntry(
        tier=CapabilityTier.CORE,
        module="navig.agent",
        cli_commands=["agent"],
        notes="Conversation loop, planning, LLM client, model routing",
    ),
    "storage": CapabilityEntry(
        tier=CapabilityTier.CORE,
        module="navig.storage",
        cli_commands=[],
        notes="SQLite engine backing memory, vault, conversations",
    ),
    "infra_commands": CapabilityEntry(
        tier=CapabilityTier.CORE,
        module="navig.commands",
        cli_commands=["host", "run", "db", "docker", "file", "web", "backup"],
        notes="Infrastructure command surface — the main user-facing value",
    ),
    "onboarding": CapabilityEntry(
        tier=CapabilityTier.CORE,
        module="navig.onboarding",
        cli_commands=["init"],
        notes="First-run setup wizard; loads only on navig init",
    ),
    "identity": CapabilityEntry(
        tier=CapabilityTier.CORE,
        module="navig.identity",
        config_key=None,  # no gate — always active
        cli_commands=["whoami"],
        notes=(
            "Node identity sigil — deterministic visual fingerprint derived from "
            "machine seed (MAC + hostname + username). "
            "Persisted at ~/.navig/entity.json. "
            "Generated once during navig init (sigil-genesis step); "
            "redisplayed via navig whoami at any time. "
            "Same seed always produces the same entity: archetype, palette, 9x9 glyph sigil."
        ),
    ),
    # ── OPTIONAL — gated; ships disabled by default ────────────────────────
    "matrix": CapabilityEntry(
        tier=CapabilityTier.OPTIONAL,
        module="navig.gateway.channels.matrix",
        config_key="matrix.enabled",
        optional_dep="matrix",
        cli_commands=["matrix", "mx"],
        notes=(
            "Matrix protocol control channel with optional E2EE. "
            "Ships disabled. Enable: matrix.enabled: true + pip install navig[matrix]. "
            "Full E2EE requires matrix.e2ee: true separately. "
            "MVP2 trigger: user requests Matrix control over Telegram."
        ),
    ),
    "mesh": CapabilityEntry(
        tier=CapabilityTier.OPTIONAL,
        module="navig.mesh",
        config_key="mesh.enabled",
        cli_commands=["mesh", "flux", "fx"],
        notes=(
            "LAN P2P node mesh with leader election. "
            "Ships disabled. Shutdown yield hook gated by mesh.enabled. "
            "MVP2 trigger: user has 2+ stable NAVIG nodes."
        ),
    ),
    "deck": CapabilityEntry(
        tier=CapabilityTier.OPTIONAL,
        module="navig.gateway.deck",
        config_key="gateway.deck_enabled",
        cli_commands=["deck"],
        notes=(
            "Web dashboard (vault UI, model selector, auth). "
            "Ships disabled. Enable: gateway.deck_enabled: true. "
            "MVP2 trigger: Telegram is the reference experience and solid."
        ),
    ),
    "voice": CapabilityEntry(
        tier=CapabilityTier.OPTIONAL,
        module="navig.voice",
        config_key="voice.enabled",
        optional_dep="voice",
        cli_commands=["voice"],
        notes=(
            "Basic STT/TTS ships as try-import (already well-guarded). "
            "wake_word.py and streaming_stt.py are MVP2+ — too much platform surface. "
            "Enable full voice: voice.enabled: true + pip install navig[voice]."
        ),
    ),
    "blackbox": CapabilityEntry(
        tier=CapabilityTier.OPTIONAL,
        module="navig.blackbox",
        config_key="blackbox.enabled",
        cli_commands=["blackbox", "bb"],
        notes=(
            "Event timeline recorder + forensic export. "
            "recorder.py + export.py: safe for MVP1.5. "
            "seal.py + bundle.py (crypto-sealed): MVP2+ only. "
            "MVP2 trigger: users report diagnosis difficulty."
        ),
    ),
    "selfheal": CapabilityEntry(
        tier=CapabilityTier.OPTIONAL,
        module="navig.selfheal",
        config_key="contribute.enabled",
        cli_commands=["contribute"],
        notes=(
            "scanner.py + patcher.py: safe read-only diagnostics (MVP1.5). "
            "heal_pr_submitter.py + pr_builder.py: MVP2+ ONLY. "
            "Config default: contribute.enabled: false (already set)."
        ),
    ),
    "proactive": CapabilityEntry(
        tier=CapabilityTier.OPTIONAL,
        module="navig.agent.proactive",
        config_key="proactive.enabled",
        cli_commands=["proactive"],
        notes=(
            "user_state.py + engagement.py: kept active via try-import guards in Telegram. "
            "External providers (google_calendar, ics_calendar, imap_email): MVP2+ only. "
            "Config default: proactive.enabled: false (providers off by default)."
        ),
    ),
    # ── LABS — not in default runtime; excluded from default help ──────────
    "formations": CapabilityEntry(
        tier=CapabilityTier.LABS,
        module="navig.formations",
        config_key="formations.enabled",
        cli_commands=["formation", "council"],
        notes=(
            "Multi-agent orchestration. "
            "Already isolated — not imported at startup. "
            "MVP2 trigger: single-agent NAVIG is excellent; users need parallel delegation."
        ),
    ),
    "perf": CapabilityEntry(
        tier=CapabilityTier.LABS,
        module="navig.perf",
        config_key="perf.enabled",
        cli_commands=[],
        notes=(
            "Internal profiler. __init__.py is empty; profiler.py appears unused. "
            "MVP2: wire to navig doctor --perf on demand. "
            "Do not add to hot path — profile after identifying top 3 bottlenecks."
        ),
    ),
    "genesis_lab": CapabilityEntry(
        tier=CapabilityTier.LABS,
        module=None,  # lives in scripts/, not navig/ package
        config_key=None,
        cli_commands=[],
        notes=(
            "Visual particle-system showcase. scripts/genesis_lab/ — standalone script only. "
            "Never imported by production code. "
            "MVP2: consider moving to contrib/ or a separate repo for community use."
        ),
    ),
}


# ---------------------------------------------------------------------------
# Helpers (read-only — no startup side effects)
# ---------------------------------------------------------------------------


def get_tier(capability: str) -> CapabilityTier | None:
    """Return the tier of a capability, or None if unknown."""
    entry = REGISTRY.get(capability)
    return entry.tier if entry else None


def get_core() -> dict[str, CapabilityEntry]:
    """Return all CORE capabilities."""
    return {k: v for k, v in REGISTRY.items() if v.tier == CapabilityTier.CORE}


def get_optional() -> dict[str, CapabilityEntry]:
    """Return all OPTIONAL capabilities."""
    return {k: v for k, v in REGISTRY.items() if v.tier == CapabilityTier.OPTIONAL}


def get_labs() -> dict[str, CapabilityEntry]:
    """Return all LABS capabilities."""
    return {k: v for k, v in REGISTRY.items() if v.tier == CapabilityTier.LABS}


def is_enabled(capability: str, config: dict | None = None) -> bool:
    """
    Return True if the capability should be active given the provided config dict.

    CORE capabilities are always enabled.
    OPTIONAL / LABS capabilities require config[entry.config_key] == True.

    In MVP1 this is a data-only helper — it reads a passed config dict.
    In MVP2 it will read from get_config_manager() directly.
    """
    entry = REGISTRY.get(capability)
    if entry is None:
        return False
    if entry.tier == CapabilityTier.CORE:
        return True
    if entry.config_key is None:
        return False  # no gate defined → off by default (except cosmetic uses)
    if config is None:
        return False
    # Walk dot-separated key path
    val: object = config
    for part in entry.config_key.split("."):
        if not isinstance(val, dict):
            return False
        val = val.get(part, False)
    return bool(val)
