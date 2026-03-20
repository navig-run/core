"""
navig.identity.entity — NaviEntity derivation from seed.

Given a deterministic seed, produces a unique entity with:
  - Name (ARCHETYPE-XXXX)
  - Palette (color scheme)
  - 9×9 symmetric sigil matrix (glyph grid)
  - Subsystem boot order
  - Resonance score (cosmetic)

All derived values are stable: same seed → same entity, always.
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Dict, List

# ── Design tokens: archetypes & palettes ────────────────────────────────────

ENTITY_ARCHETYPES: List[str] = [
    "Leviathan", "Wraith", "Chimera", "Specter", "Warden",
    "Oracle", "Sentinel", "Phantom", "Titan", "Nexus",
]

# Palette layout: [bg_hint,  entity_primary,  entity_accent]
# All palettes live in the NAVIG oceanic universe — deep sea / kraken / void.
# Entity color uniqueness comes from WHICH ocean variant they drew.
PALETTES: Dict[str, List[str]] = {
    # electric abyss — the core NAVIG signature
    "abyssal":    ["#060B1A", "#00D4FF", "#0057FF"],
    # bioluminescent deep — living light in the dark
    "biolumen":   ["#030F0A", "#00FF88", "#00C8A0"],
    # void pulse — cosmic depth, ultraviolet
    "void_pulse": ["#0A0518", "#A855F7", "#6D28D9"],
    # nautilus — pressure-blue, shell-iridescent
    "nautilus":   ["#06111E", "#4CC9F0", "#4361EE"],
    # kraken gold — bioluminescent amber, the apex predator
    "kraken_gold":["#0C0B04", "#F0B429", "#FF9F1C"],
}

SUBSYSTEMS: List[str] = [
    "vault", "gateway", "mesh", "cortex", "relay", "cipher",
]

# ── Machine name generation ──────────────────────────────────────────────────
# Curated morpheme banks — phonetically optimised for impact + memorability.
# Prefixes: strong consonant openings, monosyllabic punch.
_NAME_PREFIXES: List[str] = [
    "VOID", "IRON", "DRIFT", "GRAVE", "AXIS", "NULL", "SPEC", "FLUX",
    "CRUX", "ECHO", "NEON", "ZERO", "VEX", "HEX", "DARK", "GRIM",
    "COLD", "DEEP", "HARD", "LONE", "RUST", "WIRE", "BONE", "FELL",
]
# Suffixes: evocative endings — space/sea/predator register.
_NAME_SUFFIXES: List[str] = [
    "KITE", "SHADOW", "PULSE", "PETREL", "MOTH", "WREN", "BORNE", "HAWK",
    "LANCE", "BLADE", "FORGE", "STORM", "WARD", "GATE", "EDGE", "SHARD",
    "CLAW", "REEF", "CURL", "PIKE", "CROW", "VEIN", "HULL", "SPINE",
]


def generate_machine_name(seed: str) -> str:
    """
    Derive a deterministic elite navigator / deep-space probe pet name
    from the given seed string.

    Same seed always returns the same name.
    Example: generate_machine_name("abc123...") → "VOIDPETREL"
    """
    rng = random.Random(seed + "_machine_name")
    return rng.choice(_NAME_PREFIXES) + rng.choice(_NAME_SUFFIXES)

# Glyph set is split into three tiers for depth-shading in the renderer:
#   DENSE  → structural/bright    (▓ ⣿ ⣾ ╋ ╬)
#   MID    → mid-tone             (▒ ⣶ ⣤ ┼ ┯)
#   LIGHT  → dim/receding         (░ ⣀ ⠿ ⠶ ⠤)
#   VOID   → negative space (spaces weighted highest for breathing room)
_SIGIL_GLYPHS: List[str] = [
    # dense — weight 4
    "▓", "▓", "⣿", "⣿", "⣾", "⣻", "╋", "╬",
    # mid — weight 4
    "▒", "▒", "⣶", "⣤", "┼", "╪",  "┿", "╫",
    # light — weight 3
    "░", "░", "⣀", "⠿", "⠶", "⠤",
    # void — weight 6 (more breathing room)
    " ", " ", " ", " ", " ", " ",
]

# Full 9×9 grid (fits in 80-col terminals with padding margin)
_SIGIL_SIZE: int = 9

# Condensed 5×5 fallback for narrow terminals (< ~40 cols)
_SIGIL_SIZE_COMPACT: int = 5


# ── Data class ───────────────────────────────────────────────────────────────

@dataclass
class NaviEntity:
    seed: str
    name: str                      # e.g. "WRAITH-4A7F"
    archetype: str
    palette_key: str
    sigil_matrix: List[List[str]]  # N×N symmetric glyph grid
    sigil_compact: List[List[str]] # 5×5 fallback for narrow terminals
    subsystem_order: List[str]
    resonance: int                 # 0–100, cosmetic "entity strength"


# ── Derivation ───────────────────────────────────────────────────────────────

def derive_entity(seed: str) -> NaviEntity:
    """
    Deterministically derive a NaviEntity from the given hex seed.
    Identical seed → identical entity, always.
    """
    rng = random.Random(seed)

    archetype = rng.choice(ENTITY_ARCHETYPES)
    suffix    = seed[:4].upper()
    name      = f"{archetype.upper()}-{suffix}"
    palette_key = rng.choice(list(PALETTES.keys()))
    subsystem_order = rng.sample(SUBSYSTEMS, len(SUBSYSTEMS))
    resonance = rng.randint(60, 99)
    sigil_matrix  = _generate_sigil(rng, _SIGIL_SIZE)

    # Compact sigil uses a fresh deterministic sub-RNG derived from seed
    rng_c = random.Random(seed + "_compact")
    sigil_compact = _generate_sigil(rng_c, _SIGIL_SIZE_COMPACT)

    return NaviEntity(
        seed            = seed,
        name            = name,
        archetype       = archetype,
        palette_key     = palette_key,
        sigil_matrix    = sigil_matrix,
        sigil_compact   = sigil_compact,
        subsystem_order = subsystem_order,
        resonance       = resonance,
    )


def _generate_sigil(rng: random.Random, size: int) -> List[List[str]]:
    """
    Build an N×N symmetric glyph grid.
    Left half (including centre column) is randomly sampled;
    right half is mirrored — creating the QR-sigil aesthetic.
    """
    grid: List[List[str]] = []
    half = size // 2 + 1          # centre column included in left
    for _ in range(size):
        left    = [rng.choice(_SIGIL_GLYPHS) for _ in range(half)]
        right   = left[:-1][::-1] # mirror without duplicating centre
        grid.append(left + right)
    return grid
