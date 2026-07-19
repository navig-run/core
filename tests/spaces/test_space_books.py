"""Per-space finance BOOK writer — `set_manifest_field` + `navig space books`.

Harbor's bizops ledger partitions on the active space manifest's `books` key
(`load_space_manifest(get_active_working_dir()).books`), but nothing in core ever
WROTE that key — the per-space ledger feature was reachable only by hand-editing
`space.json`. These cover the writer that closes the loop:

- `set_manifest_field` — the single safe manifest writer (set / clear / bootstrap
  a bare space / preserve unknown keys / refuse YAML + unreadable JSON).
- `navig space books` — show / set / clear, on the active space by default.
- the round-trip through `SpaceManifest.books` (the exact key Harbor reads).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.spaces.space_manifest import (
    ManifestNotWritable,
    load_space_manifest,
    set_manifest_field,
)


def _space(tmp_path: Path, manifest: dict | None = None, *, name: str = "co") -> Path:
    space = tmp_path / name
    (space / ".navig").mkdir(parents=True)
    if manifest is not None:
        (space / ".navig" / "space.json").write_text(json.dumps(manifest), encoding="utf-8")
    return space


# ── set_manifest_field ────────────────────────────────────────────────────────

def test_set_field_round_trips_and_preserves_keys(tmp_path):
    space = _space(tmp_path, {"id": "co", "theme": {"accent": "teal"}, "apps": ["finance"]})
    set_manifest_field(space, "books", "Company")
    data = json.loads((space / ".navig" / "space.json").read_text(encoding="utf-8"))
    assert data["books"] == "Company"
    assert data["theme"] == {"accent": "teal"}   # unknown keys survive
    assert data["apps"] == ["finance"]           # sibling allow-list untouched
    assert load_space_manifest(space).books == "Company"   # the key Harbor reads


def test_set_field_none_deletes_the_key(tmp_path):
    space = _space(tmp_path, {"id": "co", "books": "Company", "keep": 1})
    set_manifest_field(space, "books", None)
    data = json.loads((space / ".navig" / "space.json").read_text(encoding="utf-8"))
    assert "books" not in data          # cleared, not set to null
    assert data["keep"] == 1
    assert load_space_manifest(space).books is None


def test_set_field_bootstraps_bare_space(tmp_path):
    space = _space(tmp_path, None)  # bare .navig/, no manifest
    set_manifest_field(space, "books", "Personal", id_hint="my-space")
    data = json.loads((space / ".navig" / "space.json").read_text(encoding="utf-8"))
    assert data["id"] == "my-space"   # id_hint wins over the folder name
    assert data["books"] == "Personal"


def test_set_field_bootstrap_defaults_id_to_folder_name(tmp_path):
    space = _space(tmp_path, None, name="warehouse")
    set_manifest_field(space, "books", "W")
    assert json.loads((space / ".navig" / "space.json").read_text())["id"] == "warehouse"


def test_set_field_refuses_yaml(tmp_path):
    space = tmp_path / "yaml"
    (space / ".navig").mkdir(parents=True)
    (space / ".navig" / "space.yaml").write_text("id: yaml\n", encoding="utf-8")
    with pytest.raises(ManifestNotWritable):
        set_manifest_field(space, "books", "X")


def test_set_field_refuses_unreadable_json(tmp_path):
    space = tmp_path / "bad"
    (space / ".navig").mkdir(parents=True)
    (space / ".navig" / "space.json").write_text("{ not json", encoding="utf-8")
    with pytest.raises(ManifestNotWritable):
        set_manifest_field(space, "books", "X")


def test_set_field_refuses_non_object_json(tmp_path):
    space = tmp_path / "arr"
    (space / ".navig").mkdir(parents=True)
    (space / ".navig" / "space.json").write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(ManifestNotWritable):
        set_manifest_field(space, "books", "X")


# ── navig space books (CLI) ───────────────────────────────────────────────────

def _runner():
    from typer.testing import CliRunner

    return CliRunner()


def _activate(monkeypatch, space: Path) -> None:
    import navig.spaces.active as active

    monkeypatch.setattr(active, "get_active_working_dir", lambda cwd=None: space)


def test_cli_books_set_then_clear_on_active_space(tmp_path, monkeypatch):
    space = _space(tmp_path, {"id": "co"})
    _activate(monkeypatch, space)
    from navig.commands.space import space_app

    r = _runner()
    res = r.invoke(space_app, ["books", "Company"])
    assert res.exit_code == 0, res.output
    assert load_space_manifest(space).books == "Company"

    res = r.invoke(space_app, ["books", "--clear"])
    assert res.exit_code == 0, res.output
    assert load_space_manifest(space).books is None


def test_cli_books_show_does_not_write(tmp_path, monkeypatch):
    space = _space(tmp_path, {"id": "co", "books": "Company"})
    _activate(monkeypatch, space)
    before = (space / ".navig" / "space.json").read_text(encoding="utf-8")
    from navig.commands.space import space_app

    res = _runner().invoke(space_app, ["books"])
    assert res.exit_code == 0
    assert "Company" in res.output
    assert (space / ".navig" / "space.json").read_text(encoding="utf-8") == before  # read-only


def test_cli_books_target_space_by_path(tmp_path, monkeypatch):
    active = _space(tmp_path, {"id": "active"}, name="active")
    other = _space(tmp_path, {"id": "other"}, name="other")
    _activate(monkeypatch, active)
    from navig.commands.space import space_app

    res = _runner().invoke(space_app, ["books", "Warehouse", "--space", str(other)])
    assert res.exit_code == 0, res.output
    assert load_space_manifest(other).books == "Warehouse"
    assert load_space_manifest(active).books is None  # active space untouched


# ── navig space init --books (seed at creation) ───────────────────────────────

def _no_registry(monkeypatch) -> None:
    """Stop `space init` writing the test space into the real ~/.navig/spaces.json."""
    import navig.spaces.registry as sreg

    monkeypatch.setattr(sreg, "register", lambda *a, **k: None)


def test_cli_init_seeds_books(tmp_path, monkeypatch):
    _no_registry(monkeypatch)
    dest = tmp_path / "company"
    from navig.commands.space import space_app

    res = _runner().invoke(
        space_app, ["init", "company", "--path", str(dest), "--books", "Company", "--no-links"]
    )
    assert res.exit_code == 0, res.output
    assert load_space_manifest(dest).books == "Company"   # the key Harbor reads


def test_cli_init_without_books_leaves_default(tmp_path, monkeypatch):
    _no_registry(monkeypatch)
    dest = tmp_path / "plain"
    from navig.commands.space import space_app

    res = _runner().invoke(space_app, ["init", "plain", "--path", str(dest), "--no-links"])
    assert res.exit_code == 0, res.output
    assert load_space_manifest(dest).books is None   # no book unless asked


def test_cli_init_dry_run_books_writes_nothing(tmp_path, monkeypatch):
    _no_registry(monkeypatch)
    dest = tmp_path / "preview"
    from navig.commands.space import space_app

    res = _runner().invoke(
        space_app,
        ["init", "preview", "--path", str(dest), "--books", "Company", "--dry-run"],
    )
    assert res.exit_code == 0, res.output
    assert not (dest / ".navig" / "space.json").exists()   # dry-run wrote nothing
