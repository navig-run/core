"""Compat shim -- this module now lives in ``navig_media_core.media.context_builder`` (navig-media-core).

The media engine lives in navig-media-core (extracted from navig-generate) so it runs without navig. Aliases
itself to the implementation rather than re-exporting it, so the module object is IDENTICAL:
tests patch ``navig.media.context_builder.<name>`` by dotted path and singletons stay shared. Same shape as
``navig.vault.core``.
"""
from __future__ import annotations

import sys as _sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - static analysis only
    from navig_media_core.media.context_builder import *  # noqa: F401,F403

try:
    import navig_media_core.media.context_builder as _impl
except ImportError as exc:  # pragma: no cover - exercised only on a broken install
    raise ImportError(
        "NAVIG's media engine requires the navig-media-core package. "
        "Install it with: pip install navig-media-core"
    ) from exc

_sys.modules[__name__] = _impl
