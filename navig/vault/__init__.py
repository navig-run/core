"""NAVIG Vault — public API.

All vault access goes through :func:`get_vault` and the unified
:class:`Vault` implementation.
"""

from .core import Vault, get_vault, reveal_secret
from .core import Vault as CredentialsVault
from .logins import (
    LoginInfo,
    WebLogin,
    add_login,
    find_logins_for_domain,
    get_login,
    list_logins,
    remove_login,
    resolve_login,
)
from .secret_str import SecretStr
from .sessions import (
    SessionState,
    get_session,
    list_sessions,
    remove_session,
    save_session,
)
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
    "reveal_secret",
    "CredentialsVault",
    "Credential",
    "CredentialInfo",
    "CredentialType",
    "TestResult",
    "VaultItem",
    "VaultItemKind",
    "SecretStr",
    # Website logins (Stage 1)
    "WebLogin",
    "LoginInfo",
    "add_login",
    "get_login",
    "list_logins",
    "find_logins_for_domain",
    "resolve_login",
    "remove_login",
    # Authenticated sessions (Stage 1)
    "SessionState",
    "save_session",
    "get_session",
    "list_sessions",
    "remove_session",
]
