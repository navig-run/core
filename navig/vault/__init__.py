"""
NAVIG Credentials Vault

Unified, secure credential storage for all NAVIG components.
Supports API keys, OAuth tokens, email credentials, and generic secrets.

Usage:
    from navig.vault import get_vault, CredentialsVault

    # Get global vault instance
    vault = get_vault()

    # Add a credential
    cred_id = vault.add(
        provider="openai",
        credential_type="api_key",
        data={"api_key": "sk-..."},
        profile_id="work",
        label="Work OpenAI"
    )

    # Get credential
    cred = vault.get("openai", profile_id="work")

    # Get API key with env var fallback
    api_key = vault.get_api_key("openai")

    # Test credential
    result = vault.test(cred_id)
"""

from .core import CredentialsVault
from .secret_str import SecretStr
from .types import Credential, CredentialInfo, CredentialType, TestResult

__all__ = [
    "CredentialsVault",
    "Credential",
    "CredentialInfo",
    "CredentialType",
    "TestResult",
    "SecretStr",
    "get_vault",
]

# Global vault singleton
_vault: CredentialsVault | None = None


def get_vault() -> CredentialsVault:
    """
    Get or create the global vault instance.

    The vault is lazily initialized on first access and reused
    for all subsequent calls.

    Returns:
        CredentialsVault: The global vault instance
    """
    global _vault
    if _vault is None:
        _vault = CredentialsVault()
    return _vault
