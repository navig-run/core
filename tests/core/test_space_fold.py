"""Pins `navig space fold` — the demotion that keeps a sub-space out of the registry.

A folder holding a ``.navig/`` is claimed as a *top-level* space the moment anyone cds
into it, and the claim is permanent: ``discover_space_paths`` calls
``registry.ensure_registered`` **before** it filters on enabled, so ``space disable``
cannot take the id back. That is fine for a real space and wrong for a deliberate
sub-space (``<space>/spaces/<persona>/``), which wants a working ``.navig/`` without ever
owning a global id.

Folding writes ``.navig/.folded``; ``resolver._record`` skips a folded entry before it can
register. These tests pin both halves — invisible to discovery, still usable in place —
plus the audit finding that surfaces an unfolded sub-space before it leaks.

NOTE ``temp_dir`` (system temp), never ``tmp_path``: pytest's basetemp here lives inside
the checkout, so ``tmp_path`` has a ``.navig`` ancestor and would make every one of these
assertions meaningless. See ``test_tmp_dir_repo_boundary.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def _make_space(root: Path, name: str, *, with_plans: bool = True) -> Path:
    space = root / name
    (space / ".navig").mkdir(parents=True, exist_ok=True)
    if with_plans:
        (space / ".navig" / "plans").mkdir(parents=True, exist_ok=True)
    return space


@pytest.fixture
def isolated_registry(temp_dir, monkeypatch):
    """Point NAVIG's config dir at a scratch dir so spaces.json is never the real one."""
    cfg = Path(temp_dir) / "navig-config"
    cfg.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(cfg))
    return cfg


def _registered_ids(cfg: Path) -> list[str]:
    reg = cfg / "spaces.json"
    if not reg.is_file():
        return []
    data = json.loads(reg.read_text(encoding="utf-8"))
    return [e.get("id") for e in data.get("spaces", [])]


# ── the marker itself ────────────────────────────────────────────────────────


def test_is_folded_detects_marker(temp_dir):
    from navig.spaces.resolver import FOLD_MARKER, is_folded

    space = _make_space(Path(temp_dir), "persona")
    assert is_folded(space) is False
    (space / ".navig" / FOLD_MARKER).write_text("{}", encoding="utf-8")
    assert is_folded(space) is True


def test_is_folded_is_false_for_missing_path(temp_dir):
    """Runs on every discovery hit — an unreadable path is 'not folded', never a crash."""
    from navig.spaces.resolver import is_folded

    assert is_folded(Path(temp_dir) / "does-not-exist") is False


def test_fold_marker_must_be_a_file_not_a_dir(temp_dir):
    from navig.spaces.resolver import FOLD_MARKER, is_folded

    space = _make_space(Path(temp_dir), "persona")
    (space / ".navig" / FOLD_MARKER).mkdir()
    assert is_folded(space) is False


# ── discovery ────────────────────────────────────────────────────────────────


def test_unfolded_subspace_is_discovered_and_registered(temp_dir, isolated_registry):
    """The behaviour fold exists to prevent — pinned so the fix stays meaningful."""
    from navig.spaces.resolver import discover_space_paths

    root = Path(temp_dir) / "spaces"
    parent = _make_space(root, "presence")
    sub = _make_space(parent / "spaces", "persona")

    found = discover_space_paths(cwd=sub)

    assert "persona" in found
    assert "persona" in _registered_ids(isolated_registry)


def test_folded_subspace_is_not_discovered(temp_dir, isolated_registry):
    from navig.spaces.resolver import FOLD_MARKER, discover_space_paths

    root = Path(temp_dir) / "spaces"
    parent = _make_space(root, "presence")
    sub = _make_space(parent / "spaces", "persona")
    (sub / ".navig" / FOLD_MARKER).write_text("{}", encoding="utf-8")

    found = discover_space_paths(cwd=sub)

    assert "persona" not in found


def test_folded_subspace_never_reaches_the_registry(temp_dir, isolated_registry):
    """The guard sits before ensure_registered — nothing is written, not merely hidden."""
    from navig.spaces.resolver import FOLD_MARKER, discover_space_paths

    root = Path(temp_dir) / "spaces"
    parent = _make_space(root, "presence")
    sub = _make_space(parent / "spaces", "persona")
    (sub / ".navig" / FOLD_MARKER).write_text("{}", encoding="utf-8")

    discover_space_paths(cwd=sub)

    assert "persona" not in _registered_ids(isolated_registry)


def test_folded_space_hidden_even_with_include_disabled(temp_dir, isolated_registry):
    """Folding is stronger than disabling: `include_disabled` must not resurrect it."""
    from navig.spaces.resolver import FOLD_MARKER, discover_space_paths

    root = Path(temp_dir) / "spaces"
    parent = _make_space(root, "presence")
    sub = _make_space(parent / "spaces", "persona")
    (sub / ".navig" / FOLD_MARKER).write_text("{}", encoding="utf-8")

    found = discover_space_paths(cwd=sub, include_disabled=True)

    assert "persona" not in found


def test_folded_space_in_a_scanned_root_is_hidden(temp_dir, isolated_registry, monkeypatch):
    """The guard is in `_record`, so it covers the container scan too — not just cwd."""
    from navig.spaces import resolver

    root = Path(temp_dir) / "spaces"
    root.mkdir(parents=True, exist_ok=True)
    kept = _make_space(root, "kept")
    hidden = _make_space(root, "hidden")
    (hidden / ".navig" / resolver.FOLD_MARKER).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(resolver, "spaces_roots", lambda: [root])

    found = resolver.discover_space_paths(cwd=kept)

    assert "kept" in found
    assert "hidden" not in found


def test_folded_space_still_resolves_as_a_capability_root(temp_dir, isolated_registry):
    """Folding hides a space from lists — it must NOT stop the folder working in place.

    This is how the persona sub-spaces are used day to day: cd in, and navig still finds
    that `.navig/` as the project root.
    """
    from navig.spaces.resolver import FOLD_MARKER, _find_project_navig_root

    root = Path(temp_dir) / "spaces"
    parent = _make_space(root, "presence")
    sub = _make_space(parent / "spaces", "persona")
    (sub / ".navig" / FOLD_MARKER).write_text("{}", encoding="utf-8")

    assert _find_project_navig_root(sub.resolve()) == sub / ".navig"


# ── the CLI verbs ────────────────────────────────────────────────────────────


def test_fold_command_writes_marker_and_releases_the_id(temp_dir, isolated_registry):
    from navig.commands.space import space_fold
    from navig.spaces import registry as space_registry
    from navig.spaces.resolver import FOLD_MARKER

    space = _make_space(Path(temp_dir), "persona")
    space_registry.register(space, id="persona", name="persona", source="external", enabled=True)
    assert "persona" in _registered_ids(isolated_registry)

    space_fold(space)

    assert (space / ".navig" / FOLD_MARKER).is_file()
    assert "persona" not in _registered_ids(isolated_registry)


def test_fold_marker_records_why_and_how_to_undo(temp_dir, isolated_registry):
    """A bare marker is a mystery six months later — it carries its own provenance."""
    from navig.commands.space import space_fold
    from navig.spaces.resolver import FOLD_MARKER

    space = _make_space(Path(temp_dir), "persona")
    space_fold(space)

    stamp = json.loads((space / ".navig" / FOLD_MARKER).read_text(encoding="utf-8"))
    assert stamp["folded_at"]
    assert "unfold" in stamp["note"]


def test_fold_is_idempotent(temp_dir, isolated_registry):
    from navig.commands.space import space_fold

    space = _make_space(Path(temp_dir), "persona")
    space_fold(space)
    space_fold(space)  # must not raise


def test_unfold_restores_discovery(temp_dir, isolated_registry):
    from navig.commands.space import space_fold, space_unfold
    from navig.spaces.resolver import discover_space_paths

    root = Path(temp_dir) / "spaces"
    parent = _make_space(root, "presence")
    sub = _make_space(parent / "spaces", "persona")

    space_fold(sub)
    assert "persona" not in discover_space_paths(cwd=sub)

    space_unfold(sub)
    assert "persona" in discover_space_paths(cwd=sub)


def test_unfold_on_an_unfolded_space_is_a_no_op(temp_dir, isolated_registry):
    from navig.commands.space import space_unfold

    space = _make_space(Path(temp_dir), "persona")
    space_unfold(space)  # must not raise


def test_fold_rejects_a_folder_that_is_not_a_space(temp_dir, isolated_registry):
    import typer

    from navig.commands.space import space_fold

    plain = Path(temp_dir) / "just-a-folder"
    plain.mkdir()

    with pytest.raises(typer.Exit) as exc:
        space_fold(plain)
    assert exc.value.exit_code == 2  # usage class, per the exit-honesty contract


# ── audit ────────────────────────────────────────────────────────────────────


def test_audit_flags_an_unfolded_subspace(temp_dir, isolated_registry, monkeypatch):
    from navig.commands import space as space_cmd

    root = Path(temp_dir) / "spaces"
    root.mkdir(parents=True, exist_ok=True)
    parent = _make_space(root, "presence")
    _make_space(parent / "spaces", "persona")
    monkeypatch.setattr(space_cmd, "_read_workspace_id", lambda _p: None)
    monkeypatch.setattr("navig.spaces.resolver.spaces_roots", lambda: [root])

    findings = space_cmd._audit_spaces()

    flagged = {s["name"] for s in findings["unfolded_subspaces"]}
    assert "persona" in flagged
    assert findings["issue_count"] >= 1


def test_audit_is_clean_once_the_subspace_is_folded(temp_dir, isolated_registry, monkeypatch):
    from navig.commands import space as space_cmd
    from navig.spaces.resolver import FOLD_MARKER

    root = Path(temp_dir) / "spaces"
    root.mkdir(parents=True, exist_ok=True)
    parent = _make_space(root, "presence")
    sub = _make_space(parent / "spaces", "persona")
    (sub / ".navig" / FOLD_MARKER).write_text("{}", encoding="utf-8")
    monkeypatch.setattr(space_cmd, "_read_workspace_id", lambda _p: None)
    monkeypatch.setattr("navig.spaces.resolver.spaces_roots", lambda: [root])

    findings = space_cmd._audit_spaces()

    assert findings["unfolded_subspaces"] == []


def test_audit_ignores_a_bare_subspace_that_would_not_surface(temp_dir, isolated_registry, monkeypatch):
    """A `.navig/` with no manifest and no plans is already invisible — not a finding."""
    from navig.commands import space as space_cmd

    root = Path(temp_dir) / "spaces"
    root.mkdir(parents=True, exist_ok=True)
    parent = _make_space(root, "presence")
    _make_space(parent / "spaces", "bare", with_plans=False)
    monkeypatch.setattr(space_cmd, "_read_workspace_id", lambda _p: None)
    monkeypatch.setattr("navig.spaces.resolver.spaces_roots", lambda: [root])

    findings = space_cmd._audit_spaces()

    assert findings["unfolded_subspaces"] == []


# ── the hand-rolled `.navig.folded/` convention (predates the verb) ──────────


def test_renamed_navig_folded_dir_counts_as_folded(temp_dir):
    """homelab-space/spaces/{iot,research} were folded by hand this way."""
    from navig.spaces.resolver import FOLDED_DIR, is_folded

    space = Path(temp_dir) / "iot"
    (space / FOLDED_DIR / "inbox").mkdir(parents=True)
    assert is_folded(space) is True


def test_hard_folded_space_in_a_scanned_root_is_hidden(temp_dir, isolated_registry, monkeypatch):
    """`_scan_container` records every child dir without a space check — so a hand-folded
    space sitting directly in a spaces root would otherwise still be registered."""
    from navig.spaces import resolver

    root = Path(temp_dir) / "spaces"
    root.mkdir(parents=True, exist_ok=True)
    kept = _make_space(root, "kept")
    hidden = root / "hidden"
    (hidden / resolver.FOLDED_DIR).mkdir(parents=True)
    monkeypatch.setattr(resolver, "spaces_roots", lambda: [root])

    found = resolver.discover_space_paths(cwd=kept)

    assert "kept" in found
    assert "hidden" not in found
    assert "hidden" not in _registered_ids(isolated_registry)


def test_unfold_restores_a_renamed_navig_folded_dir(temp_dir, isolated_registry):
    from navig.commands.space import space_unfold
    from navig.spaces.resolver import FOLDED_DIR

    space = Path(temp_dir) / "iot"
    (space / FOLDED_DIR / "plans").mkdir(parents=True)

    space_unfold(space)

    assert (space / ".navig" / "plans").is_dir()
    assert not (space / FOLDED_DIR).exists()


def test_unfold_refuses_to_clobber_an_existing_navig_dir(temp_dir, isolated_registry):
    """Both dirs present means a half-done fold — merging is the operator's call."""
    import typer

    from navig.commands.space import space_unfold
    from navig.spaces.resolver import FOLDED_DIR

    space = Path(temp_dir) / "iot"
    (space / FOLDED_DIR).mkdir(parents=True)
    (space / ".navig").mkdir(parents=True)

    with pytest.raises(typer.Exit) as exc:
        space_unfold(space)
    assert exc.value.exit_code == 1
    assert (space / FOLDED_DIR).is_dir()  # nothing destroyed
