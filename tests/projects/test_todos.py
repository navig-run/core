"""Todos: extraction across docs, and in-place mutations that change only their line."""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.projects import todos

DEV = ".navig/plans/DEV_PLAN.md"


def _by_text(root: Path) -> dict[str, dict]:
    return {t["text"]: t for t in todos.extract_todos(root) if t["path"] == DEV}


def test_three_states_headings_and_fences(project: Path) -> None:
    items = _by_text(project)
    assert items["Wire the checkout"]["state"] == "open"
    assert items["Ship the landing"]["state"] == "done"
    assert items["Refactor payments"]["state"] == "in_progress"
    assert items["Mobile app"]["state"] == "in_progress"  # *(in progress)* marker, stripped
    assert items["nested subtask"]["indent"] == 1
    assert items["Wire the checkout"]["heading"] == "Dev plan › Active"
    assert items["Mobile app"]["heading"] == "Dev plan › Later"
    assert "not a todo (inside a fence)" not in items


def test_extract_spans_every_doc(project: Path) -> None:
    paths = {t["path"] for t in todos.extract_todos(project)}
    assert {DEV, ".navig/plans/ROADMAP.md", ".navig/plans/CURRENT_PHASE.md",
            ".navig/plans/tasks/T-001-login.md"} <= paths
    assert not any("vault" in p for p in paths)


@pytest.mark.parametrize("eol", ["\n", "\r\n"])
def test_toggle_round_trip_is_byte_identical(project: Path, eol: str) -> None:
    doc = project / DEV
    doc.write_bytes(doc.read_bytes().decode().replace("\n", eol).encode())
    before = doc.read_bytes()
    t = _by_text(project)["Wire the checkout"]
    res = todos.set_state(project, DEV, t["line"], "done", expect=t["fingerprint"])
    assert res["todo"]["state"] == "done"
    assert doc.read_bytes() != before
    todos.set_state(project, DEV, t["line"], "open", expect=res["todo"]["fingerprint"])
    assert doc.read_bytes() == before


def test_in_progress_marker_is_cleared_when_done(project: Path) -> None:
    t = _by_text(project)["Mobile app"]
    todos.set_state(project, DEV, t["line"], "done", expect=t["fingerprint"])
    line = (project / DEV).read_text(encoding="utf-8").splitlines()[t["line"]]
    assert line == "* [x] Mobile app"


def test_stale_fingerprint_refuses_and_changes_nothing(project: Path) -> None:
    doc = project / DEV
    t = _by_text(project)["Wire the checkout"]
    # Someone inserts a line above it in their editor.
    doc.write_bytes(b"# inserted\n" + doc.read_bytes())
    before = doc.read_bytes()
    with pytest.raises(todos.TodoError) as exc:
        todos.set_state(project, DEV, t["line"], "done", expect=t["fingerprint"])
    assert exc.value.status == 409
    assert doc.read_bytes() == before


def test_edit_keeps_bullet_indent_and_state(project: Path) -> None:
    t = _by_text(project)["nested subtask"]
    todos.edit_todo(project, DEV, t["line"], "  renamed   subtask ", expect=t["fingerprint"])
    line = (project / DEV).read_text(encoding="utf-8").splitlines()[t["line"]]
    assert line == "  - [ ] renamed subtask"


def test_add_under_an_existing_heading_joins_its_list(project: Path) -> None:
    res = todos.add_todo(project, DEV, "Write tests", heading="Active")
    lines = (project / DEV).read_text(encoding="utf-8").splitlines()
    assert lines[res["todo"]["line"]] == "- [ ] Write tests"
    # It landed inside "Active", before the "Later" heading.
    assert res["todo"]["line"] < lines.index("## Later")
    assert res["todo"]["heading"] == "Dev plan › Active"


def test_add_under_a_new_heading_creates_it(project: Path) -> None:
    todos.add_todo(project, DEV, "Pick a domain", heading="Launch")
    text = (project / DEV).read_text(encoding="utf-8")
    assert text.rstrip().endswith("## Launch\n\n- [ ] Pick a domain")


def test_mutations_refuse_denied_paths(project: Path) -> None:
    from navig.projects.docs import DocError

    with pytest.raises(DocError):
        todos.add_todo(project, ".navig/vault/secret.md", "x")
