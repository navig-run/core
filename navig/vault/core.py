"""
NAVIG Vault Core - Main CredentialsVault Class

The primary API for credential management across all NAVIG components.
"""

import builtins
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .encryption import VaultEncryption
from .secret_str import SecretStr
from .storage import VaultStorage
from .types import (
    PROVIDER_PRESETS,
    Credential,
    CredentialInfo,
    CredentialType,
    TestResult,
)
from .validators import get_validator


class CredentialsVault:
    """
    Unified credentials vault for NAVIG.

    Provides secure storage and retrieval of all credential types
    with profile/namespace support.

    Features:
    - Encrypted storage using Fernet (AES-128)
    - Multiple credential types (API keys, OAuth, email, tokens)
    - Profile namespaces for organizing credentials
    - Provider-specific validation
    - Audit logging of all access
    - Automatic migration from legacy auth-profiles.json

    Usage:
        from navig.vault import get_vault

        vault = get_vault()

        # Add a credential
        cred_id = vault.add(
            provider="openai",
            credential_type="api_key",
            data={"api_key": "sk-..."},
            profile_id="work",
        )

        # Get API key with fallback to env vars
        api_key = vault.get_api_key("openai")

        # Test a credential
        result = vault.test(cred_id)
    """

    DEFAULT_VAULT_PATH = Path.home() / ".navig" / "credentials" / "vault.db"
    ACTIVE_PROFILE_FILE = "active_profile"

    def __init__(
        self,
        vault_path: Path | None = None,
        auto_migrate: bool = True,
    ):
        """
        Initialize the credentials vault.

        Args:
            vault_path: Path to vault database (default: ~/.navig/credentials/vault.db)
            auto_migrate: Automatically migrate from auth-profiles.json if exists
        """
        self.vault_path = vault_path or self.DEFAULT_VAULT_PATH
        self.vault_dir = self.vault_path.parent

        self._encryption = VaultEncryption(self.vault_dir)
        self._storage = VaultStorage(self.vault_path, self._encryption)
        self._active_profile = self._load_active_profile()

        if auto_migrate:
            self._migrate_legacy()

    # ========================================================================
    # Profile Management
    # ========================================================================

    def set_active_profile(self, profile_id: str) -> None:
        """
        Set the active profile for credential resolution.

        The active profile is used when no specific profile is passed
        to get() or get_api_key().

        Args:
            profile_id: Profile to make active (e.g., "work", "personal")
        """
        self._active_profile = profile_id
        self._save_active_profile()

    def get_active_profile(self) -> str:
        """
        Get the currently active profile.

        Returns:
            Active profile ID (default: "default")
        """
        return self._active_profile

    def list_profiles(self) -> list[str]:
        """
        List all unique profile IDs in the vault.

        Returns:
            Sorted list of profile IDs
        """
        infos = self._storage.list_all()
        return sorted(set(c.profile_id for c in infos))

    def _load_active_profile(self) -> str:
        """Load active profile from file."""
        profile_file = self.vault_dir / self.ACTIVE_PROFILE_FILE
        if profile_file.exists():
            return profile_file.read_text().strip() or "default"
        return "default"

    def _save_active_profile(self) -> None:
        """Save active profile to file."""
        profile_file = self.vault_dir / self.ACTIVE_PROFILE_FILE
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        profile_file.write_text(self._active_profile)

    # ========================================================================
    # CRUD Operations
    # ========================================================================

    def add(
        self,
        provider: str,
        credential_type: str | CredentialType,
        data: dict[str, Any],
        profile_id: str = "default",
        label: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Add a new credential to the vault.

        Args:
            provider: Service name (e.g., "openai", "github", "gmail")
            credential_type: Type of credential (api_key, oauth, email, token, password)
            data: Secret data to store (e.g., {"api_key": "sk-..."})
            profile_id: Namespace identifier (default: "default")
            label: Human-readable label (auto-generated if not provided)
            metadata: Non-secret provider-specific data

        Returns:
            Generated credential ID (8 character string)

        Example:
            cred_id = vault.add(
                provider="openai",
                credential_type="api_key",
                data={"api_key": "sk-1234567890"},
                profile_id="work",
                label="Work OpenAI Account",
            )
        """
        if isinstance(credential_type, str):
            credential_type = CredentialType(credential_type)

        cred_id = Credential.generate_id()
        label = label or f"{provider} ({profile_id})"

        # Merge preset metadata if available
        final_metadata = {}
        if provider in PROVIDER_PRESETS:
            preset = PROVIDER_PRESETS[provider]
            final_metadata.update(preset.get("metadata", {}))
        if metadata:
            final_metadata.update(metadata)

        credential = Credential(
            id=cred_id,
            provider=provider,
            profile_id=profile_id,
            credential_type=credential_type,
            label=label,
            data=data,
            metadata=final_metadata,
        )

        self._storage.save(credential)
        self._storage.log_access(cred_id, "created", "vault.add")

        return cred_id

    def get(
        self,
        provider: str,
        profile_id: str | None = None,
        caller: str = "unknown",
    ) -> Credential | None:
        """
        Get credential for a provider.

        Resolution order:
        1. Specified profile_id
        2. Active profile
        3. "default" profile
        4. Any enabled credential for the provider

        Args:
            provider: Provider name to look up
            profile_id: Specific profile to use (overrides active profile)
            caller: Identifier for audit logging

        Returns:
            Credential if found, None otherwise
        """
        profile = profile_id or self._active_profile

        # Try specified/active profile
        cred = self._storage.get_by_provider_profile(provider, profile)

        # Try default profile if different
        if cred is None and profile != "default":
            cred = self._storage.get_by_provider_profile(provider, "default")

        # Try any available credential for this provider
        if cred is None:
            all_creds = self._storage.list_all(provider=provider)
            enabled = [c for c in all_creds if c.enabled]
            if enabled:
                cred = self._storage.get(enabled[0].id)

        if cred:
            self._storage.update_last_used(cred.id)
            self._storage.log_access(cred.id, "accessed", caller)

        return cred

    def get_by_id(
        self, credential_id: str, caller: str = "unknown"
    ) -> Credential | None:
        """
        Get credential by its ID.

        Args:
            credential_id: Unique credential identifier
            caller: Identifier for audit logging

        Returns:
            Credential if found, None otherwise
        """
        cred = self._storage.get(credential_id)
        if cred:
            self._storage.update_last_used(cred.id)
            self._storage.log_access(cred.id, "accessed", caller)
        return cred

    def get_secret(
        self,
        provider: str,
        key: str = "api_key",
        profile_id: str | None = None,
        caller: str = "unknown",
    ) -> SecretStr | None:
        """
        Get a specific secret value wrapped in SecretStr.

        This is the safest way to retrieve secrets as the returned
        SecretStr object prevents accidental logging.

        Args:
            provider: Provider name
            key: Key within credential data (e.g., "api_key", "password")
            profile_id: Specific profile to use
            caller: Identifier for audit logging

        Returns:
            SecretStr if found, None otherwise

        Example:
            secret = vault.get_secret("openai")
            if secret:
                # Will print "***"
                print(f"Using key: {secret}")
                # To use the actual value:
                api_key = secret.reveal()
        """
        cred = self.get(provider, profile_id, caller)
        if cred and key in cred.data:
            return SecretStr(cred.data[key])
        return None

    def list(
        self,
        provider: str | None = None,
        profile_id: str | None = None,
    ) -> list[CredentialInfo]:
        """
        List credentials (metadata only, no secrets).

        Safe to display in UIs and logs.

        Args:
            provider: Optional filter by provider
            profile_id: Optional filter by profile

        Returns:
            List of CredentialInfo objects
        """
        return self._storage.list_all(provider, profile_id)

    def update(
        self,
        credential_id: str,
        data: dict[str, Any] | None = None,
        label: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> bool:
        """
        Update credential data.

        Only provided fields are updated; others remain unchanged.

        Args:
            credential_id: Credential to update
            data: Secret data to merge/update
            label: New label
            metadata: Metadata to merge/update

        Returns:
            True if updated, False if credential not found
        """
        cred = self._storage.get(credential_id)
        if cred is None:
            return False

        if data is not None:
            cred.data.update(data)
        if label is not None:
            cred.label = label
        if metadata is not None:
            cred.metadata.update(metadata)

        cred.updated_at = datetime.now(timezone.utc)
        self._storage.save(cred)
        self._storage.log_access(credential_id, "updated", "vault.update")

        return True

    def delete(self, credential_id: str) -> bool:
        """
        Delete credential permanently.

        Args:
            credential_id: Credential to delete

        Returns:
            True if deleted, False if not found
        """
        self._storage.log_access(credential_id, "deleted", "vault.delete")
        return self._storage.delete(credential_id)

    def disable(self, credential_id: str) -> bool:
        """
        Disable credential without deletion.

        Disabled credentials are not returned by get() operations
        but can be re-enabled later.

        Args:
            credential_id: Credential to disable

        Returns:
            True if disabled, False if not found
        """
        success = self._storage.set_enabled(credential_id, False)
        if success:
            self._storage.log_access(credential_id, "disabled", "vault.disable")
        return success

    def enable(self, credential_id: str) -> bool:
        """
        Re-enable a disabled credential.

        Args:
            credential_id: Credential to enable

        Returns:
            True if enabled, False if not found
        """
        success = self._storage.set_enabled(credential_id, True)
        if success:
            self._storage.log_access(credential_id, "enabled", "vault.enable")
        return success

    def clone(
        self,
        credential_id: str,
        new_profile_id: str,
        new_label: str | None = None,
    ) -> str | None:
        """
        Clone credential to a new profile.

        Useful for copying credentials across environments
        (e.g., personal → work).

        Args:
            credential_id: Credential to clone
            new_profile_id: Target profile
            new_label: Optional new label (appends "(cloned)" if not specified)

        Returns:
            New credential ID if successful, None if source not found
        """
        original = self._storage.get(credential_id)
        if original is None:
            return None

        return self.add(
            provider=original.provider,
            credential_type=original.credential_type,
            data=original.data.copy(),
            profile_id=new_profile_id,
            label=new_label or f"{original.label} (cloned)",
            metadata=original.metadata.copy(),
        )

    # ========================================================================
    # Validation
    # ========================================================================

    def test(self, credential_id: str) -> TestResult:
        """
        Test a credential with provider-specific validation.

        Makes a minimal API call to verify the credential works.

        Args:
            credential_id: Credential to test

        Returns:
            TestResult with success status and details
        """
        cred = self._storage.get(credential_id)
        if cred is None:
            return TestResult(
                success=False,
                message="Credential not found",
            )

        validator = get_validator(cred.provider)
        result = validator.validate(cred)

        self._storage.log_access(
            credential_id,
            f"tested:{'success' if result.success else 'failed'}",
            "vault.test",
        )

        return result

    def test_provider(self, provider: str, profile_id: str | None = None) -> TestResult:
        """
        Test credential for a specific provider/profile.

        Args:
            provider: Provider name
            profile_id: Specific profile (uses active profile if not set)

        Returns:
            TestResult with success status and details
        """
        cred = self.get(provider, profile_id, caller="vault.test_provider")
        if cred is None:
            return TestResult(
                success=False,
                message=f"No credential found for {provider}",
            )
        return self.test(cred.id)

    # ========================================================================
    # Environment Variable Fallback
    # ========================================================================

    def get_api_key(
        self,
        provider: str,
        profile_id: str | None = None,
        caller: str = "unknown",
    ) -> str | None:
        """
        Get API key with environment variable fallback.

        This is the recommended method for getting API keys as it
        handles both vault and environment variable resolution.

        Resolution order:
        1. Vault credential for specified/active profile
        2. Vault credential for default profile
        3. Environment variables (e.g., OPENAI_API_KEY)

        Args:
            provider: Provider name
            profile_id: Specific profile (uses active profile if not set)
            caller: Identifier for audit logging

        Returns:
            API key string if found, None otherwise
        """
        secret = self.get_secret(provider, "api_key", profile_id, caller)
        if secret:
            return secret.reveal()

        # Also try 'token' key for token-based providers
        secret = self.get_secret(provider, "token", profile_id, caller)
        if secret:
            return secret.reveal()

        # Environment variable fallback
        return self._get_env_key(provider)

    def _get_env_key(self, provider: str) -> str | None:
        """Get API key from environment variables."""
        # Import here to avoid circular imports
        try:
            from navig.providers.types import PROVIDER_ENV_VARS

            env_vars = PROVIDER_ENV_VARS.get(provider, [])
        except ImportError:
            env_vars = []

        # Add standard pattern
        provider_upper = provider.upper().replace("-", "_")
        env_vars = list(env_vars) + [f"{provider_upper}_API_KEY"]

        for var in env_vars:
            value = os.environ.get(var, "").strip()
            if value:
                return value
        return None

    # ========================================================================
    # Audit
    # ========================================================================

    def get_audit_log(
        self,
        credential_id: str | None = None,
        limit: int = 100,
    ) -> builtins.list[dict]:
        """
        Get audit log entries.

        Args:
            credential_id: Filter by credential (all if None)
            limit: Maximum entries to return

        Returns:
            List of audit log entries as dicts
        """
        return self._storage.get_audit_log(credential_id, limit)

    # ========================================================================
    # Statistics
    # ========================================================================

    def count(
        self,
        provider: str | None = None,
        enabled_only: bool = False,
    ) -> int:
        """
        Count credentials in vault.

        Args:
            provider: Optional filter by provider
            enabled_only: Only count enabled credentials

        Returns:
            Number of matching credentials
        """
        return self._storage.count(provider, enabled_only)

    # ========================================================================
    # Migration
    # ========================================================================

    def _migrate_legacy(self) -> None:
        """
        Migrate from auth-profiles.json if it exists.

        Called automatically during initialization. Existing vault
        data is preserved (migration only runs if vault is empty).
        """
        legacy_path = self.vault_dir / "auth-profiles.json"
        if not legacy_path.exists():
            return

        # Skip if vault already has data
        if self._storage.count() > 0:
            return

        import json

        try:
            with open(legacy_path) as f:
                data = json.load(f)

            migrated = 0
            for profile_id, cred_data in data.get("profiles", {}).items():
                cred_type = cred_data.get("type", "api_key")
                provider = cred_data.get("provider", "unknown")

                # Map old format to new
                vault_data: dict[str, Any] = {}
                if cred_type == "api_key":
                    vault_data["api_key"] = cred_data.get("key", "")
                elif cred_type == "oauth":
                    vault_data["access_token"] = cred_data.get("accessToken", "")
                    vault_data["refresh_token"] = cred_data.get("refreshToken")
                    vault_data["expires_at"] = cred_data.get("expiresAt")
                    vault_data["client_id"] = cred_data.get("clientId")
                elif cred_type == "token":
                    vault_data["token"] = cred_data.get("token", "")
                    vault_data["expires"] = cred_data.get("expires")

                # Skip empty credentials
                if not any(v for v in vault_data.values() if v):
                    continue

                self.add(
                    provider=provider,
                    credential_type=cred_type,
                    data=vault_data,
                    profile_id=profile_id,
                    label=f"{provider} (migrated from auth-profiles)",
                    metadata={"email": cred_data.get("email")},
                )
                migrated += 1

            if migrated:
                # Rename old file to indicate migration
                backup_path = legacy_path.with_suffix(".json.migrated")
                legacy_path.rename(backup_path)
                print(f"✅ Migrated {migrated} credentials from auth-profiles.json")

        except Exception as e:
            print(f"⚠️  Failed to migrate legacy credentials: {e}")
