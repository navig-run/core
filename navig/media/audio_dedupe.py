"""Compat shim — audio dedupe now lives in the standalone ``navig-dedupe`` package.

The engine was extracted to ``navig_dedupe.audio`` so navig and the standalone tool share
**one source of truth**. Install it with ``pip install navig[dedupe]``. Both
``navig media dedupe-audio`` and ``navig dedupe --audio`` drive this same engine (needs
the ``fpcalc`` binary at runtime).
"""
from __future__ import annotations

try:
    from navig_dedupe.audio import (  # noqa: F401
        AUDIO_EXT,
        cluster,
        fingerprint,
        fingerprint_dir,
        fpcalc_available,
        fpcalc_bin,
        quarantine,
    )
except ImportError as exc:  # pragma: no cover - exercised only on a bare install
    raise ImportError(
        "Audio dedupe requires the navig-dedupe engine. "
        "Install it with: pip install navig[dedupe]"
    ) from exc
