"""Navigation aliases: link safety, idempotence, discovery isolation, doctor, rollback.

Phase 3 §9 coverage on the real filesystem (junctions are created with the shipped
`media_links` primitives — Windows runs are actual `CreateJunction`/mklink, not mocks).
Isolated registry/config per test; targets are temp dirs except where a registered
space id resolves through the registry.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from navig.spaces import navigation_links as nav
from navig.spaces import registry
from navig.spaces.media_links import is_link, read_link

WIN = sys.platform == "win32"


@pytest.fixture()
def isolated(tmp_path: Path, monkeypatch):
    f = tmp_path / "spaces.json"
    monkeypatch.setattr(registry, "_registry_file", lambda: f)
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    monkeypatch.delenv("NAVIG_HOME", raising=False)
    from navig.config import reset_config_manager

    reset_config_manager()
    yield tmp_path
    reset_config_manager()


def _mk_space(root: Path, sid: str) -> Path:
    p = root / f"{sid}-space"
    (p / ".navig").mkdir(parents=True)
    (p / ".navig" / "space.json").write_text(
        json.dumps({"id": sid, "name": sid.title(), "version": "1.0.0"}) + "\n", encoding="utf-8"
    )
    registry.register(p, id=sid, name=sid.title(), source="external", enabled=True)
    return p


def _env_with_spec(root: Path, spec: dict) -> Path:
    e = root / "dev-space"
    (e / ".navig").mkdir(parents=True, exist_ok=True)
    (e / ".navig" / "space.json").write_text(
        json.dumps({"id": "homelab-dev", "name": "Dev", "version": "1.0.0",
                    "kind": "environment", "navigation": spec}, indent=2) + "\n",
        encoding="utf-8",
    )
    registry.register(e, id="homelab-dev", name="Dev", source="external", enabled=True)
    return e


def _spec(areas: dict[str, str], projects: dict[str, str] | None = None) -> dict:
    return {"areas": areas, "projects": projects or {}}


# ── 1–3, 15: creation, project links, idempotence (real junctions) ────────────


def test_area_and_project_link_creation_then_idempotent(isolated: Path):
    android = _mk_space(isolated, "android")
    repo = isolated / "repos" / "tool"
    (repo / ".navig").mkdir(parents=True)
    env = _env_with_spec(isolated, _spec({"android": "android"}, {"tool": "tool"}))
    registry.register(repo, id="tool", name="Tool", source="external", enabled=True)

    states = {s.alias: s.state for s in nav.plan(env)}
    assert states == {"android": "missing", "tool": "missing"}

    rep = nav.reconcile(env, fix=True)
    assert [p.name for p in rep.created] == ["android", "tool"]
    assert rep.changed

    # Actual Windows junction / POSIX symlink behavior (item 15 + 1).
    for alias, target in (("areas/android", android), ("projects/tool", repo)):
        lp = env / alias
        assert is_link(lp), f"{lp} is not a link"
        assert Path(read_link(lp)).resolve() == target.resolve(), f"{alias} -> {read_link(lp)}"
    assert (env / "areas" / "android" / ".navig" / "space.json").is_file()  # target content reachable

    # Item 3: second run is a no-op; rollback record unchanged.
    again = nav.reconcile(env, fix=True)
    assert not again.changed and again.created == [] and again.repaired == []
    assert again.unchanged >= 2
    records = json.loads((env / ".navig" / "navigation-links.rollback.json").read_text(encoding="utf-8"))
    assert len(records) == 2  # exactly what we created, recorded once


def test_missing_targets_are_refused_not_created(isolated: Path):
    env = _env_with_spec(isolated, _spec({"phantom": "not-registered"}))
    rep = nav.reconcile(env, fix=True)
    assert not rep.created and not rep.changed
    assert any(s.state == "unresolved" and "unregistered-id" in s.detail for s in rep.refused)
    assert not (env / "areas" / "phantom").exists()


# ── 4, 7: broken and wrong-target detection + repair ──────────────────────────


def test_broken_link_detected_and_repaired(isolated: Path):
    android = _mk_space(isolated, "android")
    env = _env_with_spec(isolated, _spec({"android": "android"}))
    nav.reconcile(env, fix=True)

    # Sever the link: point it at a path that then disappears.
    from navig.spaces.media_links import remove_link

    ghost = isolated / "vanished-target"
    ghost.mkdir()
    remove_link(env / "areas" / "android")
    from navig.spaces.media_links import make_link  # noqa: PLC0415

    make_link(env / "areas" / "android", ghost)
    ghost.rmdir()

    states = {s.alias: s for s in nav.plan(env)}
    assert states["android"].state in ("broken", "wrong-target"), states["android"]

    rep = nav.reconcile(env, fix=True)
    assert [p.name for p in rep.repaired] == ["android"]
    assert Path(read_link(env / "areas" / "android")).resolve() == android.resolve()


def test_wrong_target_detected_and_repaired(isolated: Path):
    android = _mk_space(isolated, "android")
    other = _mk_space(isolated, "chrome")
    env = _env_with_spec(isolated, _spec({"android": "android"}))
    from navig.spaces.media_links import make_link  # noqa: PLC0415

    (env / "areas").mkdir(parents=True, exist_ok=True)
    make_link(env / "areas" / "android", other)  # wrong, but a link → managed

    states = {s.alias: s for s in nav.plan(env)}
    assert states["android"].state == "wrong-target"

    rep = nav.reconcile(env, fix=True)
    assert [p.name for p in rep.repaired] == ["android"]
    assert Path(read_link(env / "areas" / "android")).resolve() == android.resolve()


# ── 5–6: collision refusal vs preservation ────────────────────────────────────


def test_real_directory_collision_never_overwritten(isolated: Path):
    android = _mk_space(isolated, "android")
    env = _env_with_spec(isolated, _spec({"android": "android"}))
    planted = env / "areas" / "android"
    planted.mkdir(parents=True)
    (planted / "mine.txt").write_text("user content", encoding="utf-8")

    states = {s.alias: s for s in nav.plan(env)}
    assert states["android"].state == "collision"

    rep = nav.reconcile(env, fix=True)
    assert not rep.created and not rep.repaired
    assert any(s.alias == "android" for s in rep.refused)
    # byte-for-byte untouched
    assert (planted / "mine.txt").read_text(encoding="utf-8") == "user content"
    assert not is_link(planted)


def test_existing_valid_link_preserved(isolated: Path):
    android = _mk_space(isolated, "android")
    env = _env_with_spec(isolated, _spec({"android": "android"}))
    from navig.spaces.media_links import make_link  # noqa: PLC0415

    (env / "areas").mkdir(parents=True, exist_ok=True)
    make_link(env / "areas" / "android", android)

    rep = nav.reconcile(env, fix=True)
    assert rep.unchanged >= 1 and not rep.created and not rep.repaired and not rep.changed


# ── 8–9: cycles, self-links, canonical resolution ─────────────────────────────


def test_cycles_and_self_targets_refused(isolated: Path):
    # `inner` resolves to a REGISTERED dir that lives INSIDE the env — the cycle
    # branch must fire, not the unregistered branch.
    inner = _env_with_spec(isolated, _spec({"self": "homelab-dev", "inner": "inner-real"}))
    inside = inner / "inside" / "inner-real"  # inside the root, outside areas/+projects/
    (inside / ".navig").mkdir(parents=True)
    (inside / ".navig" / "space.json").write_text(
        json.dumps({"id": "inner-real", "name": "Inner", "version": "1.0.0"}) + "\n",
        encoding="utf-8",
    )
    registry.register(inside, id="inner-real", name="Inner", source="external", enabled=True)

    states = nav.plan(inner)
    assert {s.alias for s in states} == {"self", "inner"}
    assert all(s.state == "unresolved" and "cycle" in s.detail for s in states), states

    rep = nav.reconcile(inner, fix=True)
    assert not rep.created and rep.changed is False


def test_canonical_target_comes_from_registry(isolated: Path):
    android = _mk_space(isolated, "android")
    env = _env_with_spec(isolated, _spec({"droid": "android"}))  # alias ≠ folder name
    target, err = nav.resolve_alias("droid", "android")
    assert err is None and target == android  # registry path, not alias-derived


# ── 10–11: nested aliases never become Spaces or Projects ─────────────────────


def test_nested_aliases_stay_out_of_discovery(isolated: Path):
    android = _mk_space(isolated, "android")
    env = _env_with_spec(isolated, _spec({"android": "android"}))
    nav.reconcile(env, fix=True)

    from navig.spaces.resolver import discover_space_paths

    found = discover_space_paths(include_disabled=True)
    # The alias PATHS must be invisible to discovery: every entry resolves to a
    # canonical folder, never to env/areas/* or env/projects/* (the alias id
    # 'android' is a REAL registered space — its canonical folder, not the link).
    # Judged RELATIVE to the env, not by searching the absolute path for "/areas/":
    # a checkout that itself lives under .../projects/... matched on every entry, and on
    # Windows the same search never matched at all (2026-10-30, outpost).
    env_root = env.resolve()

    def _alias_container(cfg_path) -> bool:
        try:
            rel = Path(cfg_path).resolve().relative_to(env_root)
        except ValueError:
            return False  # a canonical space outside the env — that is the point
        return bool({"areas", "projects"} & set(rel.parts))

    leaked = [str(cfg.path) for cfg in found.values() if _alias_container(cfg.path)]
    assert not leaked, f"alias paths escaped discovery: {leaked}"
    assert "homelab-dev" in found
    assert str(found["homelab-dev"].path).endswith("dev-space")

    from navig.projects.model import list_projects

    ids = {p["id"] for p in list_projects(refresh=True)}
    # one project row per canonical space, no alias duplicates
    assert sum(1 for p in list_projects(refresh=True) if p["id"] == "android") <= 1


# ── 13: no cross-space exposure through the aliases ───────────────────────────


def test_alias_contents_do_not_leak_into_linked_context(isolated: Path, monkeypatch):
    """Another Space linking the env with include ** must NOT read through the
    aliases: linked-context containment resolves junction paths outside the env root."""
    android = _mk_space(isolated, "android")
    (android / "secret.md").write_text("area private", encoding="utf-8")
    env = _env_with_spec(isolated, _spec({"android": "android"}))
    nav.reconcile(env, fix=True)

    viewer = _mk_space(isolated, "viewer")
    from navig.spaces.relations import link

    link(viewer, "homelab-dev", include=("**",))
    monkeypatch.setenv("NAVIG_SPACE", "viewer")

    from navig.spaces.linked_context import gather_linked

    ctx = gather_linked(viewer, space_id="viewer")
    leaked = [i for i in ctx.items if i.space == "homelab-dev" and "android" in i.rel]
    assert leaked == [], f"alias content leaked across spaces: {leaked}"


# ── 14: tombstoned Spaces stay dead ───────────────────────────────────────────


def test_tombstoned_targets_never_resurrect(isolated: Path):
    gone = _mk_space(isolated, "ghost")
    registry.forget(str(gone))
    env = _env_with_spec(isolated, _spec({"ghost": "ghost"}))

    target, err = nav.resolve_alias("ghost", "ghost")
    assert target is None and err == "unregistered-id"
    states = {s.alias: s for s in nav.plan(env)}
    assert states["ghost"].state == "unresolved"
    rep = nav.reconcile(env, fix=True)
    assert not rep.created and not (env / "areas" / "ghost").exists()
    assert registry.is_forgotten(gone)  # still forgotten


# ── 16: rollback removes exactly what we created ──────────────────────────────


def test_rollback_removes_only_recorded_links(isolated: Path):
    android = _mk_space(isolated, "android")
    env = _env_with_spec(isolated, _spec({"android": "android"}))
    nav.reconcile(env, fix=True)
    assert is_link(env / "areas" / "android")

    # a user file next to the link and a real dir recorded-then-replaced stay safe
    (env / "projects").mkdir(exist_ok=True)
    (env / "projects" / "notes.md").write_text("mine", encoding="utf-8")

    removed = nav.rollback(env)
    assert [p.name for p in removed] == ["android"]
    assert not (env / "areas" / "android").exists()  # reparse point gone
    assert android.is_dir() and (android / ".navig").is_dir()  # TARGET untouched
    assert (env / "projects" / "notes.md").read_text(encoding="utf-8") == "mine"
    # record emptied → second rollback is a no-op
    assert nav.rollback(env) == []


# ── 17: manifest & membership preserved ───────────────────────────────────────


def test_navigation_manifest_field_is_additive_and_preserving(isolated: Path):
    from navig.spaces.space_manifest import set_manifest_field

    env = _env_with_spec(isolated, _spec({"android": "android"}))
    set_manifest_field(env, "projects", ["schema"])
    set_manifest_field(env, "interface", {"dashboard": "dev"})

    m = json.loads((env / ".navig" / "space.json").read_text(encoding="utf-8"))
    assert m["kind"] == "environment"
    assert m["projects"] == ["schema"] and m["interface"] == {"dashboard": "dev"}
    assert m["navigation"] == {"areas": {"android": "android"}, "projects": {}}  # untouched by other writes

    spec = nav.load_navigation(env)
    assert spec.areas == {"android": "android"} and spec.projects == {}
    # legacy manifest without navigation → empty spec, no crash
    legacy = _mk_space(isolated, "plain")
    assert nav.load_navigation(legacy).empty


# ── doctor integration (diagnosis default; --fix constrained) ──────────────────


def test_doctor_reports_navigation_and_fix_creates_links(isolated: Path):
    android = _mk_space(isolated, "android")
    env = _env_with_spec(isolated, _spec({"android": "android"}))
    registry.register(env, id="homelab-dev", name="Dev", source="external")

    from navig.commands.space import _apply_fix, _diagnose_space

    diag = _diagnose_space(env, "homelab-dev")
    nav_group = next(g for g in diag["groups"] if g["name"] == "Navigation")
    rows = {c["label"]: c for c in nav_group["checks"]}
    assert rows["android"]["status"] == "missing"
    assert rows["android"].get("action") == "fix"  # offered by the interactive menu
    assert not (env / "areas" / "android").exists()  # diagnosis wrote nothing

    added = _apply_fix(env, "homelab-dev")
    assert added >= 1
    assert is_link(env / "areas" / "android")
    diag2 = _diagnose_space(env, "homelab-dev")
    nav2 = next(g for g in diag2["groups"] if g["name"] == "Navigation")
    assert all(c["status"] in ("ok", "warn") for c in nav2["checks"] if c["label"] == "android")


def test_doctor_collision_is_warn_without_fix_action(isolated: Path):
    env = _env_with_spec(isolated, _spec({"android": "android"}))
    planted = env / "areas" / "android"
    planted.mkdir(parents=True)
    (planted / "x").write_text("y", encoding="utf-8")
    _mk_space(isolated, "android")

    from navig.commands.space import _diagnose_space

    diag = _diagnose_space(env, "homelab-dev")
    nav_group = next(g for g in diag["groups"] if g["name"] == "Navigation")
    row = next(c for c in nav_group["checks"] if c["label"] == "android")
    assert row["status"] == "warn" and row.get("action") == "none"  # never auto-fixed
