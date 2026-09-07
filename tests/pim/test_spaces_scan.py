"""Turning `- [ ]` lines in a space's plans into suggestions.

Two properties matter more than the parsing: the scan must never propose the same line
twice (including after it was dismissed), and it must never walk a space's whole tree.
"""

from __future__ import annotations

from pathlib import Path

from navig.pim.spaces_scan import (
    PLAN_FILES,
    Suggestion,
    plan_files,
    scan_space,
    scan_spaces,
    unchecked_boxes,
)
from navig.store.board import BoardStore


def _space(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    root = tmp_path / name
    for rel, body in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


# ── where plans live ─────────────────────────────────────────────────────────

def test_plans_are_found_under_navig_plans(tmp_path: Path) -> None:
    """Where they actually live.

    Verified on the operator's install: 11 spaces keep CURRENT_PHASE.md under
    `.navig/plans/` and exactly 1 at the root — while `spaces/kickoff.py` and
    `spaces/next_action.py` read the ROOT, so they find nothing for almost every space.
    """
    root = _space(tmp_path, "s", {".navig/plans/CURRENT_PHASE.md": "- [ ] Do the thing"})
    assert [p.name for p in plan_files(root)] == ["CURRENT_PHASE.md"]


def test_a_plan_at_the_space_root_is_still_found(tmp_path: Path) -> None:
    """The 1-in-12 layout must not be dropped while fixing the 11."""
    root = _space(tmp_path, "s", {"CURRENT_PHASE.md": "- [ ] Do the thing"})
    assert [p.name for p in plan_files(root)] == ["CURRENT_PHASE.md"]


def test_the_nested_copy_wins_and_is_not_read_twice(tmp_path: Path) -> None:
    root = _space(
        tmp_path,
        "s",
        {
            ".navig/plans/CURRENT_PHASE.md": "- [ ] Nested one",
            "CURRENT_PHASE.md": "- [ ] Root one",
        },
    )
    found = plan_files(root)
    assert len(found) == 2, "both are read, nearest first"
    assert ".navig" in str(found[0])


def test_only_the_NAMED_plan_files_are_read(tmp_path: Path) -> None:
    """⚠ The scan reads named files, NEVER a recursive walk.

    Measured on this machine: `grep -r` over `~/.navig/spaces` does not finish in two
    minutes, because spaces vendor whole reference projects under `.lab/` and keep
    `.trash-*` directories from earlier merges. A walk would be slow AND would propose
    "tasks" from another project's pull-request template.
    """
    root = _space(
        tmp_path,
        "s",
        {
            ".navig/plans/CURRENT_PHASE.md": "- [ ] A real task",
            ".navig/plans/some-brief.md": "- [ ] A brief, not a plan",
            ".lab/vendored/README.md": "- [ ] Someone else's checklist",
            "notes.md": "- [ ] A scratch note",
        },
    )
    titles = [s.title for s in scan_space("s", root, limit=99)]
    assert titles == ["A real task"]


def test_the_named_list_has_not_silently_shrunk() -> None:
    """Anti-vacuity: every test above passes trivially if PLAN_FILES is empty."""
    assert len(PLAN_FILES) >= 3
    assert "CURRENT_PHASE.md" in PLAN_FILES


# ── parsing ──────────────────────────────────────────────────────────────────

def test_only_UNCHECKED_boxes_are_proposed(tmp_path: Path) -> None:
    """Proposing something already done is the fastest way to lose trust in a list."""
    path = tmp_path / "p.md"
    path.write_text(
        "- [ ] Still to do\n- [x] Already done\n- [X] Also done\n", encoding="utf-8"
    )
    assert [s.title for s in unchecked_boxes(path, space="s")] == ["Still to do"]


def test_markdown_is_stripped_so_a_task_reads_like_a_task(tmp_path: Path) -> None:
    path = tmp_path / "p.md"
    path.write_text(
        "- [ ] Read [the runbook](docs/run.md) and set `adb_path` in **config**\n",
        encoding="utf-8",
    )
    assert unchecked_boxes(path, space="s")[0].title == (
        "Read the runbook and set adb_path in config"
    )


def test_indented_and_asterisk_boxes_count(tmp_path: Path) -> None:
    path = tmp_path / "p.md"
    path.write_text("  - [ ] Indented\n* [ ] Asterisk\n", encoding="utf-8")
    assert len(unchecked_boxes(path, space="s")) == 2


def test_an_empty_or_one_word_box_is_a_template_placeholder(tmp_path: Path) -> None:
    path = tmp_path / "p.md"
    path.write_text("- [ ] \n- [ ] TBD\n- [ ] A genuine task here\n", encoding="utf-8")
    assert [s.title for s in unchecked_boxes(path, space="s")] == ["A genuine task here"]


def test_an_unreadable_file_skips_that_file_not_the_scan(tmp_path: Path) -> None:
    """One space with a permissions problem must not stop the other twenty."""
    assert unchecked_boxes(tmp_path / "does-not-exist.md", space="s") == []


# ── the dedup key ────────────────────────────────────────────────────────────

def test_the_origin_ref_names_space_file_and_line() -> None:
    suggestion = Suggestion(title="x", space="homelab", source_file="CURRENT_PHASE.md", line_no=14)
    assert suggestion.origin_ref == "homelab:CURRENT_PHASE.md:14"


def test_an_already_added_source_is_not_proposed_again(tmp_path: Path) -> None:
    root = _space(tmp_path, "s", {".navig/plans/CURRENT_PHASE.md": "- [ ] Renew the cert"})
    store = BoardStore(tmp_path / "board.db")

    first = scan_spaces({"s": root}, store=store)
    assert [x.title for x in first] == ["Renew the cert"]

    store.create_todo(first[0].title, origin="agent", origin_ref=first[0].origin_ref)
    assert scan_spaces({"s": root}, store=store) == []


def test_a_DISMISSED_suggestion_stays_dismissed(tmp_path: Path) -> None:
    """The property that decides whether this feature is helpful or a nag.

    Deleting a suggestion has to mean "not this one" permanently — otherwise the very
    next scan proposes it again, and the operator learns that dismissing does nothing.
    """
    root = _space(tmp_path, "s", {".navig/plans/CURRENT_PHASE.md": "- [ ] Renew the cert"})
    store = BoardStore(tmp_path / "board.db")

    found = scan_spaces({"s": root}, store=store)
    todo = store.create_todo(found[0].title, origin="agent", origin_ref=found[0].origin_ref)
    store.delete_todo(todo["id"])

    assert store.todo_exists_for_source(found[0].origin_ref) is True
    assert scan_spaces({"s": root}, store=store) == []


def test_the_line_number_makes_two_identical_titles_distinct(tmp_path: Path) -> None:
    """Plan files repeat phrasings; keying on the title alone would drop real work."""
    root = _space(
        tmp_path, "s", {".navig/plans/CURRENT_PHASE.md": "- [ ] Write the docs\n- [ ] Write the docs\n"}
    )
    refs = {s.origin_ref for s in scan_space("s", root)}
    assert len(refs) == 2


# ── caps ─────────────────────────────────────────────────────────────────────

def test_the_cap_is_PER_SPACE(tmp_path: Path) -> None:
    """A space with a 200-item roadmap would otherwise fill the whole proposal and
    bury every other space's genuinely-next task."""
    big = _space(
        tmp_path,
        "big",
        {".navig/plans/CURRENT_PHASE.md": "\n".join(f"- [ ] Task number {i}" for i in range(50))},
    )
    small = _space(tmp_path, "small", {".navig/plans/CURRENT_PHASE.md": "- [ ] The only one"})

    found = scan_spaces({"big": big, "small": small}, per_space=3)
    assert len([s for s in found if s.space == "big"]) == 3
    assert [s.title for s in found if s.space == "small"] == ["The only one"]


def test_a_space_with_no_plans_contributes_nothing(tmp_path: Path) -> None:
    root = _space(tmp_path, "s", {"README.md": "no checkboxes here"})
    assert scan_space("s", root) == []


def test_a_dedup_failure_does_not_lose_the_scan(tmp_path: Path) -> None:
    """A broken store must degrade to "propose it" rather than "propose nothing" —
    a duplicate is annoying, a silently empty scan looks like there is no work."""
    root = _space(tmp_path, "s", {".navig/plans/CURRENT_PHASE.md": "- [ ] Renew the cert"})

    class _Broken:
        def todo_exists_for_source(self, ref: str) -> bool:
            raise RuntimeError("database is locked")

    assert len(scan_spaces({"s": root}, store=_Broken())) == 1
