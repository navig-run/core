"""Tests for sigil_store.ensure_sigil — create the node identity if missing.

`ensure_sigil` did not exist: onboarding imported it (phantom), so the identity
was never created and `navig whoami` reported "No entity found" even after
`navig onboard`. It now derives from the machine-fingerprint seed and persists,
idempotently.
"""

from __future__ import annotations


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
