"""
Tests for navig.vault.crypto — CryptoEngine AES-GCM primitives.

Tests focus on the pure static methods (no key derivation → no slow KDF).
"""

from __future__ import annotations

import os

import pytest

from navig.vault.crypto import (
    CryptoEngine,
    CryptoError,
    _KEY_LEN,
    _NONCE_LEN,
)


# ─── generate_dek ─────────────────────────────────────────────────────────────


def test_generate_dek_returns_32_bytes():
    dek = CryptoEngine.generate_dek()
    assert isinstance(dek, bytes)
    assert len(dek) == _KEY_LEN  # 32


def test_generate_dek_random_each_time():
    assert CryptoEngine.generate_dek() != CryptoEngine.generate_dek()


# ─── encrypt / decrypt ────────────────────────────────────────────────────────


def test_encrypt_decrypt_roundtrip():
    key = CryptoEngine.generate_dek()
    plaintext = b"Hello, vault!"
    nonce, ct = CryptoEngine.encrypt(key, plaintext)
    recovered = CryptoEngine.decrypt(key, nonce, ct)
    assert recovered == plaintext


def test_encrypt_produces_nonce_of_correct_length():
    key = CryptoEngine.generate_dek()
    nonce, _ = CryptoEngine.encrypt(key, b"data")
    assert len(nonce) == _NONCE_LEN  # 12


def test_encrypt_ciphertext_different_from_plaintext():
    key = CryptoEngine.generate_dek()
    plaintext = b"secret"
    _, ct = CryptoEngine.encrypt(key, plaintext)
    assert ct != plaintext


def test_decrypt_wrong_key_raises_crypto_error():
    key = CryptoEngine.generate_dek()
    wrong_key = CryptoEngine.generate_dek()
    nonce, ct = CryptoEngine.encrypt(key, b"data")
    with pytest.raises(CryptoError, match="Decryption failed"):
        CryptoEngine.decrypt(wrong_key, nonce, ct)


def test_decrypt_tampered_ciphertext_raises_crypto_error():
    key = CryptoEngine.generate_dek()
    nonce, ct = CryptoEngine.encrypt(key, b"important")
    tampered = ct[:-1] + bytes([ct[-1] ^ 0xFF])
    with pytest.raises(CryptoError):
        CryptoEngine.decrypt(key, nonce, tampered)


def test_encrypt_with_aad_roundtrip():
    key = CryptoEngine.generate_dek()
    plaintext = b"protected"
    aad = b"metadata-header"
    nonce, ct = CryptoEngine.encrypt(key, plaintext, aad=aad)
    recovered = CryptoEngine.decrypt(key, nonce, ct, aad=aad)
    assert recovered == plaintext


def test_decrypt_wrong_aad_raises_crypto_error():
    key = CryptoEngine.generate_dek()
    nonce, ct = CryptoEngine.encrypt(key, b"data", aad=b"correct-aad")
    with pytest.raises(CryptoError):
        CryptoEngine.decrypt(key, nonce, ct, aad=b"wrong-aad")


# ─── seal / open ──────────────────────────────────────────────────────────────


def test_seal_open_roundtrip():
    key = CryptoEngine.generate_dek()
    plaintext = b"sealed payload"
    blob = CryptoEngine.seal(key, plaintext)
    assert CryptoEngine.open(key, blob) == plaintext


def test_seal_blob_length():
    key = CryptoEngine.generate_dek()
    plaintext = b"x" * 50
    blob = CryptoEngine.seal(key, plaintext)
    # blob = nonce (12) + ciphertext (50) + tag (16)
    assert len(blob) == _NONCE_LEN + len(plaintext) + 16


def test_seal_different_each_call():
    key = CryptoEngine.generate_dek()
    plaintext = b"same"
    assert CryptoEngine.seal(key, plaintext) != CryptoEngine.seal(key, plaintext)


def test_open_too_short_raises_crypto_error():
    key = CryptoEngine.generate_dek()
    with pytest.raises(CryptoError, match="too short"):
        CryptoEngine.open(key, b"tiny")


def test_open_wrong_key_raises_crypto_error():
    key = CryptoEngine.generate_dek()
    wrong = CryptoEngine.generate_dek()
    blob = CryptoEngine.seal(key, b"secret")
    with pytest.raises(CryptoError):
        CryptoEngine.open(wrong, blob)


def test_seal_open_with_aad():
    key = CryptoEngine.generate_dek()
    aad = b"item-id-123"
    blob = CryptoEngine.seal(key, b"value", aad=aad)
    assert CryptoEngine.open(key, blob, aad=aad) == b"value"


def test_open_wrong_aad_raises():
    key = CryptoEngine.generate_dek()
    blob = CryptoEngine.seal(key, b"value", aad=b"correct")
    with pytest.raises(CryptoError):
        CryptoEngine.open(key, blob, aad=b"wrong")


# ─── CryptoEngine instance — salt management ─────────────────────────────────


def test_crypto_engine_salt_file_created(tmp_path):
    engine = CryptoEngine(vault_dir=tmp_path)
    salt = engine._get_or_create_salt()
    assert (tmp_path / CryptoEngine.SALT_FILE).exists()
    assert len(salt) == 32


def test_crypto_engine_salt_consistent(tmp_path):
    engine = CryptoEngine(vault_dir=tmp_path)
    salt1 = engine._get_or_create_salt()
    salt2 = engine._get_or_create_salt()
    assert salt1 == salt2


def test_crypto_engine_salt_persists_across_instances(tmp_path):
    engine1 = CryptoEngine(vault_dir=tmp_path)
    salt1 = engine1._get_or_create_salt()

    engine2 = CryptoEngine(vault_dir=tmp_path)
    salt2 = engine2._get_or_create_salt()
    assert salt1 == salt2


def test_crypto_engine_corrupt_salt_raises(tmp_path):
    salt_path = tmp_path / CryptoEngine.SALT_FILE
    salt_path.write_bytes(b"short")
    engine = CryptoEngine(vault_dir=tmp_path)
    with pytest.raises(CryptoError, match="corrupt"):
        engine._get_or_create_salt()


# ─── CryptoError ──────────────────────────────────────────────────────────────


def test_crypto_error_is_exception():
    err = CryptoError("test error")
    assert isinstance(err, Exception)
    assert str(err) == "test error"


# ─── kdf_info ─────────────────────────────────────────────────────────────────


def test_kdf_info_returns_string():
    info = CryptoEngine.kdf_info()
    assert isinstance(info, str)
    assert len(info) > 0
