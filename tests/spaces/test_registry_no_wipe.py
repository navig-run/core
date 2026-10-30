"""spaces.json is a multi-record store with security-relevant `trusted`/`enabled` flags
and an `active` pointer. A transient OS lock or a corrupt read must NOT let a single
mutation (enable / trust / forget / activate) wipe every OTHER space or reset trust.
Regression for the JSON-store config-wipe class (json_io).
"""

from navig.core.json_io import JsonReadError
from navig.spaces import registry


def _isolate(tmp_path, monkeypatch):
    f = tmp_path / "spaces.json"
    monkeypatch.setattr(registry, "_registry_file", lambda: f)
    return f


def _raise_lock(*_a, **_k):
    raise JsonReadError("simulated transient lock (sharing violation)")


def _seed_two(tmp_path):
    registry.register(tmp_path / "alpha", id="alpha", name="Alpha")
    registry.register(tmp_path / "beta", id="beta", name="Beta")


def test_mutation_preserves_other_spaces(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _seed_two(tmp_path)

    assert registry.set_enabled("alpha", False) is True

    reg = registry.load_registry()
    ids = {s["id"]: s.get("enabled") for s in reg["spaces"]}
    assert ids == {"alpha": False, "beta": True}  # beta untouched, alpha flipped


def test_transient_lock_refuses_every_mutator_no_wipe(tmp_path, monkeypatch):
    f = _isolate(tmp_path, monkeypatch)
    _seed_two(tmp_path)
    before = f.read_bytes()

    monkeypatch.setattr(registry, "load_json_for_update", _raise_lock)

    assert registry.set_enabled("alpha", False) is False
    assert registry.set_trusted("alpha", True) is False
    assert registry.forget("beta") is False
    registry.mark_active(tmp_path / "alpha")  # returns None, must not wipe
    registry.ensure_registered(tmp_path / "gamma", id="gamma")  # must not wipe

    assert f.read_bytes() == before  # both spaces + all flags survived — nothing wiped


def test_register_under_lock_raises_and_does_not_wipe(tmp_path, monkeypatch):
    """It returned a phantom entry stored nowhere, so `space register` and the deck
    route reported success for a registration that never happened."""
    import pytest

    f = _isolate(tmp_path, monkeypatch)
    _seed_two(tmp_path)
    before = f.read_bytes()

    monkeypatch.setattr(registry, "load_json_for_update", _raise_lock)
    with pytest.raises(registry.RegistryUnavailable):
        registry.register(tmp_path / "delta", id="delta", name="Delta")

    assert f.read_bytes() == before  # alpha + beta preserved — not overwritten by {delta}
    monkeypatch.undo()
    _isolate(tmp_path, monkeypatch)
    assert "delta" not in {s["id"] for s in registry.load_registry()["spaces"]}


def test_set_trusted_under_lock_never_resets_trust(tmp_path, monkeypatch):
    """Security: a locked read must not silently reset a space's `trusted` decision."""
    _isolate(tmp_path, monkeypatch)
    registry.register(tmp_path / "alpha", id="alpha")
    assert registry.set_trusted("alpha", True) is True
    assert registry.is_trusted(tmp_path / "alpha") is True

    monkeypatch.setattr(registry, "load_json_for_update", _raise_lock)
    assert registry.set_trusted("alpha", False) is False  # refused

    # the trust decision on disk is unchanged (still trusted)
    assert registry.is_trusted(tmp_path / "alpha") is True


def test_corrupt_registry_degrades_for_reads_and_quarantines_on_update(tmp_path, monkeypatch):
    f = _isolate(tmp_path, monkeypatch)
    f.write_text("{ this is not valid json ,,,", encoding="utf-8")

    # read-only discovery must not crash — degrades to an empty registry
    assert registry.load_registry()["spaces"] == []

    # a mutating load quarantines the corrupt bytes and starts empty (bytes preserved)
    reg = registry.load_registry_for_update()
    assert reg["spaces"] == []
    assert (tmp_path / "spaces.json.corrupt").exists()
