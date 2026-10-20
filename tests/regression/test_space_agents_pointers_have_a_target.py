"""`navig space doctor --agents` must never write a pointer to a file that does not exist.

Every assistant pointer it writes (AGENTS.md, GEMINI.md, Copilot, Cursor) says "read
NAVIG.md". On a space with no NAVIG.md it wrote all four anyway and skipped CLAUDE.md
("run --fix first") — so every assistant was pointed at nothing, and the only way to
get the two files that mattered was --fix, which also scaffolds ~60 directories.
"""

from __future__ import annotations

from pathlib import Path

from navig.commands.space import _TASKS_MARKER_START, _wire_agents


def test_agents_on_a_bare_space_creates_the_target_and_claude_md(tmp_path: Path) -> None:
    _wire_agents(tmp_path, "bare-space")

    navig_md = tmp_path / "NAVIG.md"
    assert navig_md.is_file()
    assert "NAVIG.md" in (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
    text = navig_md.read_text(encoding="utf-8")
    assert _TASKS_MARKER_START in text
    assert "A one-off reminder is a task with an exact time" in text
    for pointer in ("AGENTS.md", "GEMINI.md"):
        assert (tmp_path / pointer).is_file()


def test_agents_leaves_existing_instruction_bytes_untouched(tmp_path: Path) -> None:
    claude = tmp_path / "CLAUDE.md"
    original = "# My space\n\nPrivate rules the operator wrote.\n"
    claude.write_text(original, encoding="utf-8")

    _wire_agents(tmp_path, "kept-space")

    after = claude.read_text(encoding="utf-8")
    assert after.startswith(original.rstrip("\n"))  # only an import appended at the end
    assert "@NAVIG.md" in after


def test_agents_is_idempotent(tmp_path: Path) -> None:
    _wire_agents(tmp_path, "twice-space")
    snapshot = {p.name: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    _wire_agents(tmp_path, "twice-space")
    again = {p.name: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert again == snapshot
