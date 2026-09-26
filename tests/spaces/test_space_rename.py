"""`navig space rename <space> <new-id>` — the id changes everywhere it lives, in one move.

A space's id is written in three places: `.navig/space.json` (the source of truth —
discovery re-derives everything else from it), the registry row in `spaces.json`, and the
active-space pointer when it is the space you stand in. Editing one by hand leaves the
other two pointing at a name that no longer exists. `rename` writes the manifest FIRST
(so a failed registry write self-heals on the next discovery, not the reverse), re-keys
the registry row, moves the pointer, and follows labels that were DERIVED from the old
id (`display_name` as init writes it, `NAVIG.md`'s `space:` frontmatter) while leaving a
label someone chose alone. It never moves the folder and never adds or drops a row.

`space register` is also covered here: it was the one register site that skipped the
one-id-one-space check and filed every folder as "external".

Everything runs against an isolated registry (NAVIG_CONFIG_DIR → tmp).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands import space as space_cmd
from navig.spaces import registry
from navig.spaces.space_manifest import load_space_manifest


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch) -> Path:
    config = tmp_path / "cfg"
    (config / "spaces").mkdir(parents=True)
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(config))
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(tmp_path))
    monkeypatch.delenv("NAVIG_SPACE", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(space_cmd, "_link_space_roots", lambda p: [])
    monkeypatch.setattr(space_cmd, "_link_space_capabilities", lambda p: [])
    return tmp_path


def _space(
    root: Path, space_id: str, *, register: bool = True, folder: str | None = None, **manifest
) -> Path:
    """A space folder shaped like `space init` leaves it: id + derived display_name."""
    p = root / (folder or f"{space_id}-dir")
    (p / ".navig").mkdir(parents=True)
    data = {"id": space_id, "display_name": space_cmd._display_name_for(space_id), **manifest}
    (p / ".navig" / "space.json").write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    (p / "NAVIG.md").write_text(
        f"---\nspace: {space_id}\n---\n# {space_id}\n\nbody mentions space: {space_id} too\n",
        encoding="utf-8",
    )
    if register:
        registry.register(p, id=space_id, name=space_id, source="external")
    return p


def _run(*args: str):
    return CliRunner().invoke(space_cmd.space_app, ["rename", *args])


def _row(path: Path) -> dict | None:
    return registry.entry_for(path)


def _squash(text: str) -> str:
    """Rich folds a long path at the terminal width; compare with whitespace removed."""
    return "".join(str(text).split())


# ── the registry primitive ───────────────────────────────────────────────────


def test_registry_rename_rekeys_by_path_and_follows_a_derived_name(isolated: Path) -> None:
    p = _space(isolated, "homelab")
    assert registry.rename(p, "lab") == "homelab"
    row = _row(p)
    assert row is not None and row["id"] == "lab" and row["name"] == "lab"
    assert [e["id"] for e in registry.load_registry()["spaces"]] == ["lab"], (
        "no row added or dropped"
    )


def test_registry_rename_keeps_a_chosen_display_name(isolated: Path) -> None:
    p = _space(isolated, "homelab")
    registry.register(p, id="homelab", name="The Lab", source="external")
    registry.rename(p, "lab")
    row = _row(p)
    assert row is not None and row["id"] == "lab" and row["name"] == "The Lab"


def test_registry_rename_corrects_a_drifted_id(isolated: Path) -> None:
    """Keyed on PATH: a row whose id had drifted from the manifest is still the row."""
    p = _space(isolated, "homelab")
    registry.register(p, id="homelab-space", name="homelab-space", source="external")
    assert registry.rename(p, "lab") == "homelab-space"
    assert _row(p)["id"] == "lab"


def test_registry_rename_refuses_an_id_held_by_another_path(isolated: Path) -> None:
    p = _space(isolated, "homelab")
    other = _space(isolated, "office")
    with pytest.raises(ValueError, match="already the id of"):
        registry.rename(p, "office")
    assert _row(p)["id"] == "homelab" and _row(other)["id"] == "office", "nothing written"


def test_registry_rename_of_an_unregistered_path_is_none_and_writes_nothing(isolated: Path) -> None:
    p = _space(isolated, "homelab", register=False)
    assert registry.rename(p, "lab") is None
    assert registry.load_registry()["spaces"] == []


# ── the command ──────────────────────────────────────────────────────────────


def test_rename_by_id_moves_manifest_registry_and_derived_labels(isolated: Path) -> None:
    p = _space(isolated, "homelab")
    r = _run("homelab", "lab")
    assert r.exit_code == 0, r.output
    assert "Renamed space 'homelab' → 'lab'" in r.output
    m = load_space_manifest(p)
    assert m.resolved_id == "lab"
    assert m.get("display_name") == "Lab", "init's derived label follows the id"
    assert _row(p)["id"] == "lab"
    md = (p / "NAVIG.md").read_text(encoding="utf-8")
    assert md.startswith("---\nspace: lab\n---\n"), "frontmatter label follows"
    assert "body mentions space: homelab too" in md, "only the frontmatter line is touched"
    assert "folder stays" in r.output, "the folder is never moved"
    assert p.is_dir()


def test_rename_by_path_works_for_an_unregistered_space_and_says_so(isolated: Path) -> None:
    p = _space(isolated, "homelab", register=False)
    r = _run(str(p), "lab")
    assert r.exit_code == 0, r.output
    assert load_space_manifest(p).resolved_id == "lab"
    assert registry.load_registry()["spaces"] == [], "rename never adds a row"
    assert "not registered" in r.output
    assert _squash(f"navig space register {p}") in _squash(r.output)


def test_rename_keeps_a_chosen_display_name(isolated: Path) -> None:
    p = _space(isolated, "homelab", display_name="Kitchen Rack")
    r = _run("homelab", "lab")
    assert r.exit_code == 0, r.output
    m = load_space_manifest(p)
    assert m.resolved_id == "lab" and m.get("display_name") == "Kitchen Rack"


def test_rename_moves_the_active_space_pointer_only_when_it_is_this_space(isolated: Path) -> None:
    p = _space(isolated, "homelab")
    _space(isolated, "office")
    space_cmd._set_active_space("homelab")
    r = _run("homelab", "lab")
    assert r.exit_code == 0, r.output
    assert space_cmd.resolve_active_space() == "lab"
    assert "active space pointer" in r.output
    # …and not when another space is active
    space_cmd._set_active_space("office")
    r = _run("lab", "rack")
    assert r.exit_code == 0, r.output
    assert space_cmd.resolve_active_space() == "office"
    assert "active space pointer" not in r.output
    assert load_space_manifest(p).resolved_id == "rack"


def test_rename_refuses_an_id_another_space_holds_and_writes_nothing(isolated: Path) -> None:
    p = _space(isolated, "homelab")
    other = _space(isolated, "office")
    r = _run("homelab", "office")
    assert r.exit_code == 1, r.output
    assert "already the id of another space" in r.output
    assert str(other.resolve()) in r.output
    assert _squash(f"navig space rename {p.resolve()} <other-id>") in _squash(r.output)
    assert load_space_manifest(p).resolved_id == "homelab"
    assert _row(p)["id"] == "homelab"
    assert (p / "NAVIG.md").read_text(encoding="utf-8").startswith("---\nspace: homelab\n")


def test_rename_dry_run_reports_the_plan_and_writes_nothing(isolated: Path) -> None:
    p = _space(isolated, "homelab")
    space_cmd._set_active_space("homelab")
    r = _run("homelab", "lab", "--dry-run")
    assert r.exit_code == 0, r.output
    for line in (
        "space.json id: homelab → lab",
        "display_name: Homelab → Lab",
        "NAVIG.md frontmatter",
        "registry row re-keyed",
        "active space pointer",
        "nothing was written",
    ):
        assert line in r.output, line
    assert load_space_manifest(p).resolved_id == "homelab"
    assert _row(p)["id"] == "homelab"
    assert space_cmd.resolve_active_space() == "homelab"


def test_rename_dry_run_still_reports_a_taken_id(isolated: Path) -> None:
    _space(isolated, "homelab")
    _space(isolated, "office")
    r = _run("homelab", "office", "--dry-run")
    assert r.exit_code == 1, r.output
    assert "already the id of another space" in r.output
    assert "dry run" in r.output


def test_rename_to_the_same_id_is_a_no_op(isolated: Path) -> None:
    p = _space(isolated, "homelab")
    before = (p / ".navig" / "space.json").read_text(encoding="utf-8")
    r = _run("homelab", "homelab")
    assert r.exit_code == 0, r.output
    assert "nothingtodo" in _squash(r.output)  # follows a tmp path Rich may fold mid-phrase
    assert (p / ".navig" / "space.json").read_text(encoding="utf-8") == before


def test_rename_says_when_the_new_id_normalises(isolated: Path) -> None:
    p = _space(isolated, "homelab")
    r = _run("homelab", "lab-space")
    assert r.exit_code == 0, r.output
    assert "normalises to 'lab'" in r.output
    assert load_space_manifest(p).resolved_id == "lab"


def test_rename_rejects_a_bad_slug_before_touching_anything(isolated: Path) -> None:
    p = _space(isolated, "homelab")
    r = _run("homelab", "Not A Slug!")
    assert r.exit_code != 0
    assert "Invalid space name" in r.output
    assert load_space_manifest(p).resolved_id == "homelab"


def test_rename_of_something_that_is_not_a_space_is_refused(isolated: Path) -> None:
    plain = isolated / "plain"
    plain.mkdir()
    r = _run(str(plain), "lab")
    assert r.exit_code == 1, r.output
    assert "Not a space" in r.output
    r = _run("no-such-space", "lab")
    assert r.exit_code == 1, r.output
    assert "Not a space" in r.output


def test_rename_refuses_a_yaml_manifest_and_leaves_the_registry_alone(isolated: Path) -> None:
    p = isolated / "yaml-dir"
    (p / ".navig").mkdir(parents=True)
    (p / ".navig" / "space.yaml").write_text("id: homelab\n", encoding="utf-8")
    registry.register(p, id="homelab", name="homelab", source="external")
    r = _run(str(p), "lab")
    assert r.exit_code == 1, r.output
    assert "Could not write the manifest" in r.output
    assert _row(p)["id"] == "homelab", "manifest failed first, so the registry was never touched"


# ── space register: the last unguarded register site ─────────────────────────


def test_register_refuses_a_folder_whose_id_another_space_holds(isolated: Path) -> None:
    other = _space(isolated, "homelab")
    second = _space(isolated, "homelab", register=False, folder="elsewhere")  # same manifest id
    r = CliRunner().invoke(space_cmd.space_app, ["register", str(second)])
    assert r.exit_code == 1, r.output
    assert "already the id of another space" in r.output
    assert str(other.resolve()) in r.output
    assert "navig space rename" in r.output
    assert len([e for e in registry.load_registry()["spaces"] if e["id"] == "homelab"]) == 1


def test_register_files_a_spaces_root_resident_as_root_not_external(
    isolated: Path, monkeypatch
) -> None:
    from navig.platform import paths

    root = paths.spaces_dir()
    p = root / "rack"
    (p / ".navig").mkdir(parents=True)
    (p / ".navig" / "space.json").write_text('{"id": "rack"}\n', encoding="utf-8")
    r = CliRunner().invoke(space_cmd.space_app, ["register", str(p)])
    assert r.exit_code == 0, r.output
    assert _row(p)["source"] == "root"
    ext = _space(isolated, "office", register=False)
    r = CliRunner().invoke(space_cmd.space_app, ["register", str(ext)])
    assert r.exit_code == 0, r.output
    assert _row(ext)["source"] == "external"
