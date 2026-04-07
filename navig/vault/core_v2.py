"""Backward-compatible Vault v2 shim.

Legacy callers may import ``navig.vault.core_v2.get_vault_v2``.
This module forwards to the canonical vault implementation in ``navig.vault.core``.
"""

from __future__ import annotations

from .core import Vault


def get_vault_v2() -> Vault:
    """Return the canonical vault instance (legacy alias)."""
    from . import core

    return core.get_vault()
