"""Compat shim -- `navig cdp` now lives in ``navig_browser.commands.cdp`` (navig-browser).

Aliases itself to the implementation (not a re-export), so ``navig.commands.cdp.<name>``
patches and the registration map (``"cdp": ("navig.commands.cdp", "cdp_app")``) reach the
same module object the ``navig-browser`` console script runs.
"""
from __future__ import annotations

import sys as _sys

try:
    import navig_browser.commands.cdp as _impl
except ImportError as exc:  # pragma: no cover - exercised only on a broken install
    raise ImportError(
        "`navig cdp` requires the navig-browser package. Install it with: pip install navig-browser"
    ) from exc

_sys.modules[__name__] = _impl
