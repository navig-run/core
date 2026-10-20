"""Compat shim -- the MTProto user-account commands now live in navig-telegram.

``navig telegram`` attaches this group to its app; in-tree tests import it by this dotted
path to exercise the exit-honesty contract. Aliasing keeps one module object either way.
"""
from __future__ import annotations

import sys as _sys

try:
    import navig_telegram.commands._telegram_mtproto as _impl
except ImportError as exc:  # pragma: no cover - exercised only on a broken install
    raise ImportError(
        "the Telegram MTProto commands require the navig-telegram package. "
        "Install it with: pip install navig-telegram"
    ) from exc

_sys.modules[__name__] = _impl
