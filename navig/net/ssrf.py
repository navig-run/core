"""Compat shim -- the SSRF guard now lives in ``navig_sdk.ssrf`` (one copy for navig and
every standalone plugin).

Aliases itself to the implementation rather than re-exporting it, so the module object is
IDENTICAL: tests patch ``navig.net.ssrf.resolve_host`` / ``safe_fetch`` / ``check_url`` by
dotted path, and ten call sites import from here. Under a re-export those patches would land
on a copy while the real guard kept resolving hosts for real. Same shape as
``navig.vault.core``.
"""
from __future__ import annotations

import sys as _sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - static analysis only (see navig_sdk's py.typed)
    from navig_sdk.ssrf import *  # noqa: F401,F403

try:
    from navig_sdk import ssrf as _impl
except ImportError as exc:  # pragma: no cover - exercised only on a broken install
    raise ImportError(
        "NAVIG's SSRF guard requires the navig-sdk package. Install it with: pip install navig-sdk"
    ) from exc

_sys.modules[__name__] = _impl
