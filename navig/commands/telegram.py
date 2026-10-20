"""Compat shim -- `navig telegram` now lives in ``navig_telegram.commands.telegram``.

Aliases itself to the implementation (not a re-export), so the registration map
(``"telegram": ("navig.commands.telegram", "telegram_app")``), the ``tg`` alias and every
in-tree import keep reaching the same module object the plugin's commands run.
"""
from __future__ import annotations

import sys as _sys

try:
    import navig_telegram.commands.telegram as _impl
except ImportError as exc:  # pragma: no cover - exercised only on a broken install
    raise ImportError(
        "`navig telegram` requires the navig-telegram package. "
        "Install it with: pip install navig-telegram"
    ) from exc

_sys.modules[__name__] = _impl
