"""Deprecated compatibility facade for the pre-migration ``navig.aria`` package.

The canonical home is :mod:`navig.reflex` (**NAVIG Reflex**). This module re-exports
the public surface and keeps the pre-migration class names (``AriaRouter``,
``AriaError``) as aliases, so existing imports keep working. New code must import
``navig.reflex``.

Removal: this facade exists only for compatibility and may be deleted once no in-tree
or documented external import uses ``navig.aria`` (see
``docs/reference/reflex-migration.md``).
"""

from __future__ import annotations

from navig.reflex import *  # noqa: F401,F403
from navig.reflex import __all__ as _reflex_all
from navig.reflex.errors import ReflexError as AriaError
from navig.reflex.service import ReflexRouter as AriaRouter

__all__ = [*_reflex_all, "AriaRouter", "AriaError"]
