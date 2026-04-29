"""
Tests for navig.vault.encryption
"""

import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def _make_ve(tmp_path: Path):
    """Return a VaultEncryption instance with keyring disabled."""
    from navig.vault.encryption import VaultEncryption

    ve = VaultEncryption(tmp_path)
    # Disable keyring so tests use the derived-key path
    ve._try_keyring = lambda: None  # type: ignore[assignment]
    return ve


# ---------------------------------------------------------------------------
# check_encryption_available
# ---------------------------------------------------------------------------

class TestCheckEncryptionAvailable:
    def test_returns_true_when_cryptography_installed(self):
        from navig.vault.encryption import check_encryption_available

        assert check_encryption_available() is True

    def test_returns_false_when_cryptography_unavailable(self):
        from navig.vault.encryption import check_encryption_available

        with patch("navig.vault.encryption.CRYPTOGRAPHY_AVAILABLE", False):
            assert check_encryption_available() is False


# ---------------------------------------------------------------------------
# VaultEncryption.__init__
# ---------------------------------------------------------------------------

class TestVaultEncryptionInit:
    def test_raises_import_error_when_cryptography_missing(self, tmp_path):
        with patch("navig.vault.encryption.CRYPTOGRAPHY_AVAILABLE", False):
            from navig.vault.encryption import VaultEncryption

            with pytest.raises(ImportError, match="cryptography"):
                VaultEncryption(tmp_path)

    def test_stores_vault_dir(self, tmp_path):
        from navig.vault.encryption import VaultEncryption

        ve = VaultEncryption(tmp_path)
        assert ve.vault_dir == tmp_path

    def test_fernet_is_none_initially(self, tmp_path):
        from navig.vault.encryption import VaultEncryption

        ve = VaultEncryption(tmp_path)
        assert ve._fernet is None


# ---------------------------------------------------------------------------
# _get_machine_id
# ---------------------------------------------------------------------------

class TestGetMachineId:
    def test_returns_non_empty_string(self, tmp_path):
        ve = _make_ve(tmp_path)
        mid = ve._get_machine_id()
        assert isinstance(mid, str)
        assert len(mid) > 0

    def test_contains_separator(self, tmp_path):
        ve = _make_ve(tmp_path)
        assert "-" in ve._get_machine_id()

    def test_includes_hostname(self, tmp_path):
        import socket

        ve = _make_ve(tmp_path)
        assert socket.gethostname() in ve._get_machine_id()


# ---------------------------------------------------------------------------
# _get_or_create_salt
# ---------------------------------------------------------------------------

class TestGetOrCreateSalt:
    def test_creates_salt_file_when_missing(self, tmp_path):
        ve = _make_ve(tmp_path)
        salt = ve._get_or_create_salt()
        assert (tmp_path / "vault.salt").exists()
        assert len(salt) == 16

    def test_returns_same_salt_on_second_call(self, tmp_path):
        ve = _make_ve(tmp_path)
        salt1 = ve._get_or_create_salt()
        salt2 = ve._get_or_create_salt()
        assert salt1 == salt2

    def test_reads_existing_salt_file(self, tmp_path):
        existing = b"\xde\xad" * 8  # 16 bytes
        (tmp_path / "vault.salt").write_bytes(existing)
        ve = _make_ve(tmp_path)
        assert ve._get_or_create_salt() == existing

    def test_creates_vault_dir_when_missing(self, tmp_path):
        sub = tmp_path / "new_dir"
        from navig.vault.encryption import VaultEncryption

        ve = VaultEncryption(sub)
        ve._try_keyring = lambda: None  # type: ignore[assignment]
        ve._get_or_create_salt()
        assert sub.exists()


# ---------------------------------------------------------------------------
# _derive_key
# ---------------------------------------------------------------------------

class TestDeriveKey:
    def test_returns_bytes(self, tmp_path):
        ve = _make_ve(tmp_path)
        salt = b"x" * 16
        key = ve._derive_key(salt)
        assert isinstance(key, bytes)

    def test_returns_valid_fernet_key_length(self, tmp_path):
        """Fernet key is 32-byte payload → 44 bytes base64url-encoded."""
        ve = _make_ve(tmp_path)
        salt = b"y" * 16
        key = ve._derive_key(salt)
        decoded = base64.urlsafe_b64decode(key)
        assert len(decoded) == 32

    def test_different_salts_produce_different_keys(self, tmp_path):
        ve = _make_ve(tmp_path)
        k1 = ve._derive_key(b"a" * 16)
        k2 = ve._derive_key(b"b" * 16)
        assert k1 != k2

    def test_same_salt_produces_same_key(self, tmp_path):
        ve = _make_ve(tmp_path)
        salt = b"z" * 16
        assert ve._derive_key(salt) == ve._derive_key(salt)


# ---------------------------------------------------------------------------
# _try_keyring
# ---------------------------------------------------------------------------

class TestTryKeyring:
    def test_returns_none_when_keyring_not_installed(self, tmp_path):
        from navig.vault.encryption import VaultEncryption

        ve = VaultEncryption(tmp_path)
        with patch.dict("sys.modules", {"keyring": None}):
            result = ve._try_keyring()
        assert result is None

    def test_returns_none_when_keyring_backend_raises(self, tmp_path):
        from navig.vault.encryption import VaultEncryption

        ve = VaultEncryption(tmp_path)
        mock_kr = MagicMock()
        mock_kr.get_password.side_effect = Exception("no backend")
        with patch.dict("sys.modules", {"keyring": mock_kr}):
            result = ve._try_keyring()
        assert result is None

    def test_returns_stored_key_as_bytes(self, tmp_path):
        from cryptography.fernet import Fernet
        from navig.vault.encryption import VaultEncryption

        ve = VaultEncryption(tmp_path)
        fake_key = Fernet.generate_key().decode()  # str stored in keyring
        mock_kr = MagicMock()
        mock_kr.get_password.return_value = fake_key
        with patch.dict("sys.modules", {"keyring": mock_kr}):
            result = ve._try_keyring()
        assert isinstance(result, bytes)
        assert result == fake_key.encode()


# ---------------------------------------------------------------------------
# fernet property (lazy initialisation)
# ---------------------------------------------------------------------------

class TestFernetProperty:
    def test_fernet_is_lazily_initialised(self, tmp_path):
        from cryptography.fernet import Fernet as _Fernet

        ve = _make_ve(tmp_path)
        assert ve._fernet is None
        f = ve.fernet
        assert isinstance(f, _Fernet)
        assert ve._fernet is f  # Cached

    def test_fernet_returns_same_instance_on_second_access(self, tmp_path):
        ve = _make_ve(tmp_path)
        assert ve.fernet is ve.fernet


# ---------------------------------------------------------------------------
# encrypt / decrypt round-trip
# ---------------------------------------------------------------------------

class TestEncryptDecrypt:
    def test_encrypt_returns_bytes(self, tmp_path):
        ve = _make_ve(tmp_path)
        result = ve.encrypt("hello")
        assert isinstance(result, bytes)

    def test_round_trip_simple_string(self, tmp_path):
        ve = _make_ve(tmp_path)
        assert ve.decrypt(ve.encrypt("hello")) == "hello"

    def test_round_trip_empty_string(self, tmp_path):
        ve = _make_ve(tmp_path)
        assert ve.decrypt(ve.encrypt("")) == ""

    def test_round_trip_unicode(self, tmp_path):
        text = "café ☕ navig"
        ve = _make_ve(tmp_path)
        assert ve.decrypt(ve.encrypt(text)) == text

    def test_ciphertext_differs_from_plaintext(self, tmp_path):
        ve = _make_ve(tmp_path)
        assert ve.encrypt("secret") != b"secret"

    def test_decrypt_garbage_raises_vault_encryption_error(self, tmp_path):
        from navig.vault.encryption import VaultEncryptionError

        ve = _make_ve(tmp_path)
        with pytest.raises(VaultEncryptionError):
            ve.decrypt(b"not_valid_fernet_token")

    def test_decrypt_with_wrong_key_raises_vault_encryption_error(self, tmp_path, tmp_path_factory):
        from navig.vault.encryption import VaultEncryptionError

        ve1 = _make_ve(tmp_path)
        token = ve1.encrypt("secret")

        # Different tmp_path → different salt → different key
        ve2 = _make_ve(tmp_path_factory.mktemp("other"))
        with pytest.raises(VaultEncryptionError):
            ve2.decrypt(token)


# ---------------------------------------------------------------------------
# rotate_key
# ---------------------------------------------------------------------------

class TestRotateKey:
    def test_raises_not_implemented(self, tmp_path):
        ve = _make_ve(tmp_path)
        with pytest.raises(NotImplementedError):
            ve.rotate_key()

    def test_raises_not_implemented_with_explicit_key(self, tmp_path):
        from cryptography.fernet import Fernet

        ve = _make_ve(tmp_path)
        with pytest.raises(NotImplementedError):
            ve.rotate_key(Fernet.generate_key())
