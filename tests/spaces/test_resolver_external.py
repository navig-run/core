"""
Regression tests: externally-registered spaces must be discoverable.

`POST /api/deck/spaces/register` (and `navig space register`) write arbitrary
folders into the registry with source="external" — but discover_space_paths()
only scanned containers (global roots, plugins, project), so a registered
external space existed in spaces.json while being unresolvable by every
per-space route (plans/wiki/inbox-review/memory: "unknown space").
"""

from __future__ import annotations

import pytest

from navig.spaces import registry
from navig.spaces.resolver import discover_space_paths


@pytest.fixture(autouse=True)
def _isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    yield


def _make_space(tmp_path, name: str):
    space = tmp_path / "elsewhere" / name
    (space / ".navig" / "plans").mkdir(parents=True)
    (space / ".navig" / "plans" / "VISION.md").write_text("# vision\n", encoding="utf-8")
    return space


def test_externally_registered_space_is_discovered(tmp_path):
    space = _make_space(tmp_path, "proofext")
    registry.register(space, id="proofext", name="proofext", source="external", enabled=True)

    found = discover_space_paths(cwd=tmp_path / "unrelated-cwd")

    assert "proofext" in found
    assert found["proofext"].path.resolve() == space.resolve()


def test_disabled_external_space_is_excluded(tmp_path):
    space = _make_space(tmp_path, "offext")
    registry.register(space, id="offext", name="offext", source="external", enabled=True)
    registry.set_enabled("offext", False)

    assert "offext" not in discover_space_paths(cwd=tmp_path / "unrelated-cwd")
    assert "offext" in discover_space_paths(
        cwd=tmp_path / "unrelated-cwd", include_disabled=True
    )


def test_registry_entry_with_missing_path_is_skipped(tmp_path):
    ghost = _make_space(tmp_path, "ghostext")
    registry.register(ghost, id="ghostext", name="ghostext", source="external", enabled=True)
    import shutil

    shutil.rmtree(ghost)

    found = discover_space_paths(cwd=tmp_path / "unrelated-cwd")
    assert "ghostext" not in found


def test_container_space_still_wins_over_registry_duplicate(tmp_path):
    """A space inside a scanned container must not be double-counted."""
    container_space = tmp_path / "cfg" / "spaces" / "dualext"
    (container_space / ".navig").mkdir(parents=True)
    registry.register(
        container_space, id="dualext", name="dualext", source="external", enabled=True
    )

    found = discover_space_paths(cwd=tmp_path / "unrelated-cwd")
    assert "dualext" in found
    # Scanned first as a global-container space; the registry pass dedupes.
    assert found["dualext"].scope == "global"
