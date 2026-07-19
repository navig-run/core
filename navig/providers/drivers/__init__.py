"""NAVIG connection drivers — the pluggable backends behind a Connection.

See :mod:`navig.providers.drivers.base` for the contract. The fake driver
(:mod:`navig.providers.drivers.fake`) implements the full contract in-memory so
the connection service can be tested end-to-end without the Node bridge or any
network/official-runtime dependency.
"""

from __future__ import annotations

from navig.providers.drivers.base import (
    AuthStart,
    AuthStatus,
    ConnectResult,
    ModelInfo,
    ProviderDriver,
    ValidationResult,
)

__all__ = [
    "ProviderDriver",
    "ConnectResult",
    "AuthStart",
    "AuthStatus",
    "ValidationResult",
    "ModelInfo",
]
