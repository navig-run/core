"""
NAVIG Vault Encryption Module

Provides Fernet symmetric encryption for credential data.
Master key is derived from OS keyring or machine-specific data.
"""

import base64
import os
import platform
import socket
from pathlib import Path
from typing import Optional

# Try to import cryptography, provide helpful error if missing
try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

    CRYPTOGRAPHY_AVAILABLE = True
except ImportError:
    CRYPTOGRAPHY_AVAILABLE = False
    Fernet = None  # type: ignore
    InvalidToken = Exception  # type: ignore


class VaultEncryptionError(Exception):
    """Raised when encryption/decryption fails."""

    pass


class VaultEncryption:
    """
    Handles vault encryption and decryption using Fernet.

    The master key is derived from:
    1. OS keyring (if available) - most secure
    2. Machine-specific data + salt file - fallback

    All credential data is encrypted before storage and decrypted
    on retrieval.
    """

    SALT_FILE = "vault.salt"
    KEYRING_SERVICE = "navig-vault"
    KEYRING_USERNAME = "master-key"

    def __init__(self, vault_dir: Path):
        """
        Initialize encryption manager.

        Args:
            vault_dir: Directory where vault files are stored
        """
        if not CRYPTOGRAPHY_AVAILABLE:
            raise ImportError(
                "cryptography package is required for vault encryption. "
                "Install it with: pip install cryptography"
            )

        self.vault_dir = vault_dir
        self._fernet: Optional[Fernet] = None

    def _get_machine_id(self) -> str:
        """
        Get a unique machine identifier.

        Combines several system properties to create a consistent
        machine fingerprint that survives reboots.
        """
        components = [
            platform.node(),  # Hostname
            socket.gethostname(),  # Network hostname
            platform.system(),  # OS name
            platform.machine(),  # CPU architecture
        ]

        # Try to get more stable identifiers on Windows
        if platform.system() == "Windows":
            try:
                import winreg

                key = winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Cryptography",
                )
                machine_guid, _ = winreg.QueryValueEx(key, "MachineGuid")
                components.append(machine_guid)
            except Exception:
                pass  # Not critical, use other components

        return "-".join(filter(None, components))

    def _get_or_create_salt(self) -> bytes:
        """Get or generate the encryption salt."""
        salt_path = self.vault_dir / self.SALT_FILE

        if salt_path.exists():
            return salt_path.read_bytes()

        # Generate new salt
        salt = os.urandom(16)

        # Ensure directory exists with proper permissions
        self.vault_dir.mkdir(parents=True, exist_ok=True)
        salt_path.write_bytes(salt)

        # Set restrictive permissions (Unix only)
        try:
            os.chmod(salt_path, 0o600)
        except OSError:
            pass  # best-effort cleanup

        return salt

    def _try_keyring(self) -> Optional[bytes]:
        """
        Try to get/set master key from OS keyring.

        Returns None if keyring is not available.
        The key is stored and returned as a complete Fernet key (base64-encoded).
        """
        try:
            import keyring

            # Try to get existing key
            stored_key = keyring.get_password(
                self.KEYRING_SERVICE, self.KEYRING_USERNAME
            )
            if stored_key:
                # The stored key is already a valid Fernet key (base64-encoded string)
                # Just convert it to bytes
                return stored_key.encode()

            # Generate and store new key
            new_key = Fernet.generate_key()
            keyring.set_password(
                self.KEYRING_SERVICE,
                self.KEYRING_USERNAME,
                new_key.decode(),  # Store as string
            )
            return new_key

        except ImportError:
            return None  # keyring not installed
        except Exception:
            return None  # keyring backend not available

    def _derive_key(self, salt: bytes) -> bytes:
        """
        Derive encryption key from machine ID and salt.

        Uses PBKDF2 with SHA256 and 480,000 iterations for
        strong key derivation.
        """
        machine_id = self._get_machine_id()

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=480000,  # OWASP recommended minimum for PBKDF2-SHA256
        )

        derived = kdf.derive(machine_id.encode())
        return base64.urlsafe_b64encode(derived)

    def _get_master_key(self) -> bytes:
        """
        Get the master encryption key.

        Tries OS keyring first, falls back to derived key.
        """
        # Try keyring first (most secure)
        keyring_key = self._try_keyring()
        if keyring_key:
            return keyring_key

        # Fall back to derived key
        salt = self._get_or_create_salt()
        return self._derive_key(salt)

    @property
    def fernet(self) -> Fernet:
        """
        Get the Fernet cipher instance.

        Lazily initializes on first access.
        """
        if self._fernet is None:
            key = self._get_master_key()
            self._fernet = Fernet(key)
        return self._fernet

    def encrypt(self, data: str) -> bytes:
        """
        Encrypt string data.

        Args:
            data: Plaintext string to encrypt

        Returns:
            Encrypted bytes (base64-encoded Fernet token)

        Raises:
            VaultEncryptionError: If encryption fails
        """
        try:
            return self.fernet.encrypt(data.encode("utf-8"))
        except Exception as e:
            raise VaultEncryptionError(f"Encryption failed: {e}") from e

    def decrypt(self, token: bytes) -> str:
        """
        Decrypt data back to string.

        Args:
            token: Encrypted Fernet token bytes

        Returns:
            Decrypted plaintext string

        Raises:
            VaultEncryptionError: If decryption fails (invalid key or corrupted data)
        """
        try:
            return self.fernet.decrypt(token).decode("utf-8")
        except InvalidToken as e:
            raise VaultEncryptionError(
                "Decryption failed: Invalid key or corrupted data. "
                "The vault may have been created on a different machine."
            ) from e
        except Exception as e:
            raise VaultEncryptionError(f"Decryption failed: {e}") from e

    def rotate_key(self, new_key: Optional[bytes] = None) -> None:
        """
        Rotate the encryption key.

        This will require re-encrypting all stored data with the new key.
        Use with caution!

        Args:
            new_key: Optional specific key to use (generates random if None)
        """
        # This is a placeholder for future key rotation support
        # Implementation would need to:
        # 1. Decrypt all data with old key
        # 2. Generate/set new key
        # 3. Re-encrypt all data with new key
        raise NotImplementedError(
            "Key rotation is not yet implemented. "
            "Please backup and recreate credentials."
        )


def check_encryption_available() -> bool:
    """Check if encryption is available."""
    return CRYPTOGRAPHY_AVAILABLE
