"""NAVIG Vault Core — redirect shim.

CredentialsVault is now a backward-compat alias for the unified Vault class
defined in core_v2.  This file exists only to satisfy legacy imports such as:

    from navig.vault.core import CredentialsVault

All new code should import from navig.vault directly.
"""

import os as _os
from pathlib import Path as _Path

from .core_v2 import Vault as CredentialsVault, get_vault

# Set DEFAULT_VAULT_PATH so callers can read CredentialsVault.DEFAULT_VAULT_PATH.
# Evaluated here (not in core_v2) so importlib.reload(this module) picks up env changes.
_cfg_dir = _Path(_os.environ.get("NAVIG_CONFIG_DIR", _Path.home() / ".navig"))
CredentialsVault.DEFAULT_VAULT_PATH = _cfg_dir / "credentials" / "vault.db"

__all__ = ["CredentialsVault", "get_vault"]
