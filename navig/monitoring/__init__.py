"""Structured health-check API — the data layer under the monitoring surfaces.

Public entry points (see :mod:`navig.monitoring.checks` for the full contract)::

    from navig.monitoring import Sample, collect, collect_http, collect_with_errors

    samples = await collect("web-1", ["resources", "disk"])
    samples, failures = await collect_with_errors(None, ["resources"])

The CLI printers in ``navig.commands.monitoring`` consume the same command
constants and parsers, so both surfaces read a host through one data path.
"""

from __future__ import annotations

from navig.monitoring.checks import (
    DEFAULT_CHECKS,
    HOST_CHECKS,
    VALID_CHECKS,
    CheckFailure,
    Sample,
    collect,
    collect_http,
    collect_with_errors,
)

__all__ = [
    "DEFAULT_CHECKS",
    "HOST_CHECKS",
    "VALID_CHECKS",
    "CheckFailure",
    "Sample",
    "collect",
    "collect_http",
    "collect_with_errors",
]
