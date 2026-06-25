from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

CANONICAL_SPACES: tuple[str, ...] = (
    "default",  # zero-config personal space — always first
    "project",
    "career",
    "health",
    "finance",
    "learning",
    "life",
    "human",
    "residence",
    "company",
    "devops",
    "sysops",
)

SPACE_ALIASES: dict[str, str] = {
    "project-space": "project",
    "career-space": "career",
    "health-space": "health",
    "finance-space": "finance",
    "learning-space": "learning",
    "life-space": "life",
    "human-space": "human",
    "residence-space": "residence",
    "company-space": "company",
    "devops-space": "devops",
    "sysops-space": "sysops",
    "ops": "devops",
    "operations": "devops",
    "system-operations": "sysops",
}


@dataclass(frozen=True)
class SpaceConfig:
    requested_name: str
    canonical_name: str
    path: Path
    scope: str  # project | global


def normalize_space_name(name: str | None) -> str:
    value = (name or "").strip().lower()
    if not value:
        return "default"

    if value in CANONICAL_SPACES:
        return value

    alias = SPACE_ALIASES.get(value)
    if alias:
        return alias

    if value.endswith("-space"):
        candidate = value[:-6]
        if candidate in CANONICAL_SPACES:
            return candidate
        value = candidate or value

    # Free-form directory space: keep its own (slugified) name rather than
    # collapsing to "default" — collapsing silently broke directory spaces
    # like `homelab` (they all resolved to the default space).
    slug = re.sub(r"[^a-z0-9_-]+", "-", value).strip("-")
    return slug or "default"


def validate_space_name(name: str) -> bool:
    value = (name or "").strip().lower()
    return value in CANONICAL_SPACES or value in SPACE_ALIASES


def is_user_space(name: str) -> bool:
    """Return True for free-form spaces not in the canonical set."""
    return not validate_space_name(name)
