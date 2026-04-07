"""NAVIG Vault — public API.

All vault access goes through :func:`get_vault` and the unified
:class:`Vault` implementation.
"""

from .core import Vault, get_vault
from .core import Vault as CredentialsVault
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
    "Vault",
    "get_vault",
    "CredentialsVault",
    "Credential",
    "CredentialInfo",
    "CredentialType",
    "TestResult",
    "VaultItem",
    "VaultItemKind",
    "SecretStr",
]
