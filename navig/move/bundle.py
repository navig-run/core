"""The ``.navigmove`` file: one machine's NAVIG, encrypted for the trip to another.

Layout (all integers big-endian)::

    b"NAVIGMOVE1"                    magic, 10 bytes
    u32  header length
    header                           UTF-8 JSON (format, version, kdf, cipher, source …)
    repeated:  u32 chunk length | AES-256-GCM(chunk)

The key comes from a one-time passphrase through scrypt (n=2^15, r=8, p=1). Each chunk
is sealed with nonce = 7-byte random prefix | u32 chunk index | 1-byte "final" flag, and
the whole header as associated data, so a reordered, dropped, truncated or re-headed file
fails to open instead of opening wrong. A bundle that ends before its final chunk is
refused as incomplete.

The plaintext (a tar.gz the caller streams through :class:`BundleWriter`) never touches
the disk: the bundle carries every secret of the machine it came from, so neither side
may leave a decrypted copy behind. Nothing here touches NAVIG state.
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import struct
from pathlib import Path
from typing import Any, BinaryIO

MAGIC = b"NAVIGMOVE1"
FORMAT = "navig-move"
VERSION = 1
CHUNK = 4 * 1024 * 1024
_SCRYPT = {"n": 2**15, "r": 8, "p": 1}
_MAX_HEADER = 64 * 1024
# A sealed chunk is the plaintext plus the 16-byte GCM tag; anything bigger is not ours.
_MAX_SEALED = CHUNK + 16
# Readable, unambiguous (no 0/O, 1/I/L): 25 symbols from 31 is ~124 bits.
_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


class BundleError(Exception):
    """The bundle cannot be opened — the message says why, in words a person can act on."""


def new_passphrase() -> str:
    """A one-time passphrase, grouped for reading aloud: ``ABCDE-FGHJK-…`` (5 × 5)."""
    raw = "".join(secrets.choice(_ALPHABET) for _ in range(25))
    return "-".join(raw[i : i + 5] for i in range(0, 25, 5))


def normalize_passphrase(text: str) -> str:
    """What the person typed, minus spaces, dashes and case — so a pasted or retyped
    passphrase matches the one that was shown."""
    return "".join(ch for ch in (text or "").upper() if ch.isalnum())


def _derive(passphrase: str, salt: bytes) -> bytes:
    from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

    secret = normalize_passphrase(passphrase).encode("utf-8")
    if not secret:
        raise BundleError("The passphrase is empty.")
    return Scrypt(salt=salt, length=32, **_SCRYPT).derive(secret)


def _nonce(prefix: bytes, index: int, final: bool) -> bytes:
    return prefix + struct.pack(">I", index) + (b"\x01" if final else b"\x00")


class BundleWriter:
    """A write-only file object: what is written comes out sealed in *out*.

    Written to ``<out>.part`` and renamed on a clean :meth:`close`, so an interrupted run
    never leaves something that looks like a finished bundle. :meth:`abort` discards it.
    """

    def __init__(self, out: Path, passphrase: str, source: dict[str, Any]) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        salt = os.urandom(16)
        self._prefix = os.urandom(7)
        self.header = {
            "format": FORMAT,
            "version": VERSION,
            "kdf": {"name": "scrypt", **_SCRYPT, "salt": base64.b64encode(salt).decode()},
            "cipher": "aes-256-gcm-chunked",
            "chunk": CHUNK,
            "nonce_prefix": base64.b64encode(self._prefix).decode(),
            "source": source,
        }
        self._header_bytes = json.dumps(self.header, sort_keys=True).encode("utf-8")
        self._aead = AESGCM(_derive(passphrase, salt))
        self._out = out
        self._tmp = out.with_name(out.name + ".part")
        self._f: BinaryIO | None = self._tmp.open("wb")
        self._f.write(MAGIC + struct.pack(">I", len(self._header_bytes)) + self._header_bytes)
        self._buf = bytearray()
        self._index = 0

    def writable(self) -> bool:
        return True

    def _seal(self, block: bytes, final: bool) -> None:
        if self._f is None:
            raise ValueError("write to a closed bundle")
        sealed = self._aead.encrypt(_nonce(self._prefix, self._index, final), block, self._header_bytes)
        self._f.write(struct.pack(">I", len(sealed)) + sealed)
        self._index += 1

    def write(self, data: bytes) -> int:
        if self._f is None:
            raise ValueError("write to a closed bundle")
        self._buf += data
        # Keep at least one byte back: the LAST chunk must be sealed as final, and only
        # close() knows which one that is.
        while len(self._buf) > CHUNK:
            self._seal(bytes(self._buf[:CHUNK]), final=False)
            del self._buf[:CHUNK]
        return len(data)

    def flush(self) -> None:
        return None

    def close(self) -> None:
        if self._f is None:
            return
        self._seal(bytes(self._buf), final=True)
        self._buf.clear()
        self._f.close()
        self._f = None
        os.replace(self._tmp, self._out)

    def abort(self) -> None:
        if self._f is not None:
            self._f.close()
            self._f = None
        self._tmp.unlink(missing_ok=True)


def _read_header(f: BinaryIO) -> tuple[dict[str, Any], bytes]:
    if f.read(len(MAGIC)) != MAGIC:
        raise BundleError("This is not a NAVIG move bundle.")
    raw_len = f.read(4)
    if len(raw_len) != 4:
        raise BundleError("The bundle is incomplete.")
    (length,) = struct.unpack(">I", raw_len)
    if length > _MAX_HEADER:
        raise BundleError("The bundle header is damaged.")
    header_bytes = f.read(length)
    try:
        header = json.loads(header_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError("The bundle header is damaged.") from exc
    if not isinstance(header, dict) or header.get("format") != FORMAT:
        raise BundleError("This is not a NAVIG move bundle.")
    if header.get("version") != VERSION:
        raise BundleError(
            f"This bundle was made by a newer NAVIG (format {header.get('version')}). Update NAVIG here first."
        )
    return header, header_bytes


def read_header(bundle: Path) -> dict[str, Any]:
    """The bundle's header (where it came from, when) — no passphrase needed."""
    with bundle.open("rb") as f:
        return _read_header(f)[0]


class BundleReader:
    """A read-only file object over a bundle's plaintext, verified chunk by chunk.

    A wrong passphrase fails on the first chunk; tampering or truncation fail where they
    are found, and a stream that ends before the final chunk is an error, never a short
    read that looks like the end.
    """

    def __init__(self, bundle: Path, passphrase: str) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        self._f: BinaryIO | None = bundle.open("rb")
        try:
            self.header, self._header_bytes = _read_header(self._f)
            kdf = self.header["kdf"]
            salt = base64.b64decode(kdf["salt"])
            self._prefix = base64.b64decode(self.header["nonce_prefix"])
            if kdf.get("name") != "scrypt" or len(self._prefix) != 7:
                raise BundleError("The bundle header is damaged.")
            self._aead = AESGCM(_derive(passphrase, salt))
        except (KeyError, TypeError, ValueError) as exc:
            self.close()
            raise BundleError("The bundle header is damaged.") from exc
        except BundleError:
            self.close()
            raise
        self._buf = b""
        self._index = 0
        self._done = False

    def readable(self) -> bool:
        return True

    def _next_chunk(self) -> None:
        from cryptography.exceptions import InvalidTag

        if self._f is None:
            raise ValueError("read from a closed bundle")
        raw_len = self._f.read(4)
        if len(raw_len) != 4:
            raise BundleError("The bundle is incomplete — it was cut off while copying.")
        (length,) = struct.unpack(">I", raw_len)
        if length > _MAX_SEALED:
            raise BundleError("The bundle is damaged.")
        sealed = self._f.read(length)
        if len(sealed) != length:
            raise BundleError("The bundle is incomplete — it was cut off while copying.")
        for final in (False, True):
            try:
                block = self._aead.decrypt(_nonce(self._prefix, self._index, final), sealed, self._header_bytes)
            except InvalidTag:
                continue
            self._index += 1
            self._buf += block
            if final:
                if self._f.read(1):
                    raise BundleError("The bundle is damaged (data after its end).")
                self._done = True
            return
        raise BundleError(
            "Wrong passphrase, or the bundle was changed after it was made."
            if self._index == 0
            else "The bundle is damaged."
        )

    def read(self, n: int = -1) -> bytes:
        if self._f is None:
            raise ValueError("read from a closed bundle")
        while not self._done and (n < 0 or len(self._buf) < n):
            self._next_chunk()
        if n < 0:
            out, self._buf = self._buf, b""
        else:
            out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def close(self) -> None:
        if self._f is not None:
            self._f.close()
            self._f = None


def encrypt_file(plain: Path, out: Path, passphrase: str, source: dict[str, Any]) -> dict[str, Any]:
    """Seal a whole file (tests and tools; ``navig move`` streams through BundleWriter)."""
    writer = BundleWriter(out, passphrase, source)
    try:
        with plain.open("rb") as src:
            while block := src.read(1024 * 1024):
                writer.write(block)
    except BaseException:
        writer.abort()
        raise
    writer.close()
    return writer.header


def decrypt_bytes(bundle: Path, passphrase: str) -> bytes:
    """The whole plaintext in memory (tests and small bundles)."""
    reader = BundleReader(bundle, passphrase)
    try:
        return reader.read()
    finally:
        reader.close()


__all__ = [
    "BundleError",
    "BundleReader",
    "BundleWriter",
    "CHUNK",
    "decrypt_bytes",
    "encrypt_file",
    "new_passphrase",
    "normalize_passphrase",
    "read_header",
]
