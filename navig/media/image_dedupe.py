"""Compat shim — image dedupe now lives in the standalone ``navig-dedupe`` package.

The engine was extracted to ``navig_dedupe.image`` so navig and the standalone tool
share **one source of truth** (the same ``voice/`` → navig-audio pattern). Install it
with ``pip install navig[dedupe]``. Both ``navig media dedupe-images`` and the richer
``navig dedupe`` drive this same engine.
"""
from __future__ import annotations

try:
    from navig_dedupe.image import (  # noqa: F401
        IMAGE_EXT,
        cluster,
        dhash,
        extras_to_drop,
        hash_dir,
        list_images,
        quarantine,
        redundant_thumbs,
    )
except ImportError as exc:  # pragma: no cover - exercised only on a bare install
    raise ImportError(
        "Image dedupe requires the navig-dedupe engine. "
        "Install it with: pip install navig[dedupe]"
    ) from exc
