"""Compat shim -- this module now lives in ``navig_browser.signed_in_sites`` (navig-browser).

The browser engine lives in navig-browser so it runs without navig. Aliases itself to
the implementation rather than re-exporting it, so the module object is IDENTICAL: tests
patch ``navig.browser.signed_in_sites.<name>`` by dotted path. Same shape as
``navig.vault.core``.
"""
from __future__ import annotations

import sys as _sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - static analysis only
    from navig_browser.signed_in_sites import *  # noqa: F401,F403

try:
    import navig_browser.signed_in_sites as _impl
except ImportError as exc:  # pragma: no cover - exercised only on a broken install
    raise ImportError(
        "NAVIG's browser engine requires the navig-browser package. "
        "Install it with: pip install navig-browser"
    ) from exc

_sys.modules[__name__] = _impl
