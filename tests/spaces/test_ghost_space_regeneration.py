"""The October 2026 ghost-space incident: cleaned-up spaces kept coming back.

Fourteen spaces were quarantined twice (2026-09-21, 2026-10-14) and were back within a
day both times. Two writers cooperated:

* NAVIG OS still listed them as workspaces and recreated each folder as a stub
  (``skills/`` + ``sources/`` + ``events.jsonl``) — fixed in ``apps/os`` (watcher).
* Core discovery treated EVERY non-dot child of ``~/.navig/spaces`` as a space and
  registered it during reads, so the stubs went straight back into ``spaces.json``; and
  ``space forget`` lasted only until the next scan.

These tests pin the Core half.
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from navig.spaces import registry
from navig.spaces.resolver import discover_space_paths


@pytest.fixture
def spaces_root(tmp_path: Path, monkeypatch) -> Path:
    """Isolated spaces root + registry (both hang off ``config_dir()``)."""
    config = tmp_path / "cfg"
    (config / "spaces").mkdir(parents=True)
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(config))
    # Keep the project walk-up from finding the repo's own .navig/.
    monkeypatch.setattr("navig.spaces.resolver._find_project_navig_root", lambda _cwd: None)
    assert registry._registry_file().parent == config, "registry is not isolated"
    return config / "spaces"


def make_space(root: Path, folder: str, space_id: str | None = None) -> Path:
    path = root / folder
    (path / ".navig").mkdir(parents=True)
    (path / ".navig" / "space.json").write_text(
        json.dumps({"id": space_id or folder, "name": space_id or folder}), encoding="utf-8"
    )
    return path


def make_stub(root: Path, folder: str) -> Path:
    """What NAVIG OS left behind for a deleted space."""
    path = root / folder
    (path / "skills").mkdir(parents=True)
    (path / "sources").mkdir()
    (path / "events.jsonl").write_text('{"type":"SchedulerTick"}\n', encoding="utf-8")
    return path


def registered_paths() -> set[str]:
    return {registry._norm(e["path"]) for e in registry.load_registry()["spaces"]}


# ── stubs are not spaces ─────────────────────────────────────────────────────

def test_a_stub_folder_is_neither_discovered_nor_registered(spaces_root: Path) -> None:
    make_space(spaces_root, "human-space", "human")
    stub = make_stub(spaces_root, "human-growth-space")

    found = discover_space_paths()

    assert "human" in found
    assert "human-growth" not in found and "human-growth-space" not in found
    assert registry._norm(stub) not in registered_paths(), "the stub was written to spaces.json"


def test_a_stub_with_an_empty_navig_dir_is_not_a_space(spaces_root: Path) -> None:
    """Two of the fourteen stubs gained an EMPTY `.navig/` seconds after they were
    recreated; a bare `.navig/` inside a spaces root must not make them spaces."""
    stub = make_stub(spaces_root, "android-space")
    (stub / ".navig").mkdir()

    assert "android-space" not in discover_space_paths()
    assert "android" not in discover_space_paths()
    assert registry._norm(stub) not in registered_paths()


def test_a_stale_row_for_a_stub_does_not_bring_it_back(spaces_root: Path) -> None:
    """Rows written by the old scan (source `global`) still sit in real registries."""
    stub = make_stub(spaces_root, "company-empire-space")
    registry.register(stub, id="company-empire", name="Empire", source="global")

    assert "company-empire" not in discover_space_paths()


def test_an_explicit_row_in_a_root_is_honoured_and_forget_removes_it(
    spaces_root: Path,
) -> None:
    """Two real ghosts were registered explicitly (`external`) with an empty `.navig/`.
    Discovery honours an explicit registration; `forget` is the way out — and now sticks."""
    stub = make_stub(spaces_root, "company-research-space")
    (stub / ".navig").mkdir()
    registry.register(stub, id="company-research", name="Research", source="external")
    assert "company-research" in discover_space_paths()

    registry.forget(str(stub))

    assert "company-research" not in discover_space_paths()
    assert registry._norm(stub) not in registered_paths()


def test_an_explicitly_registered_external_folder_still_resolves(
    spaces_root: Path, tmp_path: Path
) -> None:
    """The strict rule is for children of a spaces ROOT only — not external rows."""
    external = tmp_path / "projects" / "app"
    (external / ".navig").mkdir(parents=True)
    registry.register(external, id="app", name="app", source="external")

    assert discover_space_paths()["app"].path == external


# ── forget sticks ────────────────────────────────────────────────────────────

def test_forget_survives_a_rescan(spaces_root: Path) -> None:
    space = make_space(spaces_root, "war-room-space", "war-room")
    assert "war-room" in discover_space_paths()

    assert registry.forget(str(space))
    found = discover_space_paths()

    assert "war-room" not in found
    assert registry._norm(space) not in registered_paths()
    assert registry.is_forgotten(space)


def test_register_lifts_a_forget(spaces_root: Path) -> None:
    space = make_space(spaces_root, "war-room-space", "war-room")
    discover_space_paths()
    registry.forget(str(space))

    registry.register(space, id="war-room", name="War Room")

    assert not registry.is_forgotten(space)
    assert "war-room" in discover_space_paths()


def test_an_explicit_ensure_registered_lifts_a_forget(spaces_root: Path) -> None:
    """`space switch` / `doctor --fix` are decisions, unlike a scan."""
    space = make_space(spaces_root, "research-space", "research")
    discover_space_paths()
    registry.forget(str(space))

    registry.ensure_registered(space, id="research", name="Research")

    assert not registry.is_forgotten(space)
    assert registry._norm(space) in registered_paths()


def test_fold_style_forget_does_not_tombstone(spaces_root: Path) -> None:
    """`space fold` forgets with remember=False so `unfold` needs no second step."""
    space = make_space(spaces_root, "iot", "iot")
    discover_space_paths()

    registry.forget(str(space), remember=False)

    assert not registry.is_forgotten(space)
    assert "iot" in discover_space_paths()


# ── duplicate ids ────────────────────────────────────────────────────────────

def test_a_duplicate_id_keeps_the_first_folder(spaces_root: Path) -> None:
    first = make_space(spaces_root, "a-android-space", "android")
    make_space(spaces_root, "b-android-space", "android")

    assert discover_space_paths()["android"].path == first


def test_a_scan_does_not_relabel_a_row_onto_an_id_another_folder_holds(
    spaces_root: Path,
) -> None:
    """`projects` gives a clash a `-2` suffix; the next scan used to undo it."""
    keeper = make_space(spaces_root, "android-space", "android")
    second = make_space(spaces_root, "dev-android-space", "android")
    registry.register(keeper, id="android", name="Android")
    registry.register(second, id="android-2", name="Android")

    discover_space_paths()

    by_path = {registry._norm(e["path"]): e["id"] for e in registry.load_registry()["spaces"]}
    assert by_path[registry._norm(keeper)] == "android"
    assert by_path[registry._norm(second)] == "android-2"


# ── concurrency ──────────────────────────────────────────────────────────────

def test_concurrent_registers_lose_no_rows(spaces_root: Path, tmp_path: Path) -> None:
    """Load-modify-save from several writers used to keep only the last writer's row."""
    targets = [tmp_path / "ext" / f"s{i}" for i in range(8)]
    for t in targets:
        (t / ".navig").mkdir(parents=True)
    barrier = threading.Barrier(len(targets))

    def work(t: Path) -> None:
        barrier.wait()
        registry.register(t, id=t.name, name=t.name, source="external")

    threads = [threading.Thread(target=work, args=(t,)) for t in targets]
    for th in threads:
        th.start()
    for th in threads:
        th.join(timeout=60)

    assert {registry._norm(t) for t in targets} <= registered_paths()


# ── Windows junctions ────────────────────────────────────────────────────────

@pytest.mark.skipif(sys.platform != "win32", reason="NTFS junctions are Windows-only")
def test_a_junction_to_a_registered_space_is_discovered_once(
    spaces_root: Path, tmp_path: Path
) -> None:
    real = make_space(tmp_path / "elsewhere", "homelab-space", "homelab")
    registry.register(real, id="homelab", name="Homelab", source="external")
    link = spaces_root / "homelab-link"
    made = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(real)], capture_output=True, check=False
    )
    if made.returncode != 0:
        pytest.skip(f"could not create a junction: {made.stderr!r}")

    found = discover_space_paths()
    rows = [e for e in registry.load_registry()["spaces"] if e["id"] == "homelab"]

    assert registry._norm(found["homelab"].path) == registry._norm(real)
    assert len(rows) == 1, f"the junction registered a second row: {rows}"
