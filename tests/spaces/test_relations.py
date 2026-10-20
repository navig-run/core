"""Cross-Space relations: manifest round-trip, write-time refusals, read-time tolerance.

The contract under test (Phase C):
- save → load is idempotent; an empty block DELETES the manifest key;
- a malformed ``relations`` block reads as "no relations" — never raises;
- unknown ids / missing paths resolve to ``available=False``, never an exception;
- link refuses self-links, unregistered targets and bad access; duplicates no-op;
- set_parent refuses self-parenting and cycles, and can clear with ``None``;
- an already-circular parent chain on disk is REPORTED (``parent_cycle``) at read;
- collections round-trip and tolerate garbage.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.spaces import registry
from navig.spaces import relations as rel_mod


def _isolate(tmp_path: Path, monkeypatch) -> Path:
    f = tmp_path / "spaces.json"
    monkeypatch.setattr(registry, "_registry_file", lambda: f)
    return f


def _mk_space(root: Path, sid: str) -> Path:
    p = root / f"{sid}-space"
    (p / ".navig").mkdir(parents=True, exist_ok=True)
    (p / ".navig" / "space.json").write_text(
        json.dumps({"id": sid, "name": sid.title(), "version": "1.0.0"}, indent=2) + "\n",
        encoding="utf-8",
    )
    return p


def _seed(root: Path, *ids: str) -> dict[str, Path]:
    return {sid: _mk_space(root, sid) for sid in ids}


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------


def test_link_round_trips_through_the_manifest(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    a = _seed(tmp_path, "alpha", "beta")
    registry.register(a["alpha"], id="alpha", name="Alpha")
    registry.register(a["beta"], id="beta", name="Beta")

    rel_mod.link(
        a["alpha"],
        "beta",
        include=("plans/*.md", "wiki/**"),
        exclude=("vault/**",),
        profile="brief",
    )
    loaded = rel_mod.load_relations(a["alpha"])

    assert loaded.parent is None
    assert len(loaded.links) == 1
    assert loaded.links[0].space == "beta"
    assert loaded.links[0].access == "read"
    assert loaded.links[0].include == ("plans/*.md", "wiki/**")
    assert loaded.links[0].exclude == ("vault/**",)
    assert loaded.links[0].profile == "brief"
    # Idempotent serialize: reload → re-save → identical manifest bytes.
    again = (a["alpha"] / ".navig" / "space.json").read_text(encoding="utf-8")
    rel_mod.save_relations(a["alpha"], loaded)
    assert (a["alpha"] / ".navig" / "space.json").read_text(encoding="utf-8") == again


def test_empty_relations_delete_the_manifest_key(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    a = _seed(tmp_path, "alpha", "beta")
    registry.register(a["alpha"], id="alpha", name="Alpha")
    registry.register(a["beta"], id="beta", name="Beta")
    rel_mod.link(a["alpha"], "beta")
    assert "relations" in json.loads((a["alpha"] / ".navig" / "space.json").read_text(encoding="utf-8"))

    rel_mod.unlink(a["alpha"], "beta")
    data = json.loads((a["alpha"] / ".navig" / "space.json").read_text(encoding="utf-8"))
    assert "relations" not in data  # additive schema stays clean
    assert rel_mod.load_relations(a["alpha"]).empty


def test_saving_empty_over_absent_writes_nothing(tmp_path):
    p = tmp_path / "bare"
    (p / ".navig").mkdir(parents=True)
    assert rel_mod.save_relations(p, rel_mod.Relations()) is None


# ---------------------------------------------------------------------------
# Read-time tolerance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "manifest_text",
    [
        pytest.param('"just a string"', id="manifest-not-an-object"),
        pytest.param("not-json-at-all", id="manifest-corrupt"),
        pytest.param('{"id": "b", "relations": 42}', id="block-not-a-dict"),
        pytest.param('{"id": "b", "relations": {"links": "nope", "parent": 5}}', id="wrong-field-types"),
        pytest.param(
            '{"id": "b", "relations": {"links": [{"space": ""}, {"space": "x", "access": "root"}, 7]}}',
            id="bad-link-entries",
        ),
        pytest.param('{"id": "b", "relations": {"parent": "   "}}', id="blank-parent"),
    ],
)
def test_malformed_relations_read_as_empty_not_an_exception(tmp_path, manifest_text):
    p = tmp_path / "broken"
    (p / ".navig").mkdir(parents=True, exist_ok=True)
    (p / ".navig" / "space.json").write_text(manifest_text, encoding="utf-8")
    loaded = rel_mod.load_relations(p)
    assert loaded.empty, f"manifest {manifest_text!r} should read as empty relations, got {loaded}"


def test_unknown_and_missing_targets_resolve_unavailable(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    a = _seed(tmp_path, "alpha")
    registry.register(a["alpha"], id="alpha", name="Alpha")
    # beta is registered but its path is gone; ghost has no row at all.
    registry.register(tmp_path / "beta-space-gone", id="beta", name="Beta")

    rel = rel_mod.Relations(
        parent="ghost",
        links=(
            rel_mod.SpaceLink(space="beta"),
            rel_mod.SpaceLink(space="real"),
        ),
    )
    _seed(tmp_path, "real")  # real dir exists but is NOT registered → unknown-id
    resolved = rel_mod.resolve(rel, self_id="alpha")

    assert resolved.parent is not None and resolved.parent.available is False
    assert resolved.parent.reason == "unknown-id"
    by_id = {r.target_id: r for r in resolved.links}
    assert by_id["beta"].available is False and by_id["beta"].reason == "missing-path"
    assert by_id["real"].available is False and by_id["real"].reason == "unknown-id"
    assert set(resolved.unresolved) == {"ghost", "beta", "real"}


def test_resolved_link_for_a_registered_existing_space_is_available(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    a = _seed(tmp_path, "alpha", "beta")
    registry.register(a["alpha"], id="alpha", name="Alpha")
    registry.register(a["beta"], id="beta", name="Beta")

    resolved = rel_mod.resolve(rel_mod.Relations(parent="beta"), self_id="alpha")
    assert resolved.parent is not None and resolved.parent.available is True
    assert resolved.parent.target_path == a["beta"]
    assert resolved.parent_cycle is False
    assert resolved.unresolved == ()


def test_duplicate_registry_id_resolves_first_row_like_discovery(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    first = _mk_space(tmp_path, "twin-a")
    second = _mk_space(tmp_path, "twin-b")
    registry.register(first, id="twin", name="First")
    registry.register(second, id="twin", name="Second")

    reg = rel_mod.registered_spaces()
    assert reg["twin"] == first  # first row wins — same as discovery keeps the first


# ---------------------------------------------------------------------------
# Write-time refusals
# ---------------------------------------------------------------------------


def test_link_refuses_self_unregistered_and_bad_access(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    a = _seed(tmp_path, "alpha")
    registry.register(a["alpha"], id="alpha", name="Alpha")

    with pytest.raises(ValueError, match="cannot link to itself"):
        rel_mod.link(a["alpha"], "alpha")
    with pytest.raises(ValueError, match="not a registered space"):
        rel_mod.link(a["alpha"], "nope")
    with pytest.raises(ValueError, match="access must be one of"):
        rel_mod.link(a["alpha"], "alpha", access="write")


def test_link_is_idempotent(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    a = _seed(tmp_path, "alpha", "beta")
    registry.register(a["alpha"], id="alpha", name="Alpha")
    registry.register(a["beta"], id="beta", name="Beta")

    first = rel_mod.link(a["alpha"], "beta")
    second = rel_mod.link(a["alpha"], "beta")
    assert len(second.links) == 1
    assert second == first


def test_parent_cycle_and_self_parent_are_refused(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    a = _seed(tmp_path, "alpha", "beta", "gamma")
    for sid in ("alpha", "beta", "gamma"):
        registry.register(a[sid], id=sid, name=sid.title())

    rel_mod.set_parent(a["alpha"], "beta")   # alpha -> beta
    rel_mod.set_parent(a["beta"], "gamma")   # alpha -> beta -> gamma

    with pytest.raises(ValueError, match="cannot be its own parent"):
        rel_mod.set_parent(a["alpha"], "alpha")
    with pytest.raises(ValueError, match="cycle"):
        rel_mod.set_parent(a["gamma"], "alpha")  # would close gamma -> alpha -> beta -> gamma

    assert rel_mod.load_relations(a["alpha"]).parent == "beta"


def test_set_parent_clears_with_none_and_deletes_the_key(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    a = _seed(tmp_path, "alpha", "beta")
    registry.register(a["alpha"], id="alpha", name="Alpha")
    registry.register(a["beta"], id="beta", name="Beta")

    rel_mod.set_parent(a["alpha"], "beta")
    assert rel_mod.load_relations(a["alpha"]).parent == "beta"
    rel_mod.set_parent(a["alpha"], None)
    data = json.loads((a["alpha"] / ".navig" / "space.json").read_text(encoding="utf-8"))
    assert "relations" not in data


def test_a_hand_written_cycle_is_reported_at_read_time(tmp_path, monkeypatch):
    """Corrupt/legacy data: a -> b -> a must surface as parent_cycle, not raise."""
    _isolate(tmp_path, monkeypatch)
    a = _seed(tmp_path, "alpha", "beta")
    registry.register(a["alpha"], id="alpha", name="Alpha")
    registry.register(a["beta"], id="beta", name="Beta")
    # Hand-write the cycle the write path refuses to create.
    for sid, parent in (("alpha", "beta"), ("beta", "alpha")):
        p = a[sid] / ".navig" / "space.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["relations"] = {"parent": parent}
        p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    resolved = rel_mod.resolve(rel_mod.load_relations(a["alpha"]), self_id="alpha")
    assert resolved.parent_cycle is True
    assert resolved.parent is not None and resolved.parent.available is True


def test_resolve_survives_a_upstream_cycle_without_wanting_to(tmp_path, monkeypatch):
    """b -> c -> b (not through alpha): the walk stops on `seen`, no hang, no raise."""
    _isolate(tmp_path, monkeypatch)
    a = _seed(tmp_path, "alpha", "beta", "gamma")
    for sid in ("alpha", "beta", "gamma"):
        registry.register(a[sid], id=sid, name=sid.title())
    for sid, parent in (("beta", "gamma"), ("gamma", "beta")):
        p = a[sid] / ".navig" / "space.json"
        data = json.loads(p.read_text(encoding="utf-8"))
        data["relations"] = {"parent": parent}
        p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    resolved = rel_mod.resolve(rel_mod.Relations(parent="beta"), self_id="alpha")
    assert resolved.parent_cycle is False
    assert resolved.parent is not None and resolved.parent.available is True


# ---------------------------------------------------------------------------
# Collections (UI grouping, same tolerance rules)
# ---------------------------------------------------------------------------


def test_collections_round_trip_and_tolerate_garbage(tmp_path):
    p = tmp_path / "grp"
    (p / ".navig").mkdir(parents=True)
    rel_mod.save_collections(p, ("work", "home", "work"))  # dedupes
    assert rel_mod.load_collections(p) == ("work", "home")

    # Garbage reads as empty — never raises.
    (p / ".navig" / "space.json").write_text(json.dumps({"id": "grp", "collections": 7}), encoding="utf-8")
    assert rel_mod.load_collections(p) == ()

    # Empty save deletes the key.
    rel_mod.save_collections(p, ())
    assert "collections" not in json.loads((p / ".navig" / "space.json").read_text(encoding="utf-8"))
