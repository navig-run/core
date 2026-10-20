"""The .navigmove format: what opens, and everything that must refuse to."""

from __future__ import annotations

import os
import struct

import pytest

from navig.move import bundle as b

PASS = "ABCDE-FGHJK-MNPQR-STUVW-XYZ23"


def _seal(tmp_path, data: bytes):
    plain = tmp_path / "plain"
    plain.write_bytes(data)
    out = tmp_path / "x.navigmove"
    b.encrypt_file(plain, out, PASS, {"hostname": "old"})
    return out


@pytest.mark.parametrize("size", [0, 1, b.CHUNK - 1, b.CHUNK, b.CHUNK + 1, 2 * b.CHUNK + 3])
def test_round_trip_across_chunk_boundaries(tmp_path, size) -> None:
    data = os.urandom(size)
    assert b.decrypt_bytes(_seal(tmp_path, data), PASS) == data


def test_the_passphrase_survives_retyping(tmp_path) -> None:
    out = _seal(tmp_path, b"hello")
    assert b.decrypt_bytes(out, " abcde fghjk mnpqr stuvw xyz23 ") == b"hello"


def test_a_generated_passphrase_is_readable_and_strong() -> None:
    p = b.new_passphrase()
    assert len(p) == 29 and p.count("-") == 4
    assert not set(b.normalize_passphrase(p)) & set("01OIL")
    assert b.new_passphrase() != p


def test_the_header_needs_no_passphrase_and_names_the_source(tmp_path) -> None:
    out = _seal(tmp_path, b"x")
    assert b.read_header(out)["source"] == {"hostname": "old"}


def test_a_wrong_passphrase_says_so(tmp_path) -> None:
    out = _seal(tmp_path, b"secret")
    with pytest.raises(b.BundleError, match="Wrong passphrase"):
        b.decrypt_bytes(out, "AAAAA-AAAAA")


def test_a_truncated_bundle_is_incomplete_not_short(tmp_path) -> None:
    out = _seal(tmp_path, os.urandom(b.CHUNK + 100))
    raw = out.read_bytes()
    out.write_bytes(raw[: len(raw) - 200])
    with pytest.raises(b.BundleError, match="incomplete"):
        b.decrypt_bytes(out, PASS)


def test_dropping_the_final_chunk_cleanly_is_still_caught(tmp_path) -> None:
    """Cut exactly at a chunk boundary: every remaining chunk is genuine, only the last
    one is missing — the reader must not mistake that for the end."""
    out = _seal(tmp_path, os.urandom(b.CHUNK + 100))
    raw = out.read_bytes()
    (hlen,) = struct.unpack(">I", raw[10:14])
    first = 14 + hlen
    (clen,) = struct.unpack(">I", raw[first : first + 4])
    out.write_bytes(raw[: first + 4 + clen])
    with pytest.raises(b.BundleError, match="incomplete"):
        b.decrypt_bytes(out, PASS)


def test_a_changed_header_breaks_every_chunk(tmp_path) -> None:
    out = _seal(tmp_path, b"payload")
    raw = bytearray(out.read_bytes())
    i = raw.index(b'"old"')
    raw[i + 1 : i + 4] = b"new"
    out.write_bytes(bytes(raw))
    with pytest.raises(b.BundleError, match="Wrong passphrase, or the bundle was changed"):
        b.decrypt_bytes(out, PASS)


def test_a_flipped_byte_in_the_data_is_caught(tmp_path) -> None:
    out = _seal(tmp_path, b"payload" * 100)
    raw = bytearray(out.read_bytes())
    raw[-5] ^= 0x01
    out.write_bytes(bytes(raw))
    with pytest.raises(b.BundleError):
        b.decrypt_bytes(out, PASS)


def test_not_a_bundle_and_a_newer_format_are_named(tmp_path) -> None:
    other = tmp_path / "other.navigmove"
    other.write_bytes(b"PK\x03\x04 a zip")
    with pytest.raises(b.BundleError, match="not a NAVIG move bundle"):
        b.read_header(other)
    out = _seal(tmp_path, b"x")
    raw = out.read_bytes().replace(b'"version": 1', b'"version": 9')
    out.write_bytes(raw)
    with pytest.raises(b.BundleError, match="newer NAVIG"):
        b.read_header(out)


def test_an_aborted_write_leaves_nothing_that_looks_finished(tmp_path) -> None:
    out = tmp_path / "x.navigmove"
    w = b.BundleWriter(out, PASS, {})
    w.write(b"half")
    w.abort()
    assert not out.exists()
    assert not out.with_name(out.name + ".part").exists()
