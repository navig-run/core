"""Compat shim — video dedupe now lives in the standalone ``navig-dedupe`` package.

The engine was extracted to ``navig_dedupe.video`` so navig and the standalone tool share
**one source of truth**. Install it with ``pip install navig[dedupe]``. Both
``navig media dedupe-video`` and ``navig dedupe --video`` drive this same engine (needs
``ffmpeg``/``ffprobe`` at runtime).
"""
from __future__ import annotations

try:
    from navig_dedupe.video import (  # noqa: F401
        VIDEO_EXT,
        cluster,
        signature,
        signature_dir,
    )
except ImportError as exc:  # pragma: no cover - exercised only on a bare install
    raise ImportError(
        "Video dedupe requires the navig-dedupe engine. "
        "Install it with: pip install navig[dedupe]"
    ) from exc
