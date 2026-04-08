"""
navig.core.tokens — Canonical token estimation utility.

All token-counting helpers across the codebase should call :func:`estimate_tokens`
instead of rolling their own ``len(text) // 4`` variants.

The default ratio (4.0 chars/token) matches the consensus of GPT-family
tokenisers for English text.  Callers that need a different ratio (e.g.
context_compressor uses 3.5, indexer reads it from config) pass it via the
*chars_per_token* parameter.
"""

from __future__ import annotations

__all__ = ["estimate_tokens"]


def estimate_tokens(text: str, *, chars_per_token: float = 4.0) -> int:
    """Return a rough token count for *text*.

    Parameters
    ----------
    text:
        The input string.
    chars_per_token:
        Average characters per token.  Defaults to ``4.0`` (GPT-family
        English heuristic).  Pass ``3.5`` for a more conservative
        estimate.

    Returns
    -------
    int
        Always ``>= 1`` for non-empty input, ``0`` for empty strings.
    """
    if not text:
        return 0
    return max(1, int(len(text) / chars_per_token))
