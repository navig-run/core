"""Compat shim -- this module now lives in ``navig_generate.media.tonality`` (navig-generate).

The media engine moved into navig-generate so ``navig-generate`` runs without navig. Aliases
itself to the implementation rather than re-exporting it, so the module object is IDENTICAL:
tests patch ``navig.media.tonality.<name>`` by dotted path and singletons stay shared. Same shape as
``navig.vault.core``.
"""
from __future__ import annotations

import sys as _sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - static analysis only
    from navig_generate.media.tonality import *  # noqa: F401,F403

try:
    import navig_generate.media.tonality as _impl
except ImportError as exc:  # pragma: no cover - exercised only on a broken install
    raise ImportError(
        "NAVIG's media engine requires the navig-generate package. "
        "Install it with: pip install navig-generate"
    ) from exc

_sys.modules[__name__] = _impl
