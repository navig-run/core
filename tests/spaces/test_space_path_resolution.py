"""A space's folder name is not its canonical id, and resolution has to know that.

`resolve_space` built the path from the canonical NAME — `~/.navig/spaces/<name>` — and
never checked whether anything was there. But the canonical id comes from the space's
MANIFEST: `dev-android-space` is `android`, `research-space` is `company-research`,
`dev-space` is `homelab-dev`. Measured on a real install against
`discover_space_paths()`, which reads the manifest:

    resolve_space path == discovered path :  1 of 19
    mismatched                            : 18 of 19

Every one of those 18 pointed at a directory that does not exist, and callers guard with
`if not cfg.path.exists(): return None`. So `navig space next` answered "no next task"
for every space, `read_space_progress` reported 0%, and `build_space_kickoff` found no
actions — each indistinguishable from a space that genuinely has nothing in it. After
the fix the same install reports a next task for 9 of 19; the other 10 have no
`CURRENT_PHASE.md` or no unchecked box, which is a real answer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from navig.spaces import registry
from navig.spaces.plan_files import PLAN_DIRS, plan_file, plans_dir, read_plan
from navig.spaces.resolver import discover_space_paths, resolve_space


@pytest.fixture
def spaces_root(tmp_path: Path, monkeypatch) -> Path:
    """An isolated spaces root plus an isolated registry.

    ⚠ `NAVIG_DATA_DIR` does not reach either of these — both hang off `config_dir()` —
    so the config dir is what has to move, or the test writes into the operator's own
    `~/.navig/spaces.json`.
    """
    config = tmp_path / "cfg"
    (config / "spaces").mkdir(parents=True)
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(config))
    registry._load_for_mutation.cache_clear() if hasattr(  # type: ignore[attr-defined]
        registry._load_for_mutation, "cache_clear"
    ) else None
    return config / "spaces"


def make_space(root: Path, folder: str, *, space_id: str, phase: str | None = None) -> Path:
    """A space whose FOLDER name deliberately differs from its manifest id."""
    path = root / folder
    (path / ".navig" / "plans").mkdir(parents=True)
    (path / ".navig" / "space.json").write_text(
        json.dumps({"id": space_id, "name": space_id}), encoding="utf-8"
    )
    if phase is not None:
        (path / ".navig" / "plans" / "CURRENT_PHASE.md").write_text(phase, encoding="utf-8")
    return path


# ── resolution ───────────────────────────────────────────────────────────────

def test_a_space_is_found_by_its_manifest_id_not_its_folder(spaces_root: Path) -> None:
    """The headline. `dev-android-space` answers to `android`."""
    real = make_space(spaces_root, "dev-android-space", space_id="android")

    cfg = resolve_space("android")
    assert cfg.path == real, f"resolved to {cfg.path}, which is not the space"
    assert cfg.path.exists()


def test_the_old_behaviour_would_have_returned_a_path_that_does_not_exist(
    spaces_root: Path,
) -> None:
    """Teeth, stated as the property rather than by reverting the code.

    The conventional path is what the buggy version returned; it must NOT be what
    resolution answers when the space lives elsewhere.
    """
    make_space(spaces_root, "dev-android-space", space_id="android")

    conventional = spaces_root / "android"
    assert not conventional.exists(), "the fixture would not reproduce the bug"
    assert resolve_space("android").path != conventional


def test_an_exact_folder_match_still_wins_and_costs_no_lookup(spaces_root: Path) -> None:
    """The fast path is unchanged for the ordinary case."""
    exact = make_space(spaces_root, "homelab", space_id="homelab")
    assert resolve_space("homelab").path == exact


def test_an_unknown_name_still_returns_somewhere_to_create_it(spaces_root: Path) -> None:
    """`space init` needs a path for a space that does not exist yet, so an unresolvable
    name must return the conventional location rather than None or a raise.

    Note the path is the CANONICAL name: `normalize_space_name` strips the `-space`
    suffix, which is one of the reasons a folder and its id diverge in the first place.
    """
    from navig.spaces.contracts import normalize_space_name

    assert normalize_space_name("brand-new-space") == "brand-new"

    cfg = resolve_space("brand-new-space")
    assert cfg.path == spaces_root / "brand-new"
    assert not cfg.path.exists()


def test_resolution_agrees_with_discovery_for_every_space(spaces_root: Path) -> None:
    """The two must not disagree — that disagreement IS the bug."""
    make_space(spaces_root, "dev-android-space", space_id="android")
    make_space(spaces_root, "research-space", space_id="company-research")
    make_space(spaces_root, "homelab", space_id="homelab")

    discovered = discover_space_paths()
    assert len(discovered) >= 3
    for name, cfg in discovered.items():
        assert resolve_space(name).path == cfg.path, f"{name} resolves elsewhere"


# ── the registry index ───────────────────────────────────────────────────────

def test_a_drifted_registry_id_is_repaired(spaces_root: Path) -> None:
    """`ensure_registered` returned the moment the PATH was known, so an entry written
    before the space had a manifest kept its FOLDER name as its id forever. All 19
    entries on a real install were like that."""
    path = make_space(spaces_root, "dev-android-space", space_id="android")
    registry.register(path, id="dev-android-space", name="dev-android-space")
    assert any(e["id"] == "dev-android-space" for e in registry.load_registry()["spaces"])

    discover_space_paths()  # discovery is what repairs it

    ids = {e["id"] for e in registry.load_registry()["spaces"]}
    assert "android" in ids
    assert "dev-android-space" not in ids


def test_repairing_the_id_preserves_the_row_state(spaces_root: Path) -> None:
    """The id is a derived LABEL. `enabled` and the path travel with the row, and
    losing either while correcting a label would be a much worse bug than the one
    being fixed."""
    path = make_space(spaces_root, "research-space", space_id="company-research")
    registry.register(path, id="research-space", name="Research")
    registry.set_enabled("research-space", False)

    discover_space_paths(include_disabled=True)

    entry = next(
        e for e in registry.load_registry()["spaces"] if Path(e["path"]) == path
    )
    assert entry["id"] == "company-research"
    assert entry["enabled"] is False, "the disabled state was lost while relabelling"


def test_a_registry_row_whose_directory_is_gone_never_resolves(spaces_root: Path) -> None:
    """A stale row must not resolve to a path that is not there — that is the class of
    bug being fixed, arriving through a different door."""
    registry.register(spaces_root / "deleted-space", id="ghost", name="ghost")
    assert not resolve_space("ghost").path.exists()
    assert resolve_space("ghost").path == spaces_root / "ghost"


# ── where the plans live ─────────────────────────────────────────────────────

def test_plans_are_read_from_navig_plans(spaces_root: Path) -> None:
    """Measured on a real install: 10 of the 11 spaces that have a `CURRENT_PHASE.md`
    keep it under `.navig/plans/`, and every reader looked at the space root."""
    path = make_space(spaces_root, "s", space_id="s", phase="- [ ] Do the thing\n")
    assert plan_file(path, "CURRENT_PHASE.md") == path / ".navig" / "plans" / "CURRENT_PHASE.md"
    assert "Do the thing" in read_plan(path, "CURRENT_PHASE.md")


def test_a_plan_at_the_space_root_is_still_found(spaces_root: Path) -> None:
    """One real space still uses the flat layout; dropping it would break that space
    silently, which is the same failure in the other direction."""
    path = spaces_root / "legacy"
    path.mkdir(parents=True)
    (path / "CURRENT_PHASE.md").write_text("- [ ] Legacy task\n", encoding="utf-8")
    assert "Legacy task" in read_plan(path, "CURRENT_PHASE.md")


def test_the_nested_copy_is_preferred_over_the_root(spaces_root: Path) -> None:
    path = make_space(spaces_root, "s", space_id="s", phase="- [ ] Nested\n")
    (path / "CURRENT_PHASE.md").write_text("- [ ] Root\n", encoding="utf-8")
    assert "Nested" in read_plan(path, "CURRENT_PHASE.md")


def test_a_missing_plan_reads_as_empty_not_an_error(spaces_root: Path) -> None:
    """8 of 19 spaces have no `CURRENT_PHASE.md` at all — a real and common answer, so
    raising would turn one plan-less space into a briefing that fails for all of them."""
    path = make_space(spaces_root, "s", space_id="s")
    assert plan_file(path, "CURRENT_PHASE.md") is None
    assert read_plan(path, "CURRENT_PHASE.md") == ""


def test_a_new_plan_is_written_where_the_space_already_keeps_them(spaces_root: Path) -> None:
    """Writing to a different folder than the one being READ is how you end up with two
    CURRENT_PHASE.md files and no way to tell which one counts."""
    nested = make_space(spaces_root, "s", space_id="s", phase="x")
    assert plans_dir(nested) == nested / ".navig" / "plans"

    flat = spaces_root / "legacy"
    flat.mkdir(parents=True)
    (flat / "CURRENT_PHASE.md").write_text("x", encoding="utf-8")
    assert plans_dir(flat) == flat

    empty = spaces_root / "fresh"
    empty.mkdir(parents=True)
    assert plans_dir(empty) == empty / ".navig" / "plans"


def test_the_search_order_is_not_empty() -> None:
    """Anti-vacuity: every assertion above passes trivially against an empty list."""
    assert ".navig/plans" in PLAN_DIRS
    assert PLAN_DIRS[0] == ".navig/plans", "the real location must be searched first"


# ── the surfaces that were silently empty ────────────────────────────────────

def test_next_action_finds_the_task_it_used_to_miss(spaces_root: Path) -> None:
    """The end the whole change is for."""
    from navig.spaces.next_action import get_space_next_action

    make_space(
        spaces_root,
        "dev-android-space",
        space_id="android",
        phase="# Phase\n\n- [x] Done already\n- [ ] Install platform-tools\n",
    )
    action = get_space_next_action("android")
    assert action is not None, "resolution returned a path that does not exist"
    assert action.next_task == "Install platform-tools"


def test_kickoff_reads_the_SPACE_not_the_working_directory(
    spaces_root: Path, tmp_path: Path
) -> None:
    """⚠ A second bug wearing the same symptom.

    `build_space_kickoff` read its plans from ``cwd / ".navig" / "plans"`` — the
    PROCESS's directory, not the space's. A briefing built for space X by a daemon
    sitting anywhere else read whatever that folder happened to hold, or nothing.
    """
    from navig.spaces.kickoff import build_space_kickoff

    space = make_space(
        spaces_root, "homelab-space", space_id="homelab",
        phase="- [ ] Renew the certificate\n",
    )

    # A decoy: the same file, in the CWD, with different content.
    elsewhere = tmp_path / "somewhere-else"
    (elsewhere / ".navig" / "plans").mkdir(parents=True)
    (elsewhere / ".navig" / "plans" / "CURRENT_PHASE.md").write_text(
        "- [ ] A task from a completely different folder\n", encoding="utf-8"
    )

    kickoff = build_space_kickoff("homelab", space, cwd=elsewhere)
    joined = " ".join(kickoff.actions)
    assert "Renew the certificate" in joined
    assert "completely different folder" not in joined, (
        "the kickoff read the working directory instead of the space"
    )
