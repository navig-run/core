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

from navig.core.file_permissions import set_owner_only_file_permissions

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# argon2-cffi is probed lazily on the first KDF call rather than at module
# import time.  On some platforms (Windows / Python 3.14) the argon2-cffi C
# extension blocks indefinitely during DLL loading, which hangs the entire
# navig.vault import chain and causes pytest collection to stall.
_argon2_probed: bool = False
_argon2_funcs: tuple | None = None  # (Type, hash_secret_raw) when argon2 is available

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
                raise CryptoError(f"Salt file corrupt (only {len(data)} bytes): {salt_path}")
            self._salt = data
        else:
            self._salt = os.urandom(_SALT_LEN)
            self.vault_dir.mkdir(parents=True, exist_ok=True)
            salt_path.write_bytes(self._salt)
            set_owner_only_file_permissions(salt_path)
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
        global _argon2_probed, _argon2_funcs
        if not _argon2_probed:
            _argon2_probed = True
            try:
                from argon2.low_level import Type as _A2T  # noqa: PLC0415
                from argon2.low_level import hash_secret_raw as _hsr
                _argon2_funcs = (_A2T, _hsr)
            except Exception:  # noqa: BLE001  — catches ImportError and DLL load failures
                _argon2_funcs = None
        if _argon2_funcs is not None:
            _A2Type, hash_secret_raw = _argon2_funcs
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
    def _os_arch() -> str:
        """Return the hardware architecture, stable across 32-bit and 64-bit Python.

        On POSIX, ``platform.machine()`` reflects the *Python binary's* bitness,
        not the kernel's.  A 32-bit Python on a 64-bit Linux kernel reports
        ``i686`` while a 64-bit Python reports ``x86_64``.  On Apple Silicon,
        Rosetta-translated x86_64 Python reports ``x86_64`` while native ARM
        Python reports ``arm64``.  In both cases ``os.uname().machine`` returns
        the kernel's view (``x86_64`` / ``arm64``) regardless of Python bitness.

        On Windows, ``platform.machine()`` already returns the CPU architecture
        (``AMD64``) for both 32-bit and 64-bit Python, so no correction is needed.
        """
        if platform.system() != "Windows":
            try:
                return os.uname().machine  # pylint: disable=no-member
            except AttributeError:
                pass  # should not happen on any POSIX platform; fall through
        return platform.machine()

    @staticmethod
    def _stable_machine_uuid() -> str | None:
        """Return a stable OS-level machine UUID, or ``None`` if unavailable.

        Sources per platform:

        - **Linux**   : ``/etc/machine-id`` (systemd) or
          ``/var/lib/dbus/machine-id`` — a file read, no subprocess needed.
        - **macOS**   : ``IOPlatformUUID`` from ``ioreg -rd1 -c
          IOPlatformExpertDevice`` — survives OS upgrades, unique per logic-board.
        - **Windows** : ``MachineGuid`` from
          ``HKLM\\SOFTWARE\\Microsoft\\Cryptography`` opened with
          ``KEY_WOW64_64KEY`` so that 32-bit Python on a 64-bit OS bypasses
          WOW64 registry redirection and reads the same value as 64-bit Python.

        All errors are silently swallowed — the UUID is *extra* fingerprint
        entropy and the vault remains functional without it.
        """
        system = platform.system()
        try:
            if system == "Linux":
                for _mid in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
                    _p = Path(_mid)
                    if _p.exists():
                        uid = _p.read_text(encoding="utf-8").strip()
                        if uid:
                            return uid
            elif system == "Darwin":
                import subprocess  # noqa: PLC0415

                result = subprocess.run(  # noqa: S603,S607
                    ["ioreg", "-rd1", "-c", "IOPlatformExpertDevice"],
                    capture_output=True,
                    text=True,
                    timeout=3,
                )
                for line in result.stdout.splitlines():
                    if "IOPlatformUUID" in line:
                        _parts = line.split("=", 1)
                        if len(_parts) == 2:
                            uid = _parts[1].strip().strip('"')
                            if uid:
                                return uid
            elif system == "Windows":
                import winreg  # noqa: PLC0415

                # KEY_WOW64_64KEY forces 32-bit Python to bypass WOW64
                # redirection so it reads the same path as 64-bit Python.
                key = winreg.OpenKey(
                    winreg.HKEY_LOCAL_MACHINE,
                    r"SOFTWARE\Microsoft\Cryptography",
                    0,
                    winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
                )
                guid, _ = winreg.QueryValueEx(key, "MachineGuid")
                if guid:
                    return str(guid)
        except Exception:  # noqa: BLE001
            pass  # best-effort; vault works without a UUID
        return None

    @staticmethod
    def _machine_fingerprint() -> bytes:
        """Derive stable per-machine key material (no passphrase required).

        Robust across 32-bit / 64-bit Python on the same OS and across
        Rosetta-translated vs. native Python on Apple Silicon:

        - Uses ``_os_arch()`` (kernel arch) instead of ``platform.machine()``
          (Python bitness) so 32-bit and 64-bit Python produce the same result.
        - Appends a stable OS-level UUID (``_stable_machine_uuid()``) when
          available, providing stronger binding to the physical machine.
        """
        parts = [
            platform.node(),
            socket.gethostname(),
            platform.system(),
            CryptoEngine._os_arch(),
        ]
        uid = CryptoEngine._stable_machine_uuid()
        if uid:
            parts.append(uid)
        return "-".join(p for p in parts if p).encode()

    @staticmethod
    def _legacy_fingerprint() -> bytes:
        """Reconstruct the fingerprint as produced by the pre-cross-platform code.

        Used by the auto-migration path in ``navig.vault.core`` to re-key
        vaults that were created before the stable UUID / arch fix.

        The old code used ``platform.machine()`` (Python bitness) and added
        a Windows ``MachineGuid`` only when the old registry call succeeded
        (no ``KEY_WOW64_64KEY`` — so 32-bit Python on 64-bit Windows got no
        GUID).  We cannot know which variant was in effect at write time, so
        the migration logic probes both variants; this method returns the
        no-UUID base, and the migration tries appending it separately.
        """
        parts = [
            platform.node(),
            socket.gethostname(),
            platform.system(),
            platform.machine(),  # intentional: reproduce old behaviour
        ]
        return "-".join(p for p in parts if p).encode()

    @staticmethod
    def kdf_info() -> str:
        """Human-readable description of the active KDF (for navig vault doctor)."""
        if _argon2_funcs is not None:
            return (
                f"Argon2id  m={_A2_MEMORY_COST // 1024}MiB  t={_A2_TIME_COST}  p={_A2_PARALLELISM}"
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
            raise CryptoError(f"Blob too short ({len(blob)} bytes) — minimum is {min_len}")
        return cls.decrypt(key, blob[:_NONCE_LEN], blob[_NONCE_LEN:], aad)
