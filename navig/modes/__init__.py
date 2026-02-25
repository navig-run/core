"""navig.modes — operating mode profiles and PIN-gated switching."""

from .manager import (
    ModeProfile,
    all_modes,
    get_active_mode,
    get_active_mode_name,
    get_mode,
    has_pin,
    prompt_pin,
    set_active_mode,
    set_pin,
    verify_pin,
)

__all__ = [
    "ModeProfile",
    "all_modes",
    "get_active_mode",
    "get_active_mode_name",
    "get_mode",
    "has_pin",
    "prompt_pin",
    "set_active_mode",
    "set_pin",
    "verify_pin",
]
