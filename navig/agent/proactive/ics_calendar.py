"""Compat shim -- the ICS/CalDAV providers now live in the navig-calendar plugin
(``navig_calendar.ics``), so the plugin can read calendars without navig.

Aliases itself to the implementation rather than re-exporting it, so the module object is
IDENTICAL and dotted-path patches land on the real code (the ``navig.vault.core`` shape).
"""
from __future__ import annotations

import sys as _sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - static analysis only
    from navig_calendar.ics import *  # noqa: F401,F403

try:
    import navig_calendar.ics as _impl
except ImportError as exc:
    raise ImportError("ICS/CalDAV calendars need the navig-calendar plugin: pip install \"navig[calendar]\"") from exc

_sys.modules[__name__] = _impl
