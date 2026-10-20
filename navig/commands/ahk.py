"""Compat shim -- `navig ahk` now lives in ``navig_ahk.commands.ahk`` (navig-ahk).

Aliases itself to the implementation (not a re-export), so the registration map
(``"ahk"`` -> ``_try_register_ahk``, which imports ``ahk_app`` from here) reaches the same
module object the plugin's commands run. Windows-only, exactly as before: core registers
the verb only on win32, so importing this on Linux/macOS raises the same "install
navig-ahk" message as any other missing optional plugin.
"""
from __future__ import annotations

import sys as _sys

try:
    import navig_ahk.commands.ahk as _impl
except ImportError as exc:  # pragma: no cover - exercised only on a broken install
    raise ImportError(
        "`navig ahk` requires the navig-ahk package. Install it with: pip install navig-ahk"
    ) from exc

_sys.modules[__name__] = _impl
