"""Backwards-compat shim — import from ``navig.gateway_client`` instead.

Importing this module triggers ``navig/gateway/__init__.py`` which has a
circular import chain that deadlocks under Python 3.14.  CLI code should
import directly from ``navig.gateway_client`` (outside the package) to
avoid that deadlock.
"""

from navig.gateway_client import (  # noqa: F401
    gateway_base_url,
    gateway_cli_defaults,
    gateway_request,
    gateway_request_headers,
)

__all__ = [
    "gateway_base_url",
    "gateway_cli_defaults",
    "gateway_request",
    "gateway_request_headers",
]
