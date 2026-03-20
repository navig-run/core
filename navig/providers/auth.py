"""
NAVIG AI Providers - Auth Profile Store

Manages secure storage of API keys and OAuth credentials.
Based on multi-provider client architecture.
"""
import json
import os
import time
from pathlib import Path
from typing import List, Optional, Tuple

from .types import (
    PROVIDER_ENV_VARS,
    ApiKeyCredential,
    AuthProfileCredential,
    AuthProfileStore,
    OAuthCredential,
    ProfileUsageStats,
    TokenCredential,
)


class AuthProfileManager:
    """
    Manages authentication profiles for AI providers.
    
    Supports:
    - API key credentials (static keys)
    - Token credentials (bearer tokens with optional expiry)
    - OAuth credentials (with refresh capability)
    - Environment variable resolution
    - Cooldown/backoff for rate-limited profiles
    """

    STORE_VERSION = 1
    COOLDOWN_HOURS = 5
    MAX_COOLDOWN_HOURS = 24

    def __init__(self, config_dir: Optional[Path] = None):
        """
        Initialize the auth profile manager.
        
        Args:
            config_dir: Path to NAVIG config directory (default: ~/.navig)
        """
        if config_dir is None:
            config_dir = Path.home() / ".navig"
        self.config_dir = config_dir
        self.credentials_dir = config_dir / "credentials"
        self.store_path = self.credentials_dir / "auth-profiles.json"
        self._store: Optional[AuthProfileStore] = None

        # Ensure directories exist
        self.credentials_dir.mkdir(parents=True, exist_ok=True)

    @property
    def store(self) -> AuthProfileStore:
        """Get or load the auth profile store."""
        if self._store is None:
            self._store = self._load_store()
        return self._store

    def _load_store(self) -> AuthProfileStore:
        """Load auth profiles from disk."""
        if not self.store_path.exists():
            return AuthProfileStore()

        try:
            with open(self.store_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            profiles = {}
            for profile_id, cred_data in data.get("profiles", {}).items():
                cred = self._parse_credential(cred_data)
                if cred:
                    profiles[profile_id] = cred

            usage_stats = {}
            for profile_id, stats_data in data.get("usageStats", {}).items():
                usage_stats[profile_id] = ProfileUsageStats(
                    last_used=stats_data.get("lastUsed"),
                    cooldown_until=stats_data.get("cooldownUntil"),
                    error_count=stats_data.get("errorCount", 0),
                    last_failure_at=stats_data.get("lastFailureAt"),
                    failure_reason=stats_data.get("failureReason"),
                )

            return AuthProfileStore(
                version=data.get("version", self.STORE_VERSION),
                profiles=profiles,
                order=data.get("order", {}),
                last_good=data.get("lastGood", {}),
                usage_stats=usage_stats,
            )
        except Exception as e:
            print(f"⚠️  Failed to load auth profiles: {e}")
            return AuthProfileStore()

    def _parse_credential(self, data: dict) -> Optional[AuthProfileCredential]:
        """Parse a credential from JSON data."""
        cred_type = data.get("type")
        provider = data.get("provider", "")

        if cred_type == "api_key":
            return ApiKeyCredential(
                provider=provider,
                key=data.get("key", ""),
                email=data.get("email"),
            )
        elif cred_type == "token":
            return TokenCredential(
                provider=provider,
                token=data.get("token", ""),
                expires=data.get("expires"),
                email=data.get("email"),
            )
        elif cred_type == "oauth":
            return OAuthCredential(
                provider=provider,
                access_token=data.get("accessToken", ""),
                refresh_token=data.get("refreshToken"),
                expires_at=data.get("expiresAt"),
                client_id=data.get("clientId"),
                email=data.get("email"),
            )
        return None

    def _serialize_credential(self, cred: AuthProfileCredential) -> dict:
        """Serialize a credential to JSON-compatible dict."""
        if isinstance(cred, ApiKeyCredential):
            return {
                "type": "api_key",
                "provider": cred.provider,
                "key": cred.key,
                "email": cred.email,
            }
        elif isinstance(cred, TokenCredential):
            return {
                "type": "token",
                "provider": cred.provider,
                "token": cred.token,
                "expires": cred.expires,
                "email": cred.email,
            }
        elif isinstance(cred, OAuthCredential):
            return {
                "type": "oauth",
                "provider": cred.provider,
                "accessToken": cred.access_token,
                "refreshToken": cred.refresh_token,
                "expiresAt": cred.expires_at,
                "clientId": cred.client_id,
                "email": cred.email,
            }
        return {}

    def save(self) -> None:
        """Save auth profiles to disk."""
        if self._store is None:
            return

        data = {
            "version": self._store.version,
            "profiles": {
                pid: self._serialize_credential(cred)
                for pid, cred in self._store.profiles.items()
            },
            "order": self._store.order,
            "lastGood": self._store.last_good,
            "usageStats": {
                pid: {
                    "lastUsed": stats.last_used,
                    "cooldownUntil": stats.cooldown_until,
                    "errorCount": stats.error_count,
                    "lastFailureAt": stats.last_failure_at,
                    "failureReason": stats.failure_reason,
                }
                for pid, stats in self._store.usage_stats.items()
            },
        }

        # Write with restrictive permissions
        self.store_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.store_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)

        # Set file permissions (Unix only)
        try:
            os.chmod(self.store_path, 0o600)
        except OSError:
            pass  # Windows doesn't support chmod the same way

    def add_api_key(
        self,
        provider: str,
        api_key: str,
        profile_id: Optional[str] = None,
        email: Optional[str] = None,
    ) -> str:
        """
        Add an API key credential.
        
        Args:
            provider: Provider name (e.g., "openai", "anthropic")
            api_key: The API key
            profile_id: Optional custom profile ID (default: provider name)
            email: Optional email associated with the key
        
        Returns:
            The profile ID
        """
        if profile_id is None:
            profile_id = provider

        self.store.profiles[profile_id] = ApiKeyCredential(
            provider=provider,
            key=api_key,
            email=email,
        )
        self.save()
        return profile_id

    def add_token(
        self,
        provider: str,
        token: str,
        profile_id: Optional[str] = None,
        expires: Optional[int] = None,
        email: Optional[str] = None,
    ) -> str:
        """Add a bearer token credential."""
        if profile_id is None:
            profile_id = provider

        self.store.profiles[profile_id] = TokenCredential(
            provider=provider,
            token=token,
            expires=expires,
            email=email,
        )
        self.save()
        return profile_id

    def remove_profile(self, profile_id: str) -> bool:
        """Remove a profile by ID."""
        if profile_id in self.store.profiles:
            del self.store.profiles[profile_id]
            if profile_id in self.store.usage_stats:
                del self.store.usage_stats[profile_id]
            self.save()
            return True
        return False

    def list_profiles(self, provider: Optional[str] = None) -> List[str]:
        """
        List all profile IDs, optionally filtered by provider.
        
        Args:
            provider: Optional provider to filter by
        
        Returns:
            List of profile IDs
        """
        if provider is None:
            return list(self.store.profiles.keys())

        return [
            pid for pid, cred in self.store.profiles.items()
            if cred.provider == provider
        ]

    def get_api_key(self, provider: str, profile_id: Optional[str] = None) -> Optional[str]:
        """
        Get an API key for a provider.
        
        Resolution order:
        1. Specific profile_id if provided
        2. Profiles matching provider (respecting order)
        3. Environment variables
        
        Args:
            provider: Provider name
            profile_id: Optional specific profile to use
        
        Returns:
            API key or None if not found
        """
        # Try specific profile
        if profile_id:
            cred = self.store.profiles.get(profile_id)
            if cred:
                return self._extract_key(cred)

        # Try profiles for this provider
        profile_order = self.store.order.get(provider, [])
        for pid in profile_order:
            if self._is_profile_available(pid):
                cred = self.store.profiles.get(pid)
                if cred and cred.provider == provider:
                    return self._extract_key(cred)

        # Try any profile for this provider
        for pid, cred in self.store.profiles.items():
            if cred.provider == provider and self._is_profile_available(pid):
                return self._extract_key(cred)

        # Try environment variables
        return self._get_env_key(provider)

    def _extract_key(self, cred: AuthProfileCredential) -> Optional[str]:
        """Extract the key/token from a credential."""
        if isinstance(cred, ApiKeyCredential):
            return cred.key
        elif isinstance(cred, TokenCredential):
            # Check expiry
            if cred.expires and cred.expires < int(time.time() * 1000):
                return None
            return cred.token
        elif isinstance(cred, OAuthCredential):
            # Check expiry
            if cred.expires_at and cred.expires_at < int(time.time() * 1000):
                return None
            return cred.access_token
        return None

    def _get_env_key(self, provider: str) -> Optional[str]:
        """Get API key from environment variables."""
        env_vars = PROVIDER_ENV_VARS.get(provider, [])

        # Also try provider-specific pattern
        provider_upper = provider.upper().replace("-", "_")
        env_vars = list(env_vars) + [f"{provider_upper}_API_KEY"]

        for var in env_vars:
            value = os.environ.get(var, "").strip()
            if value:
                return value
        return None

    def _is_profile_available(self, profile_id: str) -> bool:
        """Check if a profile is available (not in cooldown)."""
        stats = self.store.usage_stats.get(profile_id)
        if not stats:
            return True

        if stats.cooldown_until:
            now = int(time.time() * 1000)
            if now < stats.cooldown_until:
                return False

        return True

    def mark_profile_used(self, profile_id: str) -> None:
        """Mark a profile as used (for round-robin)."""
        if profile_id not in self.store.usage_stats:
            self.store.usage_stats[profile_id] = ProfileUsageStats()

        self.store.usage_stats[profile_id].last_used = int(time.time() * 1000)
        self.save()

    def mark_profile_success(self, provider: str, profile_id: str) -> None:
        """Mark a profile as successful."""
        self.store.last_good[provider] = profile_id

        # Clear any cooldown
        if profile_id in self.store.usage_stats:
            stats = self.store.usage_stats[profile_id]
            stats.cooldown_until = None
            stats.error_count = 0
            stats.failure_reason = None

        self.save()

    def mark_profile_failure(
        self,
        profile_id: str,
        reason: str = "unknown",
        apply_cooldown: bool = True,
    ) -> None:
        """
        Mark a profile as failed.
        
        Args:
            profile_id: The profile that failed
            reason: Failure reason (auth, rate_limit, billing, timeout, unknown)
            apply_cooldown: Whether to apply cooldown
        """
        if profile_id not in self.store.usage_stats:
            self.store.usage_stats[profile_id] = ProfileUsageStats()

        stats = self.store.usage_stats[profile_id]
        stats.error_count = (stats.error_count or 0) + 1
        stats.last_failure_at = int(time.time() * 1000)
        stats.failure_reason = reason

        if apply_cooldown:
            # Exponential backoff: 5h, 10h, 20h, max 24h
            hours = min(
                self.COOLDOWN_HOURS * (2 ** (stats.error_count - 1)),
                self.MAX_COOLDOWN_HOURS
            )
            stats.cooldown_until = int(time.time() * 1000) + (hours * 3600 * 1000)

        self.save()

    def clear_cooldown(self, profile_id: str) -> None:
        """Clear cooldown for a profile."""
        if profile_id in self.store.usage_stats:
            self.store.usage_stats[profile_id].cooldown_until = None
            self.store.usage_stats[profile_id].error_count = 0
            self.save()

    def resolve_auth(self, provider: str) -> Tuple[Optional[str], str]:
        """
        Resolve authentication for a provider.
        
        Returns:
            Tuple of (api_key, source_description)
        """
        # Try profiles first
        for pid, cred in self.store.profiles.items():
            if cred.provider == provider and self._is_profile_available(pid):
                key = self._extract_key(cred)
                if key:
                    return key, f"profile:{pid}"

        # Try environment
        key = self._get_env_key(provider)
        if key:
            env_vars = PROVIDER_ENV_VARS.get(provider, [f"{provider.upper()}_API_KEY"])
            for var in env_vars:
                if os.environ.get(var):
                    return key, f"env:{var}"
            return key, f"env:{provider.upper()}_API_KEY"

        return None, "not_found"

    def add_oauth_credentials(
        self,
        provider: str,
        access_token: str,
        refresh_token: Optional[str] = None,
        expires_at: Optional[int] = None,
        client_id: Optional[str] = None,
        account_id: Optional[str] = None,
        email: Optional[str] = None,
        profile_id: Optional[str] = None,
    ) -> str:
        """
        Add OAuth credentials.
        
        Args:
            provider: Provider name (e.g., "openai-codex")
            access_token: OAuth access token
            refresh_token: Optional refresh token
            expires_at: Expiry timestamp in milliseconds
            client_id: OAuth client ID used
            account_id: User account ID
            email: User email
            profile_id: Optional custom profile ID
        
        Returns:
            The profile ID
        """
        if profile_id is None:
            profile_id = f"{provider}:{email or account_id or 'default'}"

        self.store.profiles[profile_id] = OAuthCredential(
            provider=provider,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            client_id=client_id,
            email=email,
        )
        self.save()
        return profile_id

    def get_oauth_credential(
        self,
        provider: str,
        profile_id: Optional[str] = None,
    ) -> Optional[OAuthCredential]:
        """
        Get OAuth credentials for a provider.
        
        Args:
            provider: Provider name
            profile_id: Optional specific profile to use
        
        Returns:
            OAuth credential or None
        """
        if profile_id:
            cred = self.store.profiles.get(profile_id)
            if isinstance(cred, OAuthCredential):
                return cred
            return None

        # Find any OAuth credential for this provider
        for cred in self.store.profiles.values():
            if isinstance(cred, OAuthCredential) and cred.provider == provider:
                return cred

        return None

    def update_oauth_tokens(
        self,
        profile_id: str,
        access_token: str,
        refresh_token: Optional[str] = None,
        expires_at: Optional[int] = None,
    ) -> bool:
        """
        Update OAuth tokens after a refresh.
        
        Args:
            profile_id: Profile to update
            access_token: New access token
            refresh_token: Optional new refresh token
            expires_at: New expiry timestamp
        
        Returns:
            True if updated, False if profile not found
        """
        cred = self.store.profiles.get(profile_id)
        if not isinstance(cred, OAuthCredential):
            return False

        cred.access_token = access_token
        if refresh_token:
            cred.refresh_token = refresh_token
        if expires_at:
            cred.expires_at = expires_at

        self.save()
        return True

    def is_oauth_expired(self, profile_id: str, buffer_ms: int = 300000) -> bool:
        """
        Check if OAuth credentials are expired or will expire soon.
        
        Args:
            profile_id: Profile to check
            buffer_ms: Buffer time in milliseconds (default 5 minutes)
        
        Returns:
            True if expired or expiring soon
        """
        cred = self.store.profiles.get(profile_id)
        if not isinstance(cred, OAuthCredential):
            return True

        if not cred.expires_at:
            return False  # No expiry set, assume valid

        now_ms = int(time.time() * 1000)
        return now_ms >= cred.expires_at - buffer_ms
