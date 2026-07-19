"""Canonical, config-safe value coercion.

**Why this exists.** `navig config set <k> <v>` stores the CLI argument *verbatim* as a
string (a documented sharp edge), so ``navig config set x false`` writes the string
``"false"`` — and ``bool("false")`` is ``True``. A consumer that reads the value and tests
it directly (``if cfg.get("x"):``) therefore sees a *disabled* flag as *enabled*. This has
bitten the codebase repeatedly, which is why ~14 near-identical ``_coerce_bool`` / ``_truthy``
/ ``_as_bool`` helpers grew up across the tree — with **three incompatible truth tables**
(whitelist-truthy, blacklist-falsy, tri-state-with-default). That divergence means
``navig config set x on`` can resolve differently depending on which subsystem reads ``x``.

``coerce_bool`` is the one place to fix that. New config-boolean reads should use it; the
existing scattered helpers are a documented consolidation follow-up (see the CLAUDE.md
sharp-edge note) — they are NOT swept here because their differing truth tables make a blind
replacement a behaviour change.

The truth table is the union the scattered helpers collectively relied on:

    True  ← real ``True`` · non-zero numbers · "1" "true" "yes" "on" "t" "y"
    False ← real ``False`` · zero · "0" "false" "no" "off" "f" "n" "" (empty)
    None  → *default*
    any other string → *default*  (an unrecognised token is not silently "truthy")

All string matching is case-insensitive and whitespace-trimmed.
"""

from __future__ import annotations

from typing import Any

__all__ = ["coerce_bool"]

# Case-folded token sets. Kept as frozensets for O(1) membership and immutability.
_TRUE_TOKENS = frozenset({"1", "true", "yes", "on", "t", "y"})
_FALSE_TOKENS = frozenset({"0", "false", "no", "off", "f", "n", ""})


def coerce_bool(value: Any, default: bool = False) -> bool:
    """Coerce a config / env / JSON value to ``bool``, tolerating the string forms
    ``navig config set`` stores.

    Args:
        value: The raw value — a real bool, ``None``, a number, or a string token.
        default: Returned for ``None`` and for unrecognised strings, so an ambiguous
            value never silently flips to ``True`` (the exact ``bool("false")`` trap).

    Returns:
        The coerced boolean.

    Examples:
        >>> coerce_bool("false")      # the footgun the whole module exists for
        False
        >>> coerce_bool("on")
        True
        >>> coerce_bool(None, default=True)
        True
        >>> coerce_bool("maybe", default=False)   # unknown token → default, not True
        False
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    # Real numbers (and numeric bools already handled above): 0 → False, else True.
    if isinstance(value, (int, float)):
        return bool(value)
    token = str(value).strip().lower()
    if token in _TRUE_TOKENS:
        return True
    if token in _FALSE_TOKENS:
        return False
    return default
