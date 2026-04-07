"""NAVIG Vault — public API.

All vault access should go through :func:`get_vault`.  The returned
:class:`Vault` instance is the unified credential store (AES-GCM + Argon2id)
and exposes both the modern label-based API and the legacy V1 adapter API.

Backward-compat names are preserved so existing imports keep working without
any changes:

    # Modern (preferred)
    from navig.vault import get_vault
    vault = get_vault()

    # Legacy V1 names (still work)
    from navig.vault import get_vault_v2, VaultV2, CredentialsVault
    from navig.vault.core_v2 import get_vault_v2  # also works
"""

from .core_v2 import Vault, get_vault
from .core_v2 import Vault as CredentialsVault  # backward compat — V1 call sites
from .core_v2 import Vault as VaultV2  # backward compat alias
from .core_v2 import get_vault as get_vault_v2  # backward compat alias
from .secret_str import SecretStr
from .types import (
    Credential,
    CredentialInfo,
    CredentialType,
    TestResult,
    VaultItem,
    VaultItemKind,
)

__all__ = [
    # Canonical names
    "Vault",
    "get_vault",
    # Backward compat
    "VaultV2",
    "get_vault_v2",
    "CredentialsVault",
    # Types
    "Credential",
    "CredentialInfo",
    "CredentialType",
    "TestResult",
    "VaultItem",
    "VaultItemKind",
    "SecretStr",
]
