from navig.spaces.contracts import CANONICAL_SPACES, SPACE_ALIASES, SpaceConfig, normalize_space_name
from navig.spaces.resolver import discover_space_paths, get_default_space, resolve_space

__all__ = [
    "CANONICAL_SPACES",
    "SPACE_ALIASES",
    "SpaceConfig",
    "normalize_space_name",
    "discover_space_paths",
    "get_default_space",
    "resolve_space",
]
