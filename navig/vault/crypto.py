"""NAVIG Vault Cryptographic Engine — AES-256-GCM + Argon2id.

Encryption : AES-256-GCM (authenticated, 128-bit tag, 12-byte nonce)
KDF        : Argon2id — RFC 9106 interactive profile (m=65536, t=3, p=4)
Fallback   : PBKDF2HMAC-SHA256 @ 600 000 iter when argon2-cffi not installed

Key hierarchy
-------------
Master Key  (Argon2id / PBKDF2 from passphrase or machine fingerprint)
  └─ Per-item DEK  (32-byte random, encrypted with master key via AES-GCM)
       └─ Item payload  (encrypted with DEK via AES-GCM)

Compromising one DEK exposes only that item.
"""

from __future__ import annotations

import os
import platform
import socket
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

try:
    from argon2.low_level import Type as _A2Type
    from argon2.low_level import hash_secret_raw  # type: ignore

    _HAS_ARGON2 = True
except ImportError:
    _HAS_ARGON2 = False

__all__ = ["CryptoEngine", "CryptoError"]

# ── Constants ────────────────────────────────────────────────────────────────
_NONCE_LEN = 12  # AES-GCM recommended nonce size
_KEY_LEN = 32  # AES-256
_SALT_LEN = 32  # KDF salt
_A2_TIME_COST = 3
_A2_MEMORY_COST = 65_536  # 64 MiB — RFC 9106 interactive
_A2_PARALLELISM = 4
_A2_SALT_PAD = 16  # argon2 requires salt ≥ 8; use 16 for alignment
_PBKDF2_ITERS = 600_000  # OWASP 2023 minimum for PBKDF2-HMAC-SHA256


class CryptoError(Exception):
    """Raised when any crypto operation fails (decryption, auth, bad data)."""


class CryptoEngine:
    """Low-level cryptographic primitives for the vault.

    One instance per vault directory.  Manages the per-vault salt file and
    provides all encrypt/decrypt primitives.

    Usage
    -----
    engine = CryptoEngine(vault_dir)
    master = engine.derive_key()          # machine-fingerprint mode
    master = engine.derive_key(b"pass")   # passphrase mode

    dek = CryptoEngine.generate_dek()
    blob = CryptoEngine.seal(dek, b"secret payload")
    payload = CryptoEngine.open(dek, blob)
    """

    SALT_FILE = "vault.salt"

    def __init__(self, vault_dir: Path) -> None:
        self.vault_dir = vault_dir
        self._salt: bytes | None = None

    # ── Salt management ──────────────────────────────────────────────────────

    def _get_or_create_salt(self) -> bytes:
        if self._salt is not None:
            return self._salt
        salt_path = self.vault_dir / self.SALT_FILE
        if salt_path.exists():
            data = salt_path.read_bytes()
            if len(data) < 16:
                raise CryptoError(
                    f"Salt file corrupt (only {len(data)} bytes): {salt_path}"
                )
            self._salt = data
        else:
            self._salt = os.urandom(_SALT_LEN)
            self.vault_dir.mkdir(parents=True, exist_ok=True)
            salt_path.write_bytes(self._salt)
            try:
                try:
                    salt_path.chmod(0o600)
                except (OSError, PermissionError):
                    pass
            except OSError:
                pass  # Windows — no-op
        return self._salt

    # ── Key derivation ────────────────────────────────────────────────────────

    def derive_key(self, passphrase: bytes | None = None) -> bytes:
        """Derive a 256-bit master key.

        Parameters
        ----------
        passphrase:
            Raw bytes of user passphrase.  If ``None``, derives from the
            machine fingerprint (non-interactive / daemon mode).

        Returns
        -------
        bytes
            32-byte master key suitable for AES-256-GCM.
        """
        salt = self._get_or_create_salt()
        material = passphrase if passphrase is not None else self._machine_fingerprint()
        return self._kdf(material, salt)

    def _kdf(self, material: bytes, salt: bytes) -> bytes:
        if _HAS_ARGON2:
            # argon2 requires salt len ≥ 8 bytes; we pad to 16
            padded_salt = (salt + b"\x00" * _A2_SALT_PAD)[:_A2_SALT_PAD]
            return hash_secret_raw(
                secret=material,
                salt=padded_salt,
                time_cost=_A2_TIME_COST,
                memory_cost=_A2_MEMORY_COST,
                parallelism=_A2_PARALLELISM,
                hash_len=_KEY_LEN,
                type=_A2Type.ID,
            )
        # Fallback: PBKDF2-HMAC-SHA256
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=_KEY_LEN,
            salt=salt,
            iterations=_PBKDF2_ITERS,
        )
        return kdf.derive(material)

    @staticmethod
    def _machine_fingerprint() -> bytes:
        """Derive a stable per-machine key material (no passphrase required)."""
        parts = [
            platform.node(),
            socket.gethostname(),
            platform.system(),
            platform.machine(),
        ]
        if platform.system() == "Windows":
            try:
                import winreg  # noqa: PLC0415

                key = winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Cryptography",
                )
                guid, _ = winreg.QueryValueEx(key, "MachineGuid")
                parts.append(str(guid))
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
        return "-".join(p for p in parts if p).encode()

    @staticmethod
    def kdf_info() -> str:
        """Human-readable description of the active KDF (for navig vault doctor)."""
        if _HAS_ARGON2:
            return (
                f"Argon2id  m={_A2_MEMORY_COST // 1024}MiB  "
                f"t={_A2_TIME_COST}  p={_A2_PARALLELISM}"
            )
        return f"PBKDF2-HMAC-SHA256  iter={_PBKDF2_ITERS:,}  (install argon2-cffi for Argon2id)"

    # ── AES-256-GCM primitives ────────────────────────────────────────────────

    @staticmethod
    def generate_dek() -> bytes:
        """Generate a cryptographically random 256-bit Data Encryption Key."""
        return os.urandom(_KEY_LEN)

    @staticmethod
    def encrypt(key: bytes, plaintext: bytes, aad: bytes = b"") -> tuple[bytes, bytes]:
        """Encrypt *plaintext* with AES-256-GCM.

        Parameters
        ----------
        key       : 32-byte encryption key
        plaintext : data to encrypt
        aad       : additional authenticated data (not encrypted, but verified)

        Returns
        -------
        (nonce, ciphertext_with_tag)
            12-byte nonce and ciphertext that includes the 16-byte auth tag.
        """
        nonce = os.urandom(_NONCE_LEN)
        ct = AESGCM(key).encrypt(nonce, plaintext, aad if aad else None)
        return nonce, ct

    @staticmethod
    def decrypt(key: bytes, nonce: bytes, ciphertext: bytes, aad: bytes = b"") -> bytes:
        """Decrypt AES-256-GCM ciphertext.

        Raises :class:`CryptoError` on authentication failure or wrong key.
        """
        try:
            return AESGCM(key).decrypt(nonce, ciphertext, aad if aad else None)
        except Exception as exc:
            raise CryptoError("Decryption failed — wrong key or tampered data") from exc

    @classmethod
    def seal(cls, key: bytes, plaintext: bytes, aad: bytes = b"") -> bytes:
        """Encrypt and return a self-contained blob: ``nonce ‖ ciphertext``.

        Use :meth:`open` to reverse.
        """
        nonce, ct = cls.encrypt(key, plaintext, aad)
        return nonce + ct

    @classmethod
    def open(cls, key: bytes, blob: bytes, aad: bytes = b"") -> bytes:
        """Decrypt a blob produced by :meth:`seal`.

        Raises :class:`CryptoError` if the blob is too short or auth fails.
        """
        min_len = _NONCE_LEN + 16  # nonce + tag minimum
        if len(blob) < min_len:
            raise CryptoError(
                f"Blob too short ({len(blob)} bytes) — minimum is {min_len}"
            )
        return cls.decrypt(key, blob[:_NONCE_LEN], blob[_NONCE_LEN:], aad)
