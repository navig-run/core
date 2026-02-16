"""
Tests for the NAVIG Credentials Vault.

Tests cover:
- SecretStr security wrapper
- Encryption roundtrip
- Storage CRUD operations
- Core vault API
- Provider validators (mocked)
- Migration from legacy format
"""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestSecretStr:
    """Tests for the SecretStr class."""

    def test_str_redacted(self):
        """String representation should be redacted."""
        from navig.vault.secret_str import SecretStr

        secret = SecretStr("super-secret-key")
        assert str(secret) == "***"

    def test_repr_redacted(self):
        """Repr should be redacted."""
        from navig.vault.secret_str import SecretStr

        secret = SecretStr("super-secret-key")
        assert repr(secret) == "SecretStr('***')"

    def test_format_redacted(self):
        """Format should be redacted."""
        from navig.vault.secret_str import SecretStr

        secret = SecretStr("super-secret-key")
        assert f"{secret}" == "***"
        assert f"Key: {secret}" == "Key: ***"

    def test_reveal(self):
        """Reveal should return actual value."""
        from navig.vault.secret_str import SecretStr

        secret = SecretStr("super-secret-key")
        assert secret.reveal() == "super-secret-key"

    def test_reveal_prefix(self):
        """Reveal prefix should show partial value."""
        from navig.vault.secret_str import SecretStr

        secret = SecretStr("sk-abcdef123456")
        assert secret.reveal_prefix(4) == "sk-a...***"
        assert secret.reveal_prefix(2) == "sk...***"

    def test_reveal_prefix_short_value(self):
        """Short values should be fully masked."""
        from navig.vault.secret_str import SecretStr

        secret = SecretStr("abc")
        assert secret.reveal_prefix(4) == "***"

    def test_equality(self):
        """Test equality comparison."""
        from navig.vault.secret_str import SecretStr

        s1 = SecretStr("secret")
        s2 = SecretStr("secret")
        s3 = SecretStr("other")

        assert s1 == s2
        assert s1 != s3
        assert s1 != "secret"  # Not equal to raw string

    def test_bool(self):
        """Test boolean conversion."""
        from navig.vault.secret_str import SecretStr

        assert bool(SecretStr("value"))
        assert not bool(SecretStr(""))

    def test_len(self):
        """Test length."""
        from navig.vault.secret_str import SecretStr

        assert len(SecretStr("12345")) == 5

    def test_from_env(self):
        """Test creating from environment variable."""
        from navig.vault.secret_str import SecretStr

        with patch.dict(os.environ, {"TEST_SECRET": "env-value"}):
            secret = SecretStr.from_env("TEST_SECRET")
            assert secret.reveal() == "env-value"

        # Test default
        secret = SecretStr.from_env("NONEXISTENT", "default-value")
        assert secret.reveal() == "default-value"


class TestTypes:
    """Tests for vault type definitions."""

    def test_credential_generate_id(self):
        """Credential IDs should be unique 8-char strings."""
        from navig.vault.types import Credential

        id1 = Credential.generate_id()
        id2 = Credential.generate_id()

        assert len(id1) == 8
        assert len(id2) == 8
        assert id1 != id2

    def test_credential_type_enum(self):
        """CredentialType should have expected values."""
        from navig.vault.types import CredentialType

        assert CredentialType.API_KEY.value == "api_key"
        assert CredentialType.OAUTH.value == "oauth"
        assert CredentialType.EMAIL.value == "email"

    def test_test_result_str(self):
        """TestResult should have nice string representation."""
        from navig.vault.types import TestResult

        success = TestResult(success=True, message="Valid key")
        fail = TestResult(success=False, message="Invalid")

        assert "✅" in str(success)
        assert "❌" in str(fail)


class TestEncryption:
    """Tests for vault encryption."""

    @pytest.fixture
    def temp_vault_dir(self):
        """Create temporary vault directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @patch("navig.vault.encryption.VaultEncryption._try_keyring", return_value=None)
    def test_encrypt_decrypt_roundtrip(self, mock_keyring, temp_vault_dir):
        """Encryption and decryption should preserve data."""
        from navig.vault.encryption import VaultEncryption

        enc = VaultEncryption(temp_vault_dir)

        original = "super-secret-api-key-12345"
        encrypted = enc.encrypt(original)
        decrypted = enc.decrypt(encrypted)

        assert decrypted == original
        assert encrypted != original.encode()

    @patch("navig.vault.encryption.VaultEncryption._try_keyring", return_value=None)
    def test_consistent_key_across_instances(self, mock_keyring, temp_vault_dir):
        """Same vault dir should produce consistent keys."""
        from navig.vault.encryption import VaultEncryption

        enc1 = VaultEncryption(temp_vault_dir)
        data = "test-data"
        encrypted = enc1.encrypt(data)

        # Create new instance with same dir
        enc2 = VaultEncryption(temp_vault_dir)
        decrypted = enc2.decrypt(encrypted)

        assert decrypted == data

    @patch("navig.vault.encryption.VaultEncryption._try_keyring", return_value=None)
    def test_salt_file_created(self, mock_keyring, temp_vault_dir):
        """Salt file should be created on first use."""
        from navig.vault.encryption import VaultEncryption

        enc = VaultEncryption(temp_vault_dir)
        enc.encrypt("test")  # Trigger key derivation

        salt_file = temp_vault_dir / "vault.salt"
        assert salt_file.exists()
        assert len(salt_file.read_bytes()) == 16


class TestStorage:
    """Tests for vault storage backend."""

    @pytest.fixture
    def storage(self):
        """Create storage with temp directory."""
        from navig.vault.encryption import VaultEncryption
        from navig.vault.storage import VaultStorage
        from navig.vault.types import Credential, CredentialType

        with tempfile.TemporaryDirectory() as tmpdir:
            vault_dir = Path(tmpdir)
            vault_path = vault_dir / "vault.db"
            enc = VaultEncryption(vault_dir)
            storage = VaultStorage(vault_path, enc)
            yield storage, Credential, CredentialType

    def test_save_and_get(self, storage):
        """Test save and retrieve credential."""
        storage, Credential, CredentialType = storage

        cred = Credential(
            id="test1234",
            provider="openai",
            profile_id="default",
            credential_type=CredentialType.API_KEY,
            label="Test OpenAI",
            data={"api_key": "sk-test-12345"},
        )

        storage.save(cred)
        retrieved = storage.get("test1234")

        assert retrieved is not None
        assert retrieved.provider == "openai"
        assert retrieved.data["api_key"] == "sk-test-12345"

    def test_list_all(self, storage):
        """Test listing credentials."""
        storage, Credential, CredentialType = storage

        # Add two credentials
        for i in range(2):
            cred = Credential(
                id=f"test{i}",
                provider="openai",
                profile_id=f"profile{i}",
                credential_type=CredentialType.API_KEY,
                label=f"Test {i}",
                data={"api_key": f"key{i}"},
            )
            storage.save(cred)

        infos = storage.list_all()
        assert len(infos) == 2

        # Filter by provider
        infos = storage.list_all(provider="openai")
        assert len(infos) == 2

        infos = storage.list_all(provider="anthropic")
        assert len(infos) == 0

    def test_delete(self, storage):
        """Test deleting credential."""
        storage, Credential, CredentialType = storage

        cred = Credential(
            id="todel",
            provider="test",
            profile_id="default",
            credential_type=CredentialType.API_KEY,
            label="To Delete",
            data={"key": "value"},
        )
        storage.save(cred)

        assert storage.get("todel") is not None
        assert storage.delete("todel")
        assert storage.get("todel") is None

    def test_enable_disable(self, storage):
        """Test enabling/disabling credentials."""
        storage, Credential, CredentialType = storage

        cred = Credential(
            id="toggle",
            provider="test",
            profile_id="default",
            credential_type=CredentialType.API_KEY,
            label="Toggle",
            data={"key": "value"},
            enabled=True,
        )
        storage.save(cred)

        storage.set_enabled("toggle", False)
        retrieved = storage.get("toggle")
        assert not retrieved.enabled

        storage.set_enabled("toggle", True)
        retrieved = storage.get("toggle")
        assert retrieved.enabled

    def test_audit_log(self, storage):
        """Test audit logging."""
        storage, Credential, CredentialType = storage

        storage.log_access("cred123", "created", "test")
        storage.log_access("cred123", "accessed", "test")

        logs = storage.get_audit_log("cred123")
        assert len(logs) == 2
        assert logs[0]["action"] == "accessed"  # Most recent first
        assert logs[1]["action"] == "created"


class TestCredentialsVault:
    """Tests for the main CredentialsVault class."""

    @pytest.fixture
    def vault(self):
        """Create vault with temp directory."""
        from navig.vault import CredentialsVault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = Path(tmpdir) / "vault.db"
            vault = CredentialsVault(vault_path=vault_path, auto_migrate=False)
            yield vault

    def test_add_and_get(self, vault):
        """Test adding and retrieving credentials."""
        cred_id = vault.add(
            provider="openai",
            credential_type="api_key",
            data={"api_key": "sk-test123"},
            profile_id="default",
            label="Test Key",
        )

        assert len(cred_id) == 8

        cred = vault.get("openai")
        assert cred is not None
        assert cred.data["api_key"] == "sk-test123"

    def test_get_secret(self, vault):
        """Test getting secrets as SecretStr."""
        from navig.vault import SecretStr

        vault.add(
            provider="anthropic",
            credential_type="api_key",
            data={"api_key": "sk-ant-123"},
        )

        secret = vault.get_secret("anthropic")
        assert secret is not None
        assert isinstance(secret, SecretStr)
        assert str(secret) == "***"
        assert secret.reveal() == "sk-ant-123"

    def test_get_api_key(self, vault):
        """Test get_api_key helper."""
        vault.add(
            provider="openrouter",
            credential_type="api_key",
            data={"api_key": "sk-or-test"},
        )

        api_key = vault.get_api_key("openrouter")
        assert api_key == "sk-or-test"

    def test_get_api_key_env_fallback(self, vault):
        """Test environment variable fallback."""
        with patch.dict(os.environ, {"GROQ_API_KEY": "env-key-123"}):
            api_key = vault.get_api_key("groq")
            assert api_key == "env-key-123"

    def test_profile_management(self, vault):
        """Test profile switching."""
        # Add credentials to different profiles
        vault.add(
            provider="openai",
            credential_type="api_key",
            data={"api_key": "default-key"},
            profile_id="default",
        )
        vault.add(
            provider="openai",
            credential_type="api_key",
            data={"api_key": "work-key"},
            profile_id="work",
        )

        # Default profile
        assert vault.get_api_key("openai") == "default-key"

        # Switch profile
        vault.set_active_profile("work")
        assert vault.get_active_profile() == "work"
        assert vault.get_api_key("openai") == "work-key"

    def test_list_credentials(self, vault):
        """Test listing credentials."""
        vault.add(provider="openai", credential_type="api_key", data={"api_key": "k1"})
        vault.add(provider="anthropic", credential_type="api_key", data={"api_key": "k2"})

        creds = vault.list()
        assert len(creds) == 2

        creds = vault.list(provider="openai")
        assert len(creds) == 1

    def test_update_credential(self, vault):
        """Test updating credential data."""
        cred_id = vault.add(
            provider="test",
            credential_type="api_key",
            data={"api_key": "old-key"},
            label="Old Label",
        )

        success = vault.update(
            cred_id,
            data={"api_key": "new-key"},
            label="New Label",
        )

        assert success
        cred = vault.get_by_id(cred_id)
        assert cred.data["api_key"] == "new-key"
        assert cred.label == "New Label"

    def test_disable_enable(self, vault):
        """Test disabling and re-enabling credentials."""
        cred_id = vault.add(
            provider="test",
            credential_type="api_key",
            data={"api_key": "key123"},
        )

        # Disable
        assert vault.disable(cred_id)

        # Disabled credential should not be returned by get
        cred = vault.get("test")
        assert cred is None

        # Re-enable
        assert vault.enable(cred_id)
        cred = vault.get("test")
        assert cred is not None

    def test_clone(self, vault):
        """Test cloning credential to new profile."""
        orig_id = vault.add(
            provider="openai",
            credential_type="api_key",
            data={"api_key": "shared-key"},
            profile_id="personal",
            label="Personal OpenAI",
        )

        new_id = vault.clone(orig_id, "work", "Work OpenAI")

        assert new_id is not None
        assert new_id != orig_id

        work_cred = vault.get("openai", profile_id="work")
        assert work_cred is not None
        assert work_cred.data["api_key"] == "shared-key"
        assert work_cred.label == "Work OpenAI"

    def test_delete(self, vault):
        """Test deleting credential."""
        cred_id = vault.add(
            provider="test",
            credential_type="api_key",
            data={"api_key": "delete-me"},
        )

        assert vault.delete(cred_id)
        assert vault.get("test") is None

    def test_count(self, vault):
        """Test counting credentials."""
        assert vault.count() == 0

        vault.add(provider="a", credential_type="api_key", data={"api_key": "k"})
        vault.add(provider="b", credential_type="api_key", data={"api_key": "k"})

        assert vault.count() == 2
        assert vault.count(provider="a") == 1

    def test_audit_log(self, vault):
        """Test audit log retrieval."""
        cred_id = vault.add(
            provider="test",
            credential_type="api_key",
            data={"api_key": "key"},
        )

        vault.get("test", caller="my_function")

        logs = vault.get_audit_log(cred_id)
        assert len(logs) >= 2  # created + accessed
        actions = [log["action"] for log in logs]
        assert "created" in actions
        assert "accessed" in actions


class TestValidators:
    """Tests for credential validators."""

    def test_get_validator(self):
        """Test validator registry."""
        from navig.vault.validators import get_validator, GenericValidator, OpenAIValidator

        validator = get_validator("openai")
        assert isinstance(validator, OpenAIValidator)

        validator = get_validator("unknown-provider")
        assert isinstance(validator, GenericValidator)

    def test_generic_validator(self):
        """Test generic validator behavior."""
        from navig.vault.types import Credential, CredentialType
        from navig.vault.validators import GenericValidator

        validator = GenericValidator()

        # With data
        cred = Credential(
            id="test",
            provider="unknown",
            profile_id="default",
            credential_type=CredentialType.API_KEY,
            label="Test",
            data={"api_key": "some-key"},
        )
        result = validator.validate(cred)
        assert result.success

        # Without data
        empty_cred = Credential(
            id="test",
            provider="unknown",
            profile_id="default",
            credential_type=CredentialType.API_KEY,
            label="Test",
            data={},
        )
        result = validator.validate(empty_cred)
        assert not result.success

    @patch("httpx.get")
    def test_openai_validator_success(self, mock_get):
        """Test OpenAI validator with mocked success response."""
        from navig.vault.types import Credential, CredentialType
        from navig.vault.validators import OpenAIValidator

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"data": [{"id": "gpt-4"}, {"id": "gpt-3.5-turbo"}]},
        )

        validator = OpenAIValidator()
        cred = Credential(
            id="test",
            provider="openai",
            profile_id="default",
            credential_type=CredentialType.API_KEY,
            label="Test",
            data={"api_key": "sk-valid-key"},
        )

        result = validator.validate(cred)
        assert result.success
        assert result.details["models_available"] == 2

    @patch("httpx.get")
    def test_openai_validator_invalid_key(self, mock_get):
        """Test OpenAI validator with invalid key."""
        from navig.vault.types import Credential, CredentialType
        from navig.vault.validators import OpenAIValidator

        mock_get.return_value = MagicMock(status_code=401)

        validator = OpenAIValidator()
        cred = Credential(
            id="test",
            provider="openai",
            profile_id="default",
            credential_type=CredentialType.API_KEY,
            label="Test",
            data={"api_key": "invalid-key"},
        )

        result = validator.validate(cred)
        assert not result.success
        assert "Invalid" in result.message


class TestSecretStrEdgeCases:
    """Additional SecretStr tests for coverage."""

    def test_type_error_on_non_string(self):
        """SecretStr should reject non-string input."""
        from navig.vault.secret_str import SecretStr

        with pytest.raises(TypeError, match="expects str"):
            SecretStr(12345)

    def test_hash(self):
        """Test hash consistency."""
        from navig.vault.secret_str import SecretStr

        s1 = SecretStr("test")
        s2 = SecretStr("test")
        s3 = SecretStr("other")

        assert hash(s1) == hash(s2)
        assert hash(s1) != hash(s3)

        # Should work as dict key
        d = {s1: "found"}
        assert d[s2] == "found"

    def test_copy(self):
        """Test copy method."""
        from navig.vault.secret_str import SecretStr

        s1 = SecretStr("secret-value")
        s2 = s1.copy()

        assert s1 == s2
        assert s1 is not s2
        assert s2.reveal() == "secret-value"


class TestMaskSecret:
    """Tests for the mask_secret utility function."""

    def test_mask_none(self):
        from navig.vault.secret_str import mask_secret

        assert mask_secret(None) == "<none>"

    def test_mask_empty(self):
        from navig.vault.secret_str import mask_secret

        assert mask_secret("") == "<empty>"

    def test_mask_with_prefix(self):
        from navig.vault.secret_str import mask_secret

        assert mask_secret("sk-abcdef123456", 4) == "sk-a...***"

    def test_mask_no_prefix(self):
        from navig.vault.secret_str import mask_secret

        assert mask_secret("abcdef", 0) == "***"

    def test_mask_short_value(self):
        from navig.vault.secret_str import mask_secret

        assert mask_secret("abc", 10) == "***"

    def test_mask_secret_str_input(self):
        from navig.vault.secret_str import SecretStr, mask_secret

        secret = SecretStr("sk-abcdef123456")
        result = mask_secret(secret, 4)
        assert result == "sk-a...***"

    def test_mask_secret_str_no_prefix(self):
        from navig.vault.secret_str import SecretStr, mask_secret

        secret = SecretStr("sk-abcdef")
        assert mask_secret(secret, 0) == "***"


class TestEncryptionEdgeCases:
    """Additional encryption tests for coverage."""

    @pytest.fixture
    def temp_vault_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @patch("navig.vault.encryption.VaultEncryption._try_keyring", return_value=None)
    def test_decrypt_wrong_key_raises(self, mock_keyring, temp_vault_dir):
        """Decrypting with wrong key should raise VaultEncryptionError."""
        from navig.vault.encryption import VaultEncryption, VaultEncryptionError

        enc1 = VaultEncryption(temp_vault_dir)
        encrypted = enc1.encrypt("secret-data")

        # Create second instance with different salt (simulates different machine)
        with tempfile.TemporaryDirectory() as tmpdir2:
            enc2 = VaultEncryption(Path(tmpdir2))
            with pytest.raises(VaultEncryptionError, match="Decryption failed"):
                enc2.decrypt(encrypted)

    @patch("navig.vault.encryption.VaultEncryption._try_keyring", return_value=None)
    def test_rotate_key_not_implemented(self, mock_keyring, temp_vault_dir):
        """rotate_key should raise NotImplementedError."""
        from navig.vault.encryption import VaultEncryption

        enc = VaultEncryption(temp_vault_dir)
        with pytest.raises(NotImplementedError, match="not yet implemented"):
            enc.rotate_key()

    def test_check_encryption_available(self):
        """check_encryption_available should return True when cryptography is installed."""
        from navig.vault.encryption import check_encryption_available

        assert check_encryption_available() is True

    @patch("navig.vault.encryption.VaultEncryption._try_keyring", return_value=None)
    def test_encrypt_empty_string(self, mock_keyring, temp_vault_dir):
        """Encrypting empty string should roundtrip correctly."""
        from navig.vault.encryption import VaultEncryption

        enc = VaultEncryption(temp_vault_dir)
        encrypted = enc.encrypt("")
        assert enc.decrypt(encrypted) == ""

    @patch("navig.vault.encryption.VaultEncryption._try_keyring", return_value=None)
    def test_encrypt_unicode(self, mock_keyring, temp_vault_dir):
        """Encrypting unicode should roundtrip correctly."""
        from navig.vault.encryption import VaultEncryption

        enc = VaultEncryption(temp_vault_dir)
        text = "héllo wörld ñ 日本語"
        encrypted = enc.encrypt(text)
        assert enc.decrypt(encrypted) == text


class TestTypesEdgeCases:
    """Additional type tests for coverage."""

    def test_credential_get_secret(self):
        """Test Credential.get_secret helper."""
        from navig.vault.types import Credential, CredentialType

        cred = Credential(
            id="test",
            provider="openai",
            profile_id="default",
            credential_type=CredentialType.API_KEY,
            label="Test",
            data={"api_key": "sk-123", "org_id": "org-456"},
        )

        assert cred.get_secret("api_key") == "sk-123"
        assert cred.get_secret("org_id") == "org-456"
        assert cred.get_secret("missing") is None

    def test_credential_info_str(self):
        """Test CredentialInfo string representation."""
        from navig.vault.types import CredentialInfo, CredentialType

        info = CredentialInfo(
            id="abc12345",
            provider="openai",
            profile_id="default",
            credential_type=CredentialType.API_KEY,
            label="My Key",
            enabled=True,
            created_at=datetime(2024, 1, 1),
            last_used_at=None,
        )
        s = str(info)
        assert "✓" in s
        assert "openai" in s

        disabled = CredentialInfo(
            id="xyz",
            provider="test",
            profile_id="work",
            credential_type=CredentialType.TOKEN,
            label="Disabled",
            enabled=False,
            created_at=datetime(2024, 1, 1),
            last_used_at=None,
        )
        assert "✗" in str(disabled)


class TestVaultTestMethods:
    """Tests for vault test/test_provider methods."""

    @pytest.fixture
    def vault(self):
        from navig.vault import CredentialsVault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = Path(tmpdir) / "vault.db"
            vault = CredentialsVault(vault_path=vault_path, auto_migrate=False)
            yield vault

    def test_test_nonexistent(self, vault):
        """Testing nonexistent credential should return failure."""
        result = vault.test("nonexistent")
        assert not result.success
        assert "not found" in result.message

    @patch("httpx.get")
    def test_test_provider_success(self, mock_get, vault):
        """Test vaccine.test_provider() with mocked validator."""
        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"data": [{"id": "gpt-4"}]},
        )

        vault.add(
            provider="openai",
            credential_type="api_key",
            data={"api_key": "sk-test"},
        )

        result = vault.test_provider("openai")
        assert result.success

    def test_test_provider_no_credential(self, vault):
        """test_provider with no matching credential should fail."""
        result = vault.test_provider("nonexistent")
        assert not result.success
        assert "No credential found" in result.message


class TestVaultEnvFallback:
    """Tests for environment variable fallback in get_api_key."""

    @pytest.fixture
    def vault(self):
        from navig.vault import CredentialsVault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault_path = Path(tmpdir) / "vault.db"
            vault = CredentialsVault(vault_path=vault_path, auto_migrate=False)
            yield vault

    def test_token_type_fallback(self, vault):
        """get_api_key should try 'token' key for token-type credentials."""
        vault.add(
            provider="github",
            credential_type="token",
            data={"token": "ghp_test123"},
        )

        api_key = vault.get_api_key("github")
        assert api_key == "ghp_test123"

    def test_env_var_fallback_standard_pattern(self, vault):
        """get_api_key should fall back to PROVIDER_API_KEY env var."""
        with patch.dict(os.environ, {"CUSTOM_PROVIDER_API_KEY": "env-key-xyz"}):
            key = vault.get_api_key("custom_provider")
            assert key == "env-key-xyz"

    def test_no_credential_no_env(self, vault):
        """get_api_key should return None when nothing found."""
        result = vault.get_api_key("nonexistent_provider_xyz")
        assert result is None


class TestValidatorsMocked:
    """Tests for all provider validators with mocked HTTP calls."""

    def _make_cred(self, provider, data=None, metadata=None):
        from navig.vault.types import Credential, CredentialType

        return Credential(
            id="test",
            provider=provider,
            profile_id="default",
            credential_type=CredentialType.API_KEY,
            label="Test",
            data=data or {},
            metadata=metadata or {},
        )

    @patch("httpx.post")
    def test_anthropic_validator_success(self, mock_post):
        from navig.vault.validators import AnthropicValidator

        mock_post.return_value = MagicMock(status_code=200)
        cred = self._make_cred("anthropic", {"api_key": "sk-ant-test"})
        result = AnthropicValidator().validate(cred)
        assert result.success

    @patch("httpx.post")
    def test_anthropic_validator_invalid(self, mock_post):
        from navig.vault.validators import AnthropicValidator

        mock_post.return_value = MagicMock(status_code=401)
        cred = self._make_cred("anthropic", {"api_key": "invalid"})
        result = AnthropicValidator().validate(cred)
        assert not result.success
        assert "Invalid" in result.message

    @patch("httpx.post")
    def test_anthropic_validator_rate_limited(self, mock_post):
        from navig.vault.validators import AnthropicValidator

        mock_post.return_value = MagicMock(status_code=429)
        cred = self._make_cred("anthropic", {"api_key": "sk-ant-test"})
        result = AnthropicValidator().validate(cred)
        assert not result.success
        assert "Rate limited" in result.message

    @patch("httpx.post")
    def test_anthropic_validator_other_error(self, mock_post):
        from navig.vault.validators import AnthropicValidator

        mock_post.return_value = MagicMock(
            status_code=500,
            json=lambda: {"error": {"message": "Server error"}},
        )
        cred = self._make_cred("anthropic", {"api_key": "sk-ant-test"})
        result = AnthropicValidator().validate(cred)
        assert not result.success
        assert "500" in result.message

    @patch("httpx.post")
    def test_anthropic_validator_connection_error(self, mock_post):
        from navig.vault.validators import AnthropicValidator

        mock_post.side_effect = ConnectionError("timeout")
        cred = self._make_cred("anthropic", {"api_key": "sk-ant-test"})
        result = AnthropicValidator().validate(cred)
        assert not result.success
        assert "Connection error" in result.message

    def test_anthropic_validator_empty_key(self):
        from navig.vault.validators import AnthropicValidator

        cred = self._make_cred("anthropic", {"api_key": ""})
        result = AnthropicValidator().validate(cred)
        assert not result.success
        assert "empty" in result.message

    @patch("httpx.get")
    def test_openrouter_validator_success(self, mock_get):
        from navig.vault.validators import OpenRouterValidator

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"data": {"usage": 1.5, "limit": 100, "is_free_tier": False}},
        )
        cred = self._make_cred("openrouter", {"api_key": "sk-or-test"})
        result = OpenRouterValidator().validate(cred)
        assert result.success
        assert result.details["usage_usd"] == 1.5

    @patch("httpx.get")
    def test_openrouter_validator_invalid(self, mock_get):
        from navig.vault.validators import OpenRouterValidator

        mock_get.return_value = MagicMock(status_code=401)
        cred = self._make_cred("openrouter", {"api_key": "invalid"})
        result = OpenRouterValidator().validate(cred)
        assert not result.success

    def test_openrouter_validator_empty(self):
        from navig.vault.validators import OpenRouterValidator

        cred = self._make_cred("openrouter", {"api_key": ""})
        result = OpenRouterValidator().validate(cred)
        assert not result.success

    @patch("httpx.get")
    def test_groq_validator_success(self, mock_get):
        from navig.vault.validators import GroqValidator

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"data": [{"id": "llama3"}]},
        )
        cred = self._make_cred("groq", {"api_key": "gsk_test"})
        result = GroqValidator().validate(cred)
        assert result.success

    @patch("httpx.get")
    def test_groq_validator_invalid(self, mock_get):
        from navig.vault.validators import GroqValidator

        mock_get.return_value = MagicMock(status_code=401)
        cred = self._make_cred("groq", {"api_key": "invalid"})
        result = GroqValidator().validate(cred)
        assert not result.success

    def test_groq_validator_empty(self):
        from navig.vault.validators import GroqValidator

        cred = self._make_cred("groq", {"api_key": ""})
        result = GroqValidator().validate(cred)
        assert not result.success

    @patch("httpx.get")
    def test_github_validator_success(self, mock_get):
        from navig.vault.validators import GitHubValidator

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"login": "user", "name": "Test User", "email": "t@t.com"},
        )
        cred = self._make_cred("github", {"token": "ghp_test123"})
        result = GitHubValidator().validate(cred)
        assert result.success
        assert result.details["login"] == "user"

    @patch("httpx.get")
    def test_github_validator_invalid(self, mock_get):
        from navig.vault.validators import GitHubValidator

        mock_get.return_value = MagicMock(status_code=401)
        cred = self._make_cred("github", {"token": "invalid"})
        result = GitHubValidator().validate(cred)
        assert not result.success

    @patch("httpx.get")
    def test_github_validator_forbidden(self, mock_get):
        from navig.vault.validators import GitHubValidator

        mock_get.return_value = MagicMock(status_code=403)
        cred = self._make_cred("github", {"token": "ghp_test"})
        result = GitHubValidator().validate(cred)
        assert not result.success
        assert "permissions" in result.message

    def test_github_validator_empty(self):
        from navig.vault.validators import GitHubValidator

        cred = self._make_cred("github", {})
        result = GitHubValidator().validate(cred)
        assert not result.success
        assert "empty" in result.message

    @patch("httpx.get")
    def test_gitlab_validator_success(self, mock_get):
        from navig.vault.validators import GitLabValidator

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"username": "user", "name": "Test", "email": "t@t.com"},
        )
        cred = self._make_cred("gitlab", {"token": "glpat-test"}, {"base_url": "https://gitlab.com"})
        result = GitLabValidator().validate(cred)
        assert result.success

    @patch("httpx.get")
    def test_gitlab_validator_invalid(self, mock_get):
        from navig.vault.validators import GitLabValidator

        mock_get.return_value = MagicMock(status_code=401)
        cred = self._make_cred("gitlab", {"token": "invalid"})
        result = GitLabValidator().validate(cred)
        assert not result.success

    def test_gitlab_validator_empty(self):
        from navig.vault.validators import GitLabValidator

        cred = self._make_cred("gitlab", {})
        result = GitLabValidator().validate(cred)
        assert not result.success

    def test_jira_validator_missing_fields(self):
        from navig.vault.validators import JiraValidator

        # Missing token
        cred = self._make_cred("jira", {}, {"email": "a@b.com", "base_url": "https://x.atlassian.net"})
        result = JiraValidator().validate(cred)
        assert not result.success
        assert "token" in result.message.lower() or "empty" in result.message.lower()

        # Missing email
        cred = self._make_cred("jira", {"api_key": "tok"}, {"base_url": "https://x.atlassian.net"})
        result = JiraValidator().validate(cred)
        assert not result.success
        assert "Email" in result.message

        # Missing base_url
        cred = self._make_cred("jira", {"api_key": "tok"}, {"email": "a@b.com"})
        result = JiraValidator().validate(cred)
        assert not result.success
        assert "base URL" in result.message

    @patch("httpx.get")
    def test_jira_validator_success(self, mock_get):
        from navig.vault.validators import JiraValidator

        mock_get.return_value = MagicMock(
            status_code=200,
            json=lambda: {"displayName": "User", "emailAddress": "a@b.com", "accountType": "atlassian"},
        )
        cred = self._make_cred(
            "jira",
            {"api_key": "tok"},
            {"email": "a@b.com", "base_url": "https://x.atlassian.net"},
        )
        result = JiraValidator().validate(cred)
        assert result.success

    @patch("httpx.get")
    def test_jira_validator_invalid(self, mock_get):
        from navig.vault.validators import JiraValidator

        mock_get.return_value = MagicMock(status_code=401)
        cred = self._make_cred(
            "jira",
            {"api_key": "tok"},
            {"email": "a@b.com", "base_url": "https://x.atlassian.net"},
        )
        result = JiraValidator().validate(cred)
        assert not result.success

    def test_email_validator_missing_fields(self):
        from navig.vault.validators import EmailValidator

        # Missing email
        cred = self._make_cred("gmail", {"password": "pass"}, {})
        result = EmailValidator().validate(cred)
        assert not result.success
        assert "Email" in result.message or "empty" in result.message

        # Missing password
        cred = self._make_cred("gmail", {}, {"email": "a@b.com"})
        result = EmailValidator().validate(cred)
        assert not result.success
        assert "Password" in result.message or "empty" in result.message

    def test_list_supported_validators(self):
        from navig.vault.validators import list_supported_validators

        providers = list_supported_validators()
        assert "openai" in providers
        assert "anthropic" in providers
        assert "github" in providers
        assert "gmail" in providers
        assert isinstance(providers, list)
        assert providers == sorted(providers)  # Should be sorted


class TestStorageEdgeCases:
    """Additional storage tests for coverage."""

    @pytest.fixture
    def storage(self):
        from navig.vault.encryption import VaultEncryption
        from navig.vault.storage import VaultStorage

        with tempfile.TemporaryDirectory() as tmpdir:
            vault_dir = Path(tmpdir)
            vault_path = vault_dir / "vault.db"
            enc = VaultEncryption(vault_dir)
            storage = VaultStorage(vault_path, enc)
            yield storage

    @patch("navig.vault.encryption.VaultEncryption._try_keyring", return_value=None)
    def test_count(self, mock_kr, storage):
        """Test count method."""
        from navig.vault.types import Credential, CredentialType

        assert storage.count() == 0

        cred = Credential(
            id="cnt1",
            provider="openai",
            profile_id="default",
            credential_type=CredentialType.API_KEY,
            label="Test",
            data={"api_key": "k1"},
        )
        storage.save(cred)

        assert storage.count() == 1
        assert storage.count(provider="openai") == 1
        assert storage.count(provider="other") == 0

    @patch("navig.vault.encryption.VaultEncryption._try_keyring", return_value=None)
    def test_get_nonexistent(self, mock_kr, storage):
        """Getting nonexistent credential returns None."""
        assert storage.get("nosuchid") is None

    @patch("navig.vault.encryption.VaultEncryption._try_keyring", return_value=None)
    def test_delete_nonexistent(self, mock_kr, storage):
        """Deleting nonexistent credential returns False."""
        assert storage.delete("nosuchid") is False

    @patch("navig.vault.encryption.VaultEncryption._try_keyring", return_value=None)
    def test_set_enabled_nonexistent(self, mock_kr, storage):
        """Enabling nonexistent credential returns False."""
        assert storage.set_enabled("nosuchid", True) is False

    @patch("navig.vault.encryption.VaultEncryption._try_keyring", return_value=None)
    def test_get_by_provider_profile(self, mock_kr, storage):
        """Test get_by_provider_profile."""
        from navig.vault.types import Credential, CredentialType

        cred = Credential(
            id="pp1",
            provider="anthropic",
            profile_id="work",
            credential_type=CredentialType.API_KEY,
            label="Work Anthropic",
            data={"api_key": "sk-ant"},
        )
        storage.save(cred)

        found = storage.get_by_provider_profile("anthropic", "work")
        assert found is not None
        assert found.id == "pp1"

        not_found = storage.get_by_provider_profile("openai", "work")
        assert not_found is None


class TestMigration:
    """Tests for legacy auth-profiles.json migration."""

    def test_migrate_auth_profiles(self):
        """Test migration from auth-profiles.json."""
        from navig.vault import CredentialsVault

        with tempfile.TemporaryDirectory() as tmpdir:
            vault_dir = Path(tmpdir)
            legacy_file = vault_dir / "auth-profiles.json"

            # Create legacy file
            legacy_data = {
                "version": 1,
                "profiles": {
                    "openai": {
                        "type": "api_key",
                        "provider": "openai",
                        "key": "sk-legacy-key",
                        "email": "user@example.com",
                    },
                    "openai:work": {
                        "type": "api_key",
                        "provider": "openai",
                        "key": "sk-work-key",
                    },
                },
            }
            legacy_file.write_text(json.dumps(legacy_data))

            # Create vault (should trigger migration)
            vault_path = vault_dir / "vault.db"
            vault = CredentialsVault(vault_path=vault_path, auto_migrate=True)

            # Verify migration
            creds = vault.list(provider="openai")
            assert len(creds) == 2

            # Check data preserved
            cred = vault.get("openai", profile_id="openai")
            assert cred.data["api_key"] == "sk-legacy-key"

            # Legacy file should be renamed
            assert not legacy_file.exists()
            assert (vault_dir / "auth-profiles.json.migrated").exists()
