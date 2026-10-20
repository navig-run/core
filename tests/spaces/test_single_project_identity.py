"""One logical project identity — registry, discovery and resolution together.

The defect class (observed 2026-10-20 with 9 `navig` rows): discovery persisted EVERY
folder it walked into `spaces.json` *before* its duplicate check, so any session opened
inside a git worktree appended a row under the main project's id — and every id→root
reader then depended on row order, flipping between checkouts between runs (migrations
and scanners read the wrong tree).

The model pinned here: **one id → one canonical repository path; worktrees are
checkouts (instances), never rows; active checkout resolution is explicit; file
identifiers stay project-relative and shared across checkouts.** Covers: main +
multiple worktrees · stable identity · current-worktree resolution · duplicate
registration · deletion/unregistration · Windows case · concurrency · missing checkout.

Git cases use a real temp repository (init/worktree add); the registry is isolated
through NAVIG_CONFIG_DIR. No existing worktree is touched.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from navig.projects import gitinfo
from navig.projects.model import ProjectNotFound, active_checkout, resolve_project
from navig.spaces import registry

HAS_GIT = shutil.which("git") is not None
pytestmark = pytest.mark.skipif(not HAS_GIT, reason="git required for checkout identity tests")


@pytest.fixture
def isolated(tmp_path: Path, monkeypatch) -> Path:
    config = tmp_path / "cfg"
    (config / "spaces").mkdir(parents=True)
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(config))
    monkeypatch.setenv("NAVIG_INVOCATION_CWD", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _git(root: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", *args], cwd=str(root), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30, check=True,
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
    )
    return out.stdout


@pytest.fixture
def repo_family(tmp_path: Path) -> dict[str, Path]:
    """main checkout + two worktrees of ONE repository, each with a .navig/space.json
    claiming the shared id (exactly what a tracked worktree carries)."""
    main = tmp_path / "proj-main"
    main.mkdir()
    _git(main, "init", "-q", "-b", "main")
    (main / "file.txt").write_text("x\n", encoding="utf-8")
    _git(main, "add", "file.txt")
    _git(main, "commit", "-q", "-m", "first")
    for name in ("wt-one", "wt-two"):
        _git(main, "worktree", "add", "-q", str(tmp_path / name))
    for p in (main, tmp_path / "wt-one", tmp_path / "wt-two"):
        (p / ".navig").mkdir(exist_ok=True)
        (p / ".navig" / "space.json").write_text('{"id": "proj", "display_name": "Proj"}',
                                                 encoding="utf-8")
    return {"main": main, "wt1": tmp_path / "wt-one", "wt2": tmp_path / "wt-two"}


def _rows(space_id: str) -> list[dict]:
    return [e for e in registry.load_registry()["spaces"] if e.get("id") == space_id]


def _rows_for_path(path: str) -> list[dict]:
    rp = registry._norm(path)
    return [e for e in registry.load_registry()["spaces"]
            if registry._norm(e.get("path", "")) == rp]


# ── stable identity: one row, order-independent ──────────────────────────────


def test_discovery_from_a_worktree_never_appends_a_row(isolated: Path, repo_family) -> None:
    from navig.spaces.resolver import discover_space_paths  # noqa: PLC0415

    registry.register(repo_family["main"], id="proj", name="Proj", source="external")
    assert len(_rows("proj")) == 1
    # a session's cwd inside a worktree walks up to the worktree's tracked .navig
    cfgs = discover_space_paths(cwd=repo_family["wt1"], include_disabled=True)
    assert len(_rows("proj")) == 1, "discovery must not turn a checkout into a registry row"
    # the discovered INSTANCE is the active checkout (explicit context)…
    assert registry._norm(cfgs["proj"].path) == registry._norm(repo_family["wt1"])
    # …while the canonical answer stays the main checkout
    assert registry.canonical_root("proj") == registry._norm(repo_family["main"])


def test_canonical_is_stable_across_row_orders(isolated: Path, repo_family) -> None:
    """Legacy duplicates (rows written by the old bug) resolve to the SAME canonical
    checkout regardless of the order they sit in."""
    main, wt1, wt2 = repo_family["main"], repo_family["wt1"], repo_family["wt2"]
    rows = [
        {"id": "proj", "name": "wt1", "path": str(wt1), "source": "project", "enabled": True},
        {"id": "proj", "name": "main", "path": str(main), "source": "external", "enabled": True},
        {"id": "proj", "name": "wt2", "path": str(wt2), "source": "project", "enabled": True},
    ]
    import itertools  # noqa: PLC0415

    seen = set()
    for perm in itertools.permutations(rows):
        registry.save_registry({"version": 1, "active": None, "spaces": list(perm)})
        seen.add(registry.canonical_root("proj"))
    assert seen == {registry._norm(main)}, f"canonical flipped between rows: {seen}"


def test_resolve_project_is_canonical_even_with_legacy_rows(isolated: Path, repo_family) -> None:
    main, wt1 = repo_family["main"], repo_family["wt1"]
    registry.save_registry({"version": 1, "active": None, "spaces": [
        {"id": "proj", "name": "wt1", "path": str(wt1), "source": "project", "enabled": True},
        {"id": "proj", "name": "main", "path": str(main), "source": "external", "enabled": True},
    ]})
    pid, root = resolve_project("proj")
    assert pid == "proj"
    assert registry._norm(root) == registry._norm(main)
    # cwd never flips an id→root — canonical-first, no discovery ordering involved
    # (a session inside the worktree asking for THE PROJECT ROOT gets the project root;
    # asking for its own checkout uses active_checkout)


# ── explicit active-checkout resolution ──────────────────────────────────────


def test_active_checkout_picks_the_containing_worktree(isolated: Path, repo_family) -> None:
    registry.register(repo_family["main"], id="proj", name="Proj", source="external")
    # a file inside a worktree → THAT checkout
    (repo_family["wt1"] / "sub").mkdir()
    got = active_checkout(repo_family["wt1"] / "sub" / "a.py", project_id="proj")
    assert registry._norm(got) == registry._norm(repo_family["wt1"])
    # the main checkout → main
    got = active_checkout(repo_family["main"] / "file.txt", project_id="proj")
    assert registry._norm(got) == registry._norm(repo_family["main"])
    # default = cwd
    cwd = os.getcwd()
    try:
        os.chdir(repo_family["wt2"])
        assert registry._norm(active_checkout(project_id="proj")) == registry._norm(repo_family["wt2"])
    finally:
        os.chdir(cwd)
    # no project id → the checkout containing the path, period
    assert registry._norm(active_checkout(repo_family["wt1"] / "file.txt")) == registry._norm(repo_family["wt1"])


def test_active_checkout_outside_the_project_falls_back_to_canonical(isolated: Path, repo_family,
                                                                     tmp_path: Path) -> None:
    registry.register(repo_family["main"], id="proj", name="Proj", source="external")
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    _git(foreign, "init", "-q", "-b", "main")  # a DIFFERENT repository
    assert registry._norm(active_checkout(foreign, project_id="proj")) == registry._norm(repo_family["main"])
    # unknown project id inside a repo → None (never some other project's root)
    assert active_checkout(foreign, project_id="ghost") is None
    # plain non-git folder with a known id → canonical
    plain = tmp_path / "plain"
    plain.mkdir()
    assert registry._norm(active_checkout(plain, project_id="proj")) == registry._norm(repo_family["main"])


# ── duplicate registration ───────────────────────────────────────────────────


def test_ensure_registered_refuses_a_checkout_row(isolated: Path, repo_family) -> None:
    registry.register(repo_family["main"], id="proj", name="Proj", source="external")
    registry.ensure_registered(repo_family["wt1"], id="proj", name="wt-one",
                               source="project", auto=True)
    registry.ensure_registered(repo_family["wt2"], id="proj", name="wt-two",
                               source="project", auto=True)
    assert len(_rows("proj")) == 1


def test_registering_the_ancestor_repoints_a_stray_worktree_row(isolated: Path, repo_family) -> None:
    """Self-healing: a legacy row pointing at the worktree moves to the checkout that
    contains it when the main path registers."""
    registry.save_registry({"version": 1, "active": None, "spaces": [
        {"id": "proj", "name": "wt1", "path": str(repo_family["wt1"]), "source": "project",
         "enabled": True},
    ]})
    returned = registry.register(repo_family["main"], id="proj", name="Proj", source="external")
    rows = _rows("proj")
    assert len(rows) == 1
    assert registry._norm(rows[0]["path"]) == registry._norm(repo_family["main"])
    assert registry._norm(returned["path"]) == registry._norm(repo_family["main"])


def test_unrelated_folder_cannot_claim_a_taken_id(isolated: Path) -> None:
    home = isolated / "one"
    (home / ".navig").mkdir(parents=True)
    registry.register(home, id="shared-id", name="one", source="external")
    other = isolated / "two"
    (other / ".navig").mkdir(parents=True)
    registry.ensure_registered(other, id="shared-id", auto=True)
    assert len(_rows("shared-id")) == 1
    # a NON-auto explicit register is refused too, returning the holder
    back = registry.register(other, id="shared-id", name="two", source="external")
    assert registry._norm(back["path"]) == registry._norm(home)
    assert len(_rows("shared-id")) == 1


# ── deletion and unregistration ──────────────────────────────────────────────


def test_repo_remove_drops_the_spaces_row_without_tombstoning(isolated: Path,
                                                               repo_family: dict) -> None:
    from navig.commands.repo import remove_worktree  # noqa: PLC0415

    wt1 = repo_family["wt1"]
    # legacy state: the worktree owns a row (the old bug)
    registry.save_registry({"version": 1, "active": None, "spaces": [
        {"id": "proj", "name": "main", "path": str(repo_family["main"]), "source": "external",
         "enabled": True},
        {"id": "proj", "name": "wt1", "path": str(wt1), "source": "project", "enabled": True},
    ]})
    # force: the fixture's .navig/space.json is intentionally uncommitted in the worktree;
    # what is under test is the registry-row cleanup on a successful removal
    out = remove_worktree(repo_family["main"], wt1, force=True)
    assert out["removed"] is True, out
    assert _rows_for_path(str(wt1)) == [], "the removed checkout's row must go with it"
    assert len(_rows("proj")) == 1, "the canonical row stays"
    # remember=False: no tombstone, a future worktree with this slug may register
    assert registry._norm(str(wt1)) not in registry.forgotten_paths()


# ── Windows drive letters and casing ─────────────────────────────────────────


def test_nested_is_case_and_separator_blind_with_boundaries() -> None:
    assert registry._nested(r"E:\proj\alpha\.dev\worktrees\wt", r"E:\PROJ\alpha")
    assert registry._nested("e:/proj/alpha/wt", r"E:\Proj\Alpha")
    assert not registry._nested(r"E:\proj\alpha2", r"E:\proj\alpha")  # prefix ≠ parent
    assert not registry._nested(r"E:\proj\alpha", r"E:\proj\alpha")   # equal ≠ nested


def test_case_variant_paths_register_as_one_row(isolated: Path) -> None:
    target = isolated / "CaseSpace"
    (target / ".navig").mkdir(parents=True)
    registry.register(target, id="case", name="CaseSpace", source="external")
    # a second registration in a different case/separator spelling of the SAME path
    variant = str(target).upper() if os.name == "nt" else str(target)
    registry.ensure_registered(variant, id="case", auto=True)
    assert len(_rows("case")) == 1


# ── concurrent agents ────────────────────────────────────────────────────────


def test_concurrent_registrations_keep_one_row(isolated: Path, repo_family) -> None:
    registry.register(repo_family["main"], id="proj", name="Proj", source="external")
    paths = [repo_family["main"], repo_family["wt1"], repo_family["wt2"]] * 3

    def _one(p: Path) -> None:
        registry.ensure_registered(p, id="proj", name="Proj",
                                   source="project", auto=True)

    with ThreadPoolExecutor(max_workers=6) as ex:
        list(ex.map(_one, paths))
    assert len(_rows("proj")) == 1


# ── safe behavior when the checkout is missing ───────────────────────────────


def test_missing_checkout_never_resolves_to_a_path(isolated: Path) -> None:
    gone = isolated / "deleted-project"
    registry.save_registry({"version": 1, "active": None, "spaces": [
        {"id": "ghost", "name": "ghost", "path": str(gone), "source": "external", "enabled": True},
    ]})
    # readers: nothing that is not on disk
    assert registry.canonical_root("ghost") is None
    with pytest.raises(ProjectNotFound):
        resolve_project("ghost")
    # structural questions can still see the row (plumbing, not resolution)
    assert registry.canonical_root("ghost", require_exists=False) == registry._norm(gone)
    # active checkout of an unknown id is None — never another project's root
    plain = isolated / "elsewhere"
    plain.mkdir()
    assert active_checkout(plain, project_id="ghost") is None


# ── git primitives the model rests on ────────────────────────────────────────


def test_same_repo_and_toplevel_across_checkouts(repo_family) -> None:
    assert gitinfo.same_repo(repo_family["main"], repo_family["wt1"])
    assert gitinfo.same_repo(repo_family["wt1"], repo_family["wt2"])
    # the parent of the repo belongs to a different repository (the outer navig checkout
    # pytest runs from) or to none — either way it is not THIS one
    assert not gitinfo.same_repo(repo_family["main"], repo_family["main"].parent)
    top = gitinfo.toplevel(repo_family["wt1"] / "file.txt")
    assert top and registry._norm(top) == registry._norm(repo_family["wt1"])
