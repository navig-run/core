"""navig.personas — unified persona management package.

Public API
----------
switch_persona(name, user_id, chat_id, ...)  →  PersonaConfig
get_active_persona(user_id) → str
list_personas(cwd) → list[PersonaConfig]
load_persona(name, cwd) → (PersonaConfig, soul_md)
load_soul(persona_name, active_space, cwd) → str
"""
from __future__ import annotations

from navig.personas.contracts import (
    BUILTIN_PERSONAS,
    PersonaConfig,
    normalize_persona_name,
    validate_persona_name,
)
from navig.personas.loader import load_persona
from navig.personas.manager import (
    PersonaSwitchError,
    get_active_persona_config,
    list_personas,
    switch_persona,
)
from navig.personas.resolver import discover_persona_paths, resolve_persona
from navig.personas.soul_loader import load_soul
from navig.personas.store import get_active_persona, set_active_persona

__all__ = [
    "BUILTIN_PERSONAS",
    "PersonaConfig",
    "PersonaSwitchError",
    "normalize_persona_name",
    "validate_persona_name",
    "load_persona",
    "load_soul",
    "resolve_persona",
    "discover_persona_paths",
    "get_active_persona",
    "set_active_persona",
    "get_active_persona_config",
    "list_personas",
    "switch_persona",
]
