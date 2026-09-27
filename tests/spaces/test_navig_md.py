"""NAVIG.md canonical context: scaffold, the migration matrix, config composition.

Run: cd core && python -m pytest tests/spaces/test_navig_md.py -q
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def cfg_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path / "cfg"))
    return tmp_path


def test_fresh_scaffold_creates_navig_md_and_pointer(cfg_env):
    from navig.commands.space import _scaffold_space_skeleton

    proj = cfg_env / "fresh"
    proj.mkdir()
    _scaffold_space_skeleton(proj, "fresh", dry_run=False)

    navig_md = (proj / "NAVIG.md").read_text(encoding="utf-8")
    claude = (proj / "CLAUDE.md").read_text(encoding="utf-8")
    assert "space: fresh" in navig_md
    assert "navig:agent-instructions:start" in navig_md
    assert "@NAVIG.md" in claude
    assert "navig:context:start" in claude


def test_migrate_legacy_claude_preserves_bytes(cfg_env):
    from navig.commands.space import _migrate_context

    proj = cfg_env / "legacy"
    (proj / ".navig").mkdir(parents=True)
    original = "# Hand written\n\nDo the thing.\n"
    (proj / "CLAUDE.md").write_text(original, encoding="utf-8")
    (proj / ".navig" / "ai_system_prompt.txt").write_text(
        "You are helpful.\n# ====== PROJECT VISION CONTEXT (auto-injected by NAVIG) ======\n"
        "Legacy is a migration demo.\n", encoding="utf-8")

    msgs = _migrate_context(proj, "legacy")
    new_claude = (proj / "CLAUDE.md").read_text(encoding="utf-8")

    assert (proj / "NAVIG.md").exists()
    assert new_claude.startswith(original.rstrip("\n"))  # bytes intact above
    assert "@NAVIG.md" in new_claude
    assert "Legacy is a migration demo." in (proj / "NAVIG.md").read_text(encoding="utf-8")
    assert any("NAVIG.md" in m for m in msgs)

    # idempotent
    assert _migrate_context(proj, "legacy") == []


def test_migrate_handrolled_pointer_untouched(cfg_env):
    from navig.commands.space import _migrate_context, _navig_md_template

    proj = cfg_env / "ref"
    proj.mkdir()
    (proj / "NAVIG.md").write_text(_navig_md_template("ref"), encoding="utf-8")
    before = "# hand\n\nSee NAVIG.md for the details.\n"
    (proj / "CLAUDE.md").write_text(before, encoding="utf-8")

    _migrate_context(proj, "ref")
    assert (proj / "CLAUDE.md").read_text(encoding="utf-8") == before


def test_migrate_no_claude_creates_pointer(cfg_env):
    from navig.commands.space import _migrate_context

    proj = cfg_env / "nocl"
    proj.mkdir()
    _migrate_context(proj, "nocl")
    assert (proj / "NAVIG.md").exists()
    assert "@NAVIG.md" in (proj / "CLAUDE.md").read_text(encoding="utf-8")


def test_config_composes_navig_md(cfg_env, monkeypatch):
    from navig.commands.space import _navig_md_template

    proj = cfg_env / "comp"
    (proj / ".navig").mkdir(parents=True)
    (proj / "NAVIG.md").write_text(
        _navig_md_template("comp", vision_seed="Comp builds widgets."), encoding="utf-8")
    monkeypatch.chdir(proj)

    from navig.config import ConfigManager

    prompt = ConfigManager().get_ai_system_prompt()
    assert "## Project context (NAVIG.md)" in prompt
    assert "Comp builds widgets." in prompt
    assert "does not grant" in prompt  # safety framing present


def test_config_caps_project_context(cfg_env, monkeypatch):
    proj = cfg_env / "big"
    (proj / ".navig").mkdir(parents=True)
    (proj / "NAVIG.md").write_text("---\nspace: big\n---\n" + ("x" * 40000), encoding="utf-8")
    monkeypatch.chdir(proj)

    from navig.config import ConfigManager

    prompt = ConfigManager().get_ai_system_prompt()
    assert "truncated at 16" in prompt


def test_config_no_navig_md_is_legacy_verbatim(cfg_env, monkeypatch):
    proj = cfg_env / "plain"
    (proj / ".navig").mkdir(parents=True)
    monkeypatch.chdir(proj)

    from navig.config import ConfigManager

    prompt = ConfigManager().get_ai_system_prompt()
    assert "## Project context (NAVIG.md)" not in prompt


# ── The operator's task list reaches agents working in a space ───────────────


TASK_HEADING = "## The operator's task list"


def test_a_fresh_space_tells_its_agents_where_the_task_list_is(cfg_env):
    """An agent that does not know `task_add` exists files what it finds into a chat
    message the operator scrolls past, or into a checklist that dies with the chat."""
    from navig.commands.space import _scaffold_space_skeleton

    proj = cfg_env / "fresh-tasks"
    proj.mkdir()
    _scaffold_space_skeleton(proj, "fresh-tasks", dry_run=False)

    body = (proj / "NAVIG.md").read_text(encoding="utf-8")
    assert TASK_HEADING in body
    assert "task_add" in body and "task_list" in body
    assert "navig:task-list:start" in body, "marker-fenced so it can be appended later"


def test_an_older_space_gets_the_guidance_appended_without_losing_a_byte(cfg_env):
    """Spaces scaffolded before the PIM shipped are the ones with the most written
    down in them — and their agents had no idea the list existed."""
    from navig.commands.space import _migrate_context

    proj = cfg_env / "older"
    (proj / ".navig").mkdir(parents=True)
    original = "---\nspace: older\n---\n# Older\n\nHand-written context.\n"
    (proj / "NAVIG.md").write_text(original, encoding="utf-8")

    msgs = _migrate_context(proj, "older")

    body = (proj / "NAVIG.md").read_text(encoding="utf-8")
    assert body.startswith(original.rstrip("\n")), "every existing byte, still above it"
    assert TASK_HEADING in body
    assert any("task-list" in m for m in msgs), f"the repair must say what it did: {msgs}"


def test_appending_the_guidance_twice_does_not_duplicate_it(cfg_env):
    from navig.commands.space import _migrate_context

    proj = cfg_env / "twice"
    (proj / ".navig").mkdir(parents=True)
    (proj / "NAVIG.md").write_text("# Twice\n", encoding="utf-8")

    _migrate_context(proj, "twice")
    _migrate_context(proj, "twice")

    body = (proj / "NAVIG.md").read_text(encoding="utf-8")
    assert body.count(TASK_HEADING) == 1


def test_the_pre_marker_wording_is_recognised(cfg_env):
    """A space scaffolded between the PIM shipping and the markers landing already
    has the guidance — telling it to add what it has is how a doctor loses trust."""
    from navig.commands.space import _migrate_context

    proj = cfg_env / "premarker"
    (proj / ".navig").mkdir(parents=True)
    (proj / "NAVIG.md").write_text(f"# Pre\n\n{TASK_HEADING}\nUse `task_add`.\n", encoding="utf-8")

    _migrate_context(proj, "premarker")

    body = (proj / "NAVIG.md").read_text(encoding="utf-8")
    assert body.count(TASK_HEADING) == 1
    assert "navig:task-list:start" not in body, "nothing was appended"


def test_doctor_reports_the_missing_guidance_and_fix_clears_it(cfg_env):
    """The row has to be repairable. A check `--fix` cannot satisfy is a warning
    that can never go green, which trains the operator to skim past the report."""
    from navig.commands.space import _diagnose_space, _migrate_context

    proj = cfg_env / "diag"
    (proj / ".navig").mkdir(parents=True)
    (proj / "NAVIG.md").write_text("# Diag\n", encoding="utf-8")

    def _row(diag):
        for group in diag["groups"]:
            for check in group["checks"]:
                if "task-list guidance" in check["label"]:
                    return check
        return None

    before = _row(_diagnose_space(proj, "diag"))
    assert before is not None, "the doctor must have a row for it at all"
    assert before["status"] != "ok"

    _migrate_context(proj, "diag")

    after = _row(_diagnose_space(proj, "diag"))
    assert after["status"] == "ok", "the repair the row points at must actually fix it"
