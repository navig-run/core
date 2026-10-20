"""Compat shim -- this module now lives in ``navig_generate.media.generation_service`` (navig-generate).

Generation orchestration is owned by navig-generate, beside the provider clients it drives
(ADR-0002). Aliases itself to the implementation rather than re-exporting it, so the module
object is IDENTICAL: tests patch ``navig.media.generation_service.<name>`` by dotted path and
singletons stay shared. Same shape as ``navig.vault.core``.

An older navig-generate (< 0.4.0) does not have the module yet; the engine then still lives
in navig-media-core (<= 0.1.x), so that path is tried second.
"""
from __future__ import annotations

import sys as _sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - static analysis only
    from navig_generate.media.generation_service import *  # noqa: F401,F403

try:
    import navig_generate.media.generation_service as _impl
except ImportError:
    try:
        import navig_media_core.media.generation_service as _impl
    except ImportError as exc:  # pragma: no cover - exercised only on a broken install
        raise ImportError(
            "NAVIG's media engine requires the navig-generate package. "
            "Install it with: pip install navig-generate"
        ) from exc

_sys.modules[__name__] = _impl
