"""Unified Environments data model (schema 1): classification, active pointers, recents.

Phase 1 contract under test — additive and backward-compatible by construction:
- manifest ``kind`` defaults to ``"space"`` (legacy manifests keep working; a
  physical folder name never decides the role);
- ``projects`` references are stable registry ids, ordered, de-duplicated,
  many-to-many (an id may appear in several Environment manifests);
- ``interface`` is an open dict (Environment-Pack bindings later), tolerant;
- active Environment/Area/Project use the SAME engine as the active space:
  NAVIG_* override → cache file (truth) → config.yaml mirror → None;
  the three pointers are independent and clearable;
- recent Environments: newest-first, de-duplicated, capped, garbage-safe;
- the registry file preserves unknown top-level keys (extension-safe, version 1).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.spaces import environments as env
from navig.spaces.space_manifest import SPACE_KINDS, SpaceManifest, set_manifest_field

ENV_VARS = ("NAVIG_ENVIRONMENT", "NAVIG_AREA", "NAVIG_PROJECT")


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch):
    """Own config dir + no pointer overrides — every test starts from truly unset."""
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("NAVIG_HOME", raising=False)
    for var in ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    from navig.config import reset_config_manager

    reset_config_manager()
    yield tmp_path
    reset_config_manager()


def _write_manifest(space_dir: Path, data: dict) -> Path:
    (space_dir / ".navig").mkdir(parents=True, exist_ok=True)
    p = space_dir / ".navig" / "space.json"
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return p


# ── Classification ────────────────────────────────────────────────────────────


def test_kind_defaults_to_space_for_legacy_manifests():
    m = SpaceManifest({"id": "legacy", "name": "Legacy"})
    assert m.kind == "space"
    assert SPACE_KINDS == ("environment", "area", "project", "space")


def test_kind_passes_through_valid_values_and_refuses_garbage():
    assert SpaceManifest({"kind": "environment"}).kind == "environment"
    assert SpaceManifest({"kind": "area"}).kind == "area"
    assert SpaceManifest({"kind": "project"}).kind == "project"
    # A name is not a kind: folder-naming must never decide the role.
    assert SpaceManifest({"kind": "dev-space"}).kind == "space"
    assert SpaceManifest({"kind": 42}).kind == "space"
    assert SpaceManifest({"kind": None}).kind == "space"


def test_project_refs_round_trip_order_dedupe_and_tolerance():
    assert SpaceManifest({}).project_refs == ()
    assert SpaceManifest({"projects": "navig"}).project_refs == ()  # not a list
    m = SpaceManifest({"projects": ["navig", "schema", "navig", 7, "  cybesis  ", ""]})
    assert m.project_refs == ("navig", "schema", "cybesis")


def test_interface_is_an_open_tolerant_dict():
    assert SpaceManifest({}).interface == {}
    assert SpaceManifest({"interface": "nope"}).interface == {}
    src = {"dashboard": "ops", "dock": {"rail": 4}, "extra": 1}
    got = SpaceManifest({"interface": src}).interface
    assert got == src and got is not src  # a copy — mutating the view never edits the manifest


def test_manifest_fields_survive_the_safe_writer(tmp_path: Path):
    """set_manifest_field is the single writer — new fields must round-trip through it."""
    d = tmp_path / "env-space"
    set_manifest_field(d, "kind", "environment", id_hint="env-space")
    set_manifest_field(d, "projects", ["navig", "schema"], id_hint="env-space")
    set_manifest_field(d, "interface", {"dashboard": "home"}, id_hint="env-space")
    m = SpaceManifest(json.loads((d / ".navig" / "space.json").read_text(encoding="utf-8")))
    assert m.kind == "environment"
    assert m.project_refs == ("navig", "schema")
    assert m.interface == {"dashboard": "home"}
    # Deletion keeps the additive schema clean (same rule as relations).
    set_manifest_field(d, "interface", None)
    assert "interface" not in json.loads((d / ".navig" / "space.json").read_text(encoding="utf-8"))


def test_membership_stays_on_relations_and_project_refs_are_many_to_many(tmp_path: Path):
    """The shape the OS will read: Area = kind + parent; two Environments share a project."""
    dev = tmp_path / "dev"
    _write_manifest(dev, {"id": "dev", "kind": "environment", "projects": ["navig", "schema"]})
    company = tmp_path / "company"
    _write_manifest(company, {"id": "company", "kind": "environment", "projects": ["cybesis", "navig"]})
    android = tmp_path / "android"
    _write_manifest(android, {"id": "android", "kind": "area", "relations": {"parent": "dev"}})

    from navig.spaces.environments import is_area, is_environment, space_kind
    from navig.spaces.relations import load_relations

    assert is_environment(dev) and is_environment(company)
    assert is_area(android)
    assert load_relations(android).parent == "dev"  # membership = the existing relation
    # 'navig' appears under two Environments — many-to-many, no false single parent.
    assert set(SpaceManifest(json.loads((dev / ".navig" / "space.json").read_text(encoding="utf-8"))).project_refs) == {"navig", "schema"}
    assert "navig" in SpaceManifest(json.loads((company / ".navig" / "space.json").read_text(encoding="utf-8"))).project_refs
    assert space_kind(android) == "area"


def test_unreadable_manifest_classifies_as_space(tmp_path: Path):
    bad = tmp_path / "broken"
    (bad / ".navig").mkdir(parents=True)
    (bad / ".navig" / "space.json").write_text("{not json", encoding="utf-8")
    assert env.space_kind(bad) == "space"
    assert env.is_area(bad) is False and env.is_environment(bad) is False
    assert env.classify(bad) == "space"


# ── Active pointers ───────────────────────────────────────────────────────────


def test_pointers_start_unset_and_stay_independent(isolated):
    assert env.resolve_active_environment() is None
    assert env.resolve_active_area() is None
    assert env.resolve_active_project() is None

    env.set_active_environment("home")
    assert env.resolve_active_environment() == "home"
    assert env.resolve_active_area() is None  # setting one never moves another
    assert env.resolve_active_project() is None


def test_env_var_override_wins_and_is_per_pointer(isolated, monkeypatch):
    monkeypatch.setenv("NAVIG_ENVIRONMENT", "company")
    monkeypatch.setenv("NAVIG_AREA", "paperwork")
    assert env.resolve_active_environment() == "company"
    assert env.resolve_active_area() == "paperwork"
    assert env.resolve_active_project() is None
    env.set_active_environment("homelab")  # cache written…
    assert env.resolve_active_environment() == "company"  # …but the override still wins


def test_cache_is_truth_and_config_is_the_fallback(isolated):
    env.set_active_environment("human")
    cache = isolated / "cache" / "active_environment.txt"
    assert cache.is_file() and cache.read_text(encoding="utf-8") == "human"

    # Truth lost → mirror still answers (the documented degradation).
    cache.unlink()
    from navig.config import reset_config_manager

    reset_config_manager()
    assert env.resolve_active_environment() == "human"


def test_clearing_removes_cache_and_resolves_none(isolated):
    env.set_active_project("navig")
    assert (isolated / "cache" / "active_project.txt").is_file()
    env.set_active_project(None)
    assert not (isolated / "cache" / "active_project.txt").exists()
    assert env.resolve_active_project() is None


def test_writes_land_in_the_isolated_config_yaml(isolated):
    env.set_active_area("android")
    cfg = (isolated / "config.yaml").read_text(encoding="utf-8")
    assert "android" in cfg and "area" in cfg


# ── Recent Environments ───────────────────────────────────────────────────────


def test_recents_are_newest_first_deduped_and_capped(isolated):
    assert env.recent_environments() == []
    for e in ("home", "company", "home", "human"):
        got = env.push_recent_environment(e)
    assert got[0] == "human"
    assert got.count("home") == 1  # re-push moves to front, never duplicates

    got = env.push_recent_environment("company")
    assert got[0] == "company" and "human" in got

    for i in range(env.RECENT_MAX + 5):
        env.push_recent_environment(f"env-{i}")
    from navig.config import reset_config_manager

    reset_config_manager()  # read back from the written yaml, not the in-memory cache
    final = env.recent_environments()
    assert len(final) == env.RECENT_MAX
    assert final[0] == f"env-{env.RECENT_MAX + 4}"  # newest first, capped


def test_recents_tolerate_garbage(isolated):
    from navig.config import get_config_manager, reset_config_manager

    cm = get_config_manager()
    gc = dict(cm.global_config)
    gc["environment"] = {"recent": "not-a-list"}
    from navig.core.yaml_io import atomic_write_yaml

    atomic_write_yaml(gc, Path(cm.global_config_dir) / "config.yaml", allow_unicode=True)
    reset_config_manager()
    assert env.recent_environments() == []


def test_push_without_id_keeps_order_and_clears_nothing(isolated):
    env.push_recent_environment("home")
    got = env.push_recent_environment("  ")  # blank → just normalize/cap, no crash
    assert got == ["home"]
    assert env.push_recent_environment(None) == ["home"]


# ── Registry extension contract ───────────────────────────────────────────────


def test_registry_preserves_unknown_top_level_keys(isolated):
    """The registry half of 'extend existing contracts': additive keys survive,
    version stays 1 — no parallel store, no migration for readers that don't care."""
    from navig.spaces import registry

    reg = registry.load_registry_for_update()
    reg["future_thing"] = {"enabled": True}
    registry.save_registry(reg)

    back = registry.load_registry()
    assert back["future_thing"] == {"enabled": True}
    assert back["version"] == 1
    assert isinstance(back["spaces"], list)


# ── Environment ↔ Project membership ─────────────────────────────────────────


def _mk_env(root: Path, sid: str, projects: list[str], kind: str = "environment") -> Path:
    from navig.spaces import registry

    d = root / f"{sid}-space"
    data = {"id": sid, "name": sid.title(), "kind": kind}
    if projects:
        data["projects"] = projects
    _write_manifest(d, data)
    registry.register(d, id=sid, name=sid.title(), source="external", enabled=True)
    return d


def test_environment_project_map_and_containing(isolated: Path):
    _mk_env(isolated, "dev", ["navig", "schema"])
    _mk_env(isolated, "company", ["navig"])
    _mk_env(isolated, "ops", ["navig"], kind="area")  # an Area's refs are NOT env membership

    pmap = env.environment_project_map()
    assert pmap["navig"] == ["company", "dev"]  # sorted, one scan
    assert pmap["schema"] == ["dev"]
    assert "navig" not in pmap or "ops" not in pmap.get("navig", [])
    assert env.environments_containing("navig") == ["company", "dev"]
    assert env.environments_containing("nope") == []
    assert set(env.environment_manifests()) == {"dev", "company"}


def test_attach_detach_round_trip_idempotent_and_validated(isolated: Path):
    dev = _mk_env(isolated, "dev", [])
    _mk_env(isolated, "area-under-dev", [], kind="area")
    from navig.spaces import registry

    registry.register(isolated / "proj-space", id="proj", name="Proj", source="external", enabled=True)

    first = env.attach_project("dev", "proj")
    assert first == ["proj"]
    again = env.attach_project("dev", "proj")  # idempotent
    assert again == ["proj"]
    assert "proj" in SpaceManifest(json.loads((dev / ".navig" / "space.json").read_text(encoding="utf-8"))).project_refs

    with pytest.raises(ValueError, match="not a registered space"):
        env.attach_project("ghost-env", "proj")
    with pytest.raises(ValueError, match="not an environment"):
        env.attach_project("area-under-dev", "proj")
    with pytest.raises(ValueError, match="unknown project"):
        env.attach_project("dev", "vanished")

    out = env.detach_project("dev", "proj")
    assert out == []
    assert env.detach_project("dev", "proj") == []  # idempotent
    # Stale cleanup: detaching an id that no longer exists anywhere still works.
    env.attach_project("dev", "proj")
    assert env.detach_project("dev", "old-removed-id") == ["proj"]
