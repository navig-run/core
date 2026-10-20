"""The docs index and its one containment gate."""

from __future__ import annotations

from pathlib import Path

import pytest

from navig.projects import docs


def test_index_lists_every_shape_and_never_the_vault(project: Path) -> None:
    paths = [d["path"] for d in docs.index_docs(project)]
    assert ".navig/plans/VISION.md" in paths
    assert ".navig/plans/01-foundation.md" in paths
    assert ".navig/plans/briefs/brief-a.md" in paths
    assert ".navig/plans/tasks/T-001-login.md" in paths
    assert ".navig/ai_system_prompt.txt" in paths
    assert "CLAUDE.md" in paths and "README.md" in paths
    assert "ROADMAP.md" in paths  # the legacy root plan copy is still reachable
    assert not any("vault" in p for p in paths)
    assert "secret.md" not in paths  # root files outside the allowlist stay invisible


def test_canonical_plans_come_first_in_plan_files_order(project: Path) -> None:
    paths = [d["path"] for d in docs.index_docs(project)]
    # .navig/plans wins over the root copy — the same copy plan_file() reads.
    assert paths.index(".navig/plans/ROADMAP.md") < paths.index("ROADMAP.md")
    assert paths.index(".navig/plans/VISION.md") < paths.index(".navig/plans/ROADMAP.md")
    assert paths[-1].startswith(".navig/inbox/")  # the inbox sorts last


def test_groups(project: Path) -> None:
    groups = {d["path"]: d["group"] for d in docs.index_docs(project)}
    assert groups[".navig/plans/VISION.md"] == "plans"
    assert groups[".navig/vision.md"] == "plans"  # canonical stem, legacy spot
    assert groups[".navig/plans/briefs/brief-a.md"] == "briefs"
    assert groups[".navig/plans/tasks/T-001-login.md"] == "tasks"
    assert groups[".navig/inbox/note.md"] == "inbox"
    assert groups["CLAUDE.md"] == "agent"
    assert groups[".navig/ai_system_prompt.txt"] == "identity"


@pytest.mark.parametrize("rel", [
    ".navig/vault/secret.md", "../outside.md", "secret.md", "/etc/passwd",
    "C:/Windows/win.ini", ".navig/plans/../../secret.md", ".navig/plans/x.py", "",
])
def test_resolve_refuses_anything_not_listed(project: Path, rel: str) -> None:
    with pytest.raises(docs.DocError):
        docs.resolve_doc(project, rel)


def test_inbox_has_its_own_cap(project: Path) -> None:
    inbox = project / ".navig" / "inbox"
    for i in range(docs._MAX_INBOX + 25):
        (inbox / f"n{i:03}.md").write_bytes(b"# n\n")
    listed = [d for d in docs.index_docs(project) if d["group"] == "inbox"]
    assert len(listed) == docs._MAX_INBOX
    # …and the plans are still all there.
    assert any(d["path"] == ".navig/plans/DEV_PLAN.md" for d in docs.index_docs(project))


@pytest.mark.parametrize("eol", ["\n", "\r\n"])
def test_save_keeps_line_endings_and_guards_staleness(project: Path, eol: str) -> None:
    doc = project / ".navig" / "plans" / "VISION.md"
    doc.write_bytes(f"# V{eol}{eol}one{eol}".encode())
    read = docs.read_doc(project, ".navig/plans/VISION.md")
    assert read["eol"] == ("crlf" if eol == "\r\n" else "lf")

    docs.save_doc(project, ".navig/plans/VISION.md", "# V\n\ntwo\n", expect=read["fingerprint"])
    assert doc.read_bytes() == f"# V{eol}{eol}two{eol}".encode()

    with pytest.raises(docs.DocError) as exc:  # the old fingerprint is now stale
        docs.save_doc(project, ".navig/plans/VISION.md", "# V\n\nthree\n", expect=read["fingerprint"])
    assert exc.value.status == 409
    assert b"two" in doc.read_bytes()


def test_a_non_utf8_doc_is_readable_but_never_rewritten(project: Path) -> None:
    doc = project / ".navig" / "plans" / "SPEC.md"
    doc.write_bytes("# Spécification\n".encode("cp1252"))
    read = docs.read_doc(project, ".navig/plans/SPEC.md")
    assert read["editable"] is False
    with pytest.raises(docs.DocError) as exc:
        docs.save_doc(project, ".navig/plans/SPEC.md", "# x\n")
    assert exc.value.status == 422
    assert doc.read_bytes() == "# Spécification\n".encode("cp1252")


def test_save_creates_a_new_doc(project: Path) -> None:
    res = docs.save_doc(project, ".navig/plans/briefs/new.md", "# New\n")
    assert res["path"] == ".navig/plans/briefs/new.md"
    assert (project / ".navig" / "plans" / "briefs" / "new.md").read_bytes() == b"# New\n"
