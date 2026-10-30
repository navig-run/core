"""One id, one space — a folder that merely shares a NAME with an existing space is refused.

`registry.register` keys on PATH. A new folder whose name matches an existing space's id
therefore appended a SECOND entry with the same id, and `_find(id)` answered whichever came
first: `cd ~/projects/homelab && navig space init` silently made `navig space use homelab`
ambiguous and listed two `homelab` rows. Every user-facing entry point that registers —
`space init`, `navig wire`, `space doctor --fix` — now asks `id_taken_by_another_path`
first and refuses (or, for wire, reports) with the other path named. `space doctor`'s
Registry row says who holds the id.

Everything here runs against an isolated registry (NAVIG_CONFIG_DIR → tmp).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from navig.commands import space as space_cmd
from navig.spaces import registry


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch) -> Path:
    config = tmp_path / "cfg"
    (config / "spaces").mkdir(parents=True)
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(config))
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    # keep every write inside tmp: no links, no scaffolding side effects beyond the folder
    monkeypatch.setattr(space_cmd, "_link_space_roots", lambda p: [])
    monkeypatch.setattr(space_cmd, "_link_space_capabilities", lambda p: [])
    return tmp_path


def _existing(root: Path, space_id: str) -> Path:
    p = root / f"{space_id}-space"
    (p / ".navig").mkdir(parents=True)
    registry.register(p, id=space_id, name=space_id, source="external")
    return p


# ── the registry primitive ───────────────────────────────────────────────────


def test_id_taken_names_the_other_path_and_not_itself(isolated: Path) -> None:
    other = _existing(isolated, "homelab")
    assert registry.id_taken_by_another_path("homelab", isolated / "elsewhere") == str(other.resolve())
    assert registry.id_taken_by_another_path("homelab", other) is None  # same path: re-register is fine
    assert registry.id_taken_by_another_path("unclaimed", isolated / "x") is None


def test_the_old_behaviour_would_have_made_two_rows(isolated: Path) -> None:
    """register() now REFUSES a second path under one id — the layer guarantee behind
    the caller guards (was: "register() itself still keys on path… if register ever
    starts refusing, this test (and the guards) can be simplified")."""
    other = _existing(isolated, "homelab")
    returned = registry.register(isolated / "second", id="homelab", name="homelab",
                                 source="external")
    rows = [e for e in registry.load_registry()["spaces"] if e["id"] == "homelab"]
    assert len(rows) == 1
    # the refusal is honest: the holder's row comes back, not a phantom new one
    assert registry._norm(returned["path"]) == registry._norm(other)


def test_an_alias_of_a_held_id_is_taken_too(isolated: Path) -> None:
    """`project-space` canonicalises to `project`: discovery files both under one id,
    so an exact-string check let the alias through and made the lookup ambiguous."""
    other = _existing(isolated, "project")
    elsewhere = isolated / "elsewhere"
    assert registry.id_taken_by_another_path("project-space", elsewhere) == str(other.resolve())
    assert registry.id_taken_by_another_path("Project", elsewhere) == str(other.resolve())
    assert registry.id_taken_by_another_path("project-space", other) is None


def test_register_refuses_an_alias_of_a_held_id(isolated: Path) -> None:
    other = _existing(isolated, "project")
    registry.register(isolated / "second", id="project-space", name="project-space",
                      source="external")
    rows = registry.load_registry()["spaces"]
    assert [registry._norm(e["path"]) for e in rows] == [registry._norm(other)]


# ── space init ───────────────────────────────────────────────────────────────


def test_init_in_a_folder_named_like_an_existing_space_is_refused(isolated: Path) -> None:
    other = _existing(isolated, "homelab")
    here = isolated / "homelab"
    here.mkdir()
    r = CliRunner().invoke(space_cmd.space_app, ["init", "--path", str(here)])
    assert r.exit_code == 1, r.output
    assert "already the id of another space" in r.output
    assert str(other.resolve()) in r.output
    assert "navig space init <other-name>" in r.output
    assert not (here / ".navig").exists(), "nothing may be written before the refusal"
    assert len([e for e in registry.load_registry()["spaces"] if e["id"] == "homelab"]) == 1


def test_init_with_an_explicit_taken_name_is_refused_too(isolated: Path) -> None:
    _existing(isolated, "homelab")
    here = isolated / "proj"
    here.mkdir()
    r = CliRunner().invoke(space_cmd.space_app, ["init", "homelab", "--path", str(here)])
    assert r.exit_code == 1, r.output
    assert "already the id of another space" in r.output


def test_dry_run_reports_the_collision_instead_of_hiding_it(isolated: Path) -> None:
    _existing(isolated, "homelab")
    here = isolated / "homelab"
    here.mkdir()
    r = CliRunner().invoke(space_cmd.space_app, ["init", "--path", str(here), "--dry-run"])
    assert r.exit_code == 1, r.output
    assert "dry run" in r.output and "already the id" in r.output


def test_re_initialising_the_same_space_is_not_a_collision(isolated: Path) -> None:
    other = _existing(isolated, "homelab")
    r = CliRunner().invoke(space_cmd.space_app, ["init", "homelab", "--path", str(other), "--dry-run"])
    assert r.exit_code == 0, r.output


@pytest.mark.parametrize("empty", ["", "   "])
def test_an_explicitly_empty_name_is_refused_not_inferred(
    isolated: Path, monkeypatch, empty: str
) -> None:
    """`navig space init "$SPACE_NAME"` with the variable unset passes "" — that is a bad
    name, not an omitted one, and must never scaffold the invocation directory."""
    here = isolated / "proj"
    here.mkdir()
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(here))
    monkeypatch.chdir(here)
    r = CliRunner().invoke(space_cmd.space_app, ["init", empty])
    assert r.exit_code != 0, r.output
    assert not (here / ".navig").exists()
    assert list(here.iterdir()) == []


def test_an_unrelated_name_still_registers(isolated: Path) -> None:
    _existing(isolated, "homelab")
    here = isolated / "garden"
    here.mkdir()
    r = CliRunner().invoke(space_cmd.space_app, ["init", "--path", str(here)])
    assert r.exit_code == 0, r.output
    assert any(e["id"] == "garden" for e in registry.load_registry()["spaces"])


# ── wire and doctor --fix ────────────────────────────────────────────────────


def test_wire_does_the_repair_but_refuses_the_duplicate_registration(isolated: Path) -> None:
    from navig.commands import wire as wire_cmd

    other = _existing(isolated, "homelab")
    here = isolated / "homelab"
    here.mkdir()
    app = typer.Typer()
    app.command()(wire_cmd.wire_command)
    r = CliRunner().invoke(app, [str(here)])
    assert r.exit_code == 0, r.output  # the folder is still wired
    assert (here / ".navig").is_dir()
    assert "NOT registered" in r.output and str(other.resolve()) in r.output
    assert len([e for e in registry.load_registry()["spaces"] if e["id"] == "homelab"]) == 1


def test_doctor_fix_never_creates_a_duplicate_id(isolated: Path) -> None:
    _existing(isolated, "homelab")
    here = isolated / "homelab"
    (here / ".navig").mkdir(parents=True)
    space_cmd._apply_fix(here, "homelab")
    assert len([e for e in registry.load_registry()["spaces"] if e["id"] == "homelab"]) == 1


def test_doctor_names_who_holds_the_id(isolated: Path) -> None:
    other = _existing(isolated, "homelab")
    here = isolated / "homelab"
    (here / ".navig").mkdir(parents=True)
    rows = space_cmd._diagnose_space(here, "homelab")["groups"]
    row = next(c for g in rows if g["name"] == "Registry" for c in g["checks"])
    assert row["status"] == "warn" and row["action"] == "manual"
    assert str(other.resolve()) in row["detail"]


def test_doctor_names_the_holder_even_when_discovery_lists_the_folder(
    isolated: Path, monkeypatch
) -> None:
    """The cwd project is listed by discovery even when its id is held elsewhere and no row
    was written for it; that used to read as "registered" and skip the collision check."""
    from types import SimpleNamespace

    other = _existing(isolated, "homelab")
    here = isolated / "homelab"
    (here / ".navig").mkdir(parents=True)
    monkeypatch.setattr(
        "navig.spaces.resolver.discover_space_paths",
        lambda **k: {"homelab": SimpleNamespace(path=here)},
    )
    rows = space_cmd._diagnose_space(here, "homelab")["groups"]
    row = next(c for g in rows if g["name"] == "Registry" for c in g["checks"])
    assert row["status"] == "warn" and row["action"] == "manual"
    assert str(other.resolve()) in row["detail"]


def test_a_skipped_registry_check_is_never_a_green_tick(isolated: Path, monkeypatch) -> None:
    """`chk(True, "registry", "check skipped")` rendered ✓ over a check that never ran."""
    def boom(**k):
        raise RuntimeError("registry unreadable")

    monkeypatch.setattr("navig.spaces.resolver.discover_space_paths", boom)
    here = isolated / "x"
    (here / ".navig").mkdir(parents=True)
    rows = space_cmd._diagnose_space(here, "x")["groups"]
    row = next(c for g in rows if g["name"] == "Registry" for c in g["checks"])
    assert row["status"] != "ok"
    assert "could not verify" in row["detail"]


def test_space_register_fails_when_the_registry_is_unreadable(isolated: Path, monkeypatch) -> None:
    """It printed "Registered" for an entry stored nowhere while spaces.json was locked."""
    from navig.core.json_io import JsonReadError

    here = isolated / "locked"
    (here / ".navig" / "plans").mkdir(parents=True)
    (here / ".navig" / "plans" / "current.md").write_text("# plan\n", encoding="utf-8")

    def _raise_lock(*_a, **_k):
        raise JsonReadError("simulated transient lock")

    with monkeypatch.context() as m:
        m.setattr(registry, "load_json_for_update", _raise_lock)
        r = CliRunner().invoke(space_cmd.space_app, ["register", str(here)])
    assert r.exit_code == 1, r.output
    assert "Registered space" not in r.output
    assert not [e for e in registry.load_registry()["spaces"] if e["id"] == "locked"]
