"""Shared LLM default constants — zero-dependency leaf module.

These constants are the single source of truth for LLM generation defaults
used across multiple modules (llm_router, llm_generate, providers, routing).

Keeping them here avoids circular imports: navig.llm_router imports from
navig.providers, so providers/types.py cannot import back from llm_router.
Both sides import from this leaf module instead.
"""

from __future__ import annotations

# Base generation temperature used as a default when no mode-specific value
# is configured.  Range: 0.0 (deterministic) – 2.0 (very random).
_DEFAULT_TEMPERATURE: float = 0.7

# Maximum output tokens used as a default when no mode-specific limit is set.
_DEFAULT_MAX_TOKENS: int = 4_096
