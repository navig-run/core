"""Compat shim -- the calendar/email provider contracts now live in ``navig_sdk.pim``, shared with the
standalone navig-calendar and navig-email plugins.

Aliases itself to the implementation rather than re-exporting it, so the module object is
IDENTICAL and dotted-path patches land on the real code (the ``navig.vault.core`` shape).
"""
from __future__ import annotations

import sys as _sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - static analysis only
    from navig_sdk.pim import *  # noqa: F401,F403

try:
    import navig_sdk.pim as _impl
except ImportError as exc:
    raise ImportError("navig's provider contracts require the navig-sdk package: pip install navig-sdk") from exc

_sys.modules[__name__] = _impl
