"""Tests for sigil_store.ensure_sigil — create the node identity if missing.

`ensure_sigil` did not exist: onboarding imported it (phantom), so the identity
was never created and `navig whoami` reported "No entity found" even after
`navig onboard`. It now derives from the machine-fingerprint seed and persists,
idempotently.
"""

from __future__ import annotations

import pytest


def test_ensure_sigil_creates_when_missing(monkeypatch, tmp_path):
    from navig.identity import sigil_store

    monkeypatch.setattr(sigil_store, "_entity_json_path", lambda: tmp_path / "entity.json")

    assert sigil_store.entity_exists() is False

    ent = sigil_store.ensure_sigil(demo=True)  # demo=True → deterministic seed

    assert sigil_store.entity_exists() is True
    assert ent.seed == "deadbeef" * 8
    assert sigil_store.load_entity()["seed"] == "deadbeef" * 8


def test_ensure_sigil_is_idempotent_and_never_reseeds(monkeypatch, tmp_path):
    from navig.identity import sigil_store

    monkeypatch.setattr(sigil_store, "_entity_json_path", lambda: tmp_path / "entity.json")

    monkeypatch.setenv("NAVIG_DEMO_SEED", "aaaa" * 16)
    first = sigil_store.ensure_sigil(demo=True)
    assert first.seed == "aaaa" * 16

    # Even if the seed source changes, an existing identity is loaded, not re-seeded.
    monkeypatch.setenv("NAVIG_DEMO_SEED", "bbbb" * 16)
    second = sigil_store.ensure_sigil(demo=True)
    assert second.seed == "aaaa" * 16
    assert second.name == first.name


def test_ensure_sigil_refuses_to_reidentify_over_an_unreadable_present_file(monkeypatch, tmp_path):
    """The catastrophe: a transient lock on a PRESENT valid entity.json made load_entity
    return None, so ensure_sigil minted a NEW seed and OVERWROTE it — silently
    RE-IDENTIFYING the node (a different NaviEntity). It must now REFUSE and leave the
    identity byte-for-byte untouched."""
    from navig.identity import sigil_store
    from navig.identity.sigil_store import SigilReadError

    monkeypatch.setattr(sigil_store, "_entity_json_path", lambda: tmp_path / "entity.json")

    monkeypatch.setenv("NAVIG_DEMO_SEED", "cccc" * 16)
    original = sigil_store.ensure_sigil(demo=True)  # a real identity now exists on disk
    assert original.seed == "cccc" * 16
    on_disk = (tmp_path / "entity.json").read_bytes()

    # The read now fails transiently (a lock that outlives the retries) → load_entity None.
    def _boom(*_a, **_k):
        raise OSError("sharing violation")

    monkeypatch.setattr(sigil_store, "read_text_retrying", _boom)
    monkeypatch.setenv("NAVIG_DEMO_SEED", "dddd" * 16)  # a DIFFERENT seed is available

    with pytest.raises(SigilReadError):
        sigil_store.ensure_sigil(demo=True)  # must NOT re-seed over the present file

    assert (tmp_path / "entity.json").read_bytes() == on_disk  # identity untouched — no re-identify


def test_ensure_sigil_still_genesis_when_genuinely_absent(monkeypatch, tmp_path):
    """The guard must only fire for a PRESENT file — a real first run (no file) still
    genesis-es normally."""
    from navig.identity import sigil_store

    monkeypatch.setattr(sigil_store, "_entity_json_path", lambda: tmp_path / "entity.json")
    assert not (tmp_path / "entity.json").exists()

    ent = sigil_store.ensure_sigil(demo=True)  # no file → legitimate genesis
    assert ent.seed  # created
    assert (tmp_path / "entity.json").exists()
