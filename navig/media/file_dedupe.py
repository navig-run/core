"""Compat shim — exact-file dedupe now lives in the standalone ``navig-dedupe`` package.

The engine was extracted to ``navig_dedupe.file`` so navig and the standalone tool share
**one source of truth**. Install it with ``pip install navig[dedupe]``. Both
``navig media dedupe-files`` and ``navig dedupe`` drive this same engine.
"""
from __future__ import annotations

try:
    from navig_dedupe.file import (  # noqa: F401
        cluster,
        hash_dir,
        sha256,
    )
except ImportError as exc:  # pragma: no cover - exercised only on a bare install
    raise ImportError(
        "File dedupe requires the navig-dedupe engine. "
        "Install it with: pip install navig[dedupe]"
    ) from exc
