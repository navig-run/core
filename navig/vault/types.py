"""
NAVIG Vault Type Definitions

Core dataclasses for credential storage and management.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class CredentialType(str, Enum):
    """Supported credential types in the vault."""

    API_KEY = "api_key"
    OAUTH = "oauth"
    EMAIL = "email"
    TOKEN = "token"
    PASSWORD = "password"
    SSH_KEY = "ssh_key"
    GENERIC = "generic"


@dataclass
class Credential:
    """
    Full credential record with all metadata.

    This is the complete credential object including decrypted data.
    Only returned when explicitly fetching a credential, never in list operations.

    Attributes:
        id: Unique identifier (8 char UUID prefix)
        provider: Service name (e.g., "openai", "github", "gmail")
        profile_id: Namespace identifier (e.g., "default", "work", "personal")
        credential_type: Type of credential
        label: Human-readable name
        data: Decrypted credential payload (keys, tokens, etc.)
        metadata: Provider-specific non-secret data (email, scopes, etc.)
        enabled: Whether credential is active
        created_at: Creation timestamp
        updated_at: Last modification timestamp
        last_used_at: Last access timestamp (for auditing)
    """

    id: str
    provider: str
    profile_id: str
    credential_type: CredentialType
    label: str
    data: dict[str, Any]  # Decrypted secrets
    metadata: dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_used_at: datetime | None = None

    @staticmethod
    def generate_id() -> str:
        """Generate a short unique credential ID."""
        return str(uuid.uuid4())[:8]

    def get_secret(self, key: str = "api_key") -> str | None:
        """Get a secret value from the credential data."""
        return self.data.get(key)


@dataclass
class CredentialInfo:
    """
    Credential metadata for listing operations.

    This is a lightweight representation without any secret data.
    Safe to display in UIs and logs.

    Attributes:
        id: Unique identifier
        provider: Service name
        profile_id: Namespace identifier
        credential_type: Type of credential
        label: Human-readable name
        enabled: Whether credential is active
        created_at: Creation timestamp
        last_used_at: Last access timestamp
        metadata: Non-secret provider metadata
    """

    id: str
    provider: str
    profile_id: str
    credential_type: CredentialType
    label: str
    enabled: bool
    created_at: datetime
    last_used_at: datetime | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        status = "✓" if self.enabled else "✗"
        return f"[{status}] {self.provider}/{self.profile_id}: {self.label}"


@dataclass
class TestResult:
    """
    Result of credential validation.

    Returned by vault.test() with provider-specific validation results.

    Attributes:
        success: Whether the credential is valid
        message: Human-readable status message
        details: Provider-specific data (e.g., account info, quota)
        tested_at: Timestamp of the test
    """

    success: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    tested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __str__(self) -> str:
        status = "✅" if self.success else "❌"
        return f"{status} {self.message}"


# Provider presets for common configurations
PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "gmail": {
        "credential_type": CredentialType.EMAIL,
        "metadata": {
            "imap_host": "imap.gmail.com",
            "smtp_host": "smtp.gmail.com",
            "imap_port": 993,
            "smtp_port": 465,
        },
    },
    "outlook": {
        "credential_type": CredentialType.EMAIL,
        "metadata": {
            "imap_host": "outlook.office365.com",
            "smtp_host": "smtp.office365.com",
            "imap_port": 993,
            "smtp_port": 587,
        },
    },
    "fastmail": {
        "credential_type": CredentialType.EMAIL,
        "metadata": {
            "imap_host": "imap.fastmail.com",
            "smtp_host": "smtp.fastmail.com",
            "imap_port": 993,
            "smtp_port": 465,
        },
    },
    "openai": {
        "credential_type": CredentialType.API_KEY,
        "metadata": {"base_url": "https://api.openai.com/v1"},
    },
    "anthropic": {
        "credential_type": CredentialType.API_KEY,
        "metadata": {"base_url": "https://api.anthropic.com"},
    },
    "openrouter": {
        "credential_type": CredentialType.API_KEY,
        "metadata": {"base_url": "https://openrouter.ai/api/v1"},
    },
    "github": {
        "credential_type": CredentialType.TOKEN,
        "metadata": {"base_url": "https://api.github.com"},
    },
    "gitlab": {
        "credential_type": CredentialType.TOKEN,
        "metadata": {"base_url": "https://gitlab.com/api/v4"},
    },
    "matrix": {
        "credential_type": CredentialType.PASSWORD,
        "metadata": {
            "homeserver_url": "http://localhost:6167",
            "required_fields": ["homeserver", "user_id", "password"],
            "optional_fields": ["access_token", "device_name", "default_room_id"],
        },
    },
    "conduit": {
        "credential_type": CredentialType.TOKEN,
        "metadata": {
            "homeserver_url": "http://localhost:6167",
            "admin_api": "/_conduit/server_version",
        },
    },
}

# ---------------------------------------------------------------------------
# VaultItemKind / VaultItem — used by VaultStore (per-item DEK schema)
# ---------------------------------------------------------------------------


class VaultItemKind(str, Enum):
    """Kind discriminator for vault items stored in the new DEK schema."""

    SECRET = "secret"  # Generic secret / API key
    JSON = "json"  # Structured JSON blob (e.g. service-account.json)
    CERT = "cert"  # TLS / SSH certificate or key
    TOKEN = "token"  # OAuth / bearer token
    PASSWORD = "password"  # Password
    GENERIC = "generic"  # Catch-all
    # Extended kinds (used by adapters and migration)
    PROVIDER = "provider"  # Migrated / provider-keyed credential (V1 compat)
    NOTE = "note"  # Plain-text note or memo
    FILE = "file"  # Binary / JSON file (e.g. service-account key file)
    CREDENTIAL = "credential"  # Generic credential bundle


@dataclass
class VaultItem:
    """
    Single item stored in the vault with its own DEK (Data Encryption Key).

    The encrypted fields (``encrypted_dek``, ``encrypted_blob``) are used
    internally by :class:`~navig.vault.store.VaultStore` and
    :class:`~navig.vault.core.Vault`.  Callers that receive a
    ``VaultItem`` from high-level APIs (e.g. ``Vault.get_item()``) will
    find the decrypted content in ``payload``; the encrypted fields will be
    empty bytes in that context.
    """

    id: str
    kind: VaultItemKind
    label: str
    provider: str | None
    payload: bytes = b""  # Decrypted payload (populated by Vault after decrypt)
    # ── Internal encrypted storage fields ────────────────────────────────────
    encrypted_dek: bytes = field(default=b"", repr=False)   # DEK sealed with master key
    encrypted_blob: bytes = field(default=b"", repr=False)  # Payload sealed with DEK
    # ── Metadata / versioning ────────────────────────────────────────────────
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_used_at: datetime | None = None
    version: int = 1

    @staticmethod
    def new_id() -> str:
        """Generate a short random ID."""
        return str(uuid.uuid4())[:8]

    @property
    def enabled(self) -> bool:
        """Whether this item is enabled (reads from metadata, defaults to True)."""
        return bool(self.metadata.get("enabled", True))
