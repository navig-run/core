"""`navig skill list` and the runtime loader must read a skill the SAME way.

The drift this pins
-------------------
``commands/skills.py`` carried its own copy of the frontmatter parser for ``skill list``
and ``skill lint``. When the loader (#1380) learned to unwrap a frontmatter wrapped in a
code fence, the two disagreed about the same file: the agent loaded the skill's
description, the CLI showed it blank. Two parsers for one format always drift; the
command now delegates to the loader's ``read_frontmatter``.

``skill lint`` is the deliberate exception: it must decide on the RAW text, because a
fenced frontmatter is a defect for every other loader even though NAVIG tolerates it.
So ``list`` tolerates, ``lint`` fails -- and both must say so precisely.
"""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands.skills import list_skills_cmd, skills_app
from navig.skills.loader import read_frontmatter

pytestmark = pytest.mark.integration

FENCE = "`" * 3

FRONTMATTER = """---
name: fenced-cli
description: Description that must survive both the CLI and the runtime.
---

# Fenced CLI
"""


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_skill_list_shows_the_description_of_a_fenced_skill(tmp_path: Path) -> None:
    """The CLI must agree with the runtime, not with its former private parser."""
    skills_dir = tmp_path / "skills"
    _write(skills_dir / "demo" / "fenced-cli" / "SKILL.md", f"{FENCE}skill\n{FRONTMATTER}{FENCE}\n")
    skills = list_skills_cmd({"skills_dir": str(skills_dir), "plain": True})
    assert len(skills) == 1
    assert skills[0].name == "fenced-cli"
    assert skills[0].description == "Description that must survive both the CLI and the runtime."


def test_skill_list_flags_a_fenced_skill_and_not_a_correct_one(tmp_path: Path) -> None:
    """Tolerance must be VISIBLE in the listing the author looks at. A fenced skill loads
    (so it appears with its description) and carries a warning; a correct skill does not."""
    skills_dir = tmp_path / "skills"
    _write(skills_dir / "demo" / "fenced" / "SKILL.md", f"{FENCE}skill\n{FRONTMATTER}{FENCE}\n")
    _write(skills_dir / "demo" / "plain" / "SKILL.md", FRONTMATTER.replace("fenced-cli", "plain"))
    by_name = {s.name: s for s in list_skills_cmd({"skills_dir": str(skills_dir), "plain": True})}
    assert by_name["fenced-cli"].warning and "code fence" in by_name["fenced-cli"].warning
    assert by_name["plain"].warning is None
    # And the machine-readable listing carries it too, so an agent can act on it.
    result = CliRunner().invoke(skills_app, ["list", "--dir", str(skills_dir), "--json"], obj={})
    payload = json.loads(result.output)
    flagged = {s["name"]: s["warning"] for s in payload["skills"]}
    assert flagged["fenced-cli"] and flagged["plain"] is None, flagged


def test_read_frontmatter_rejects_a_non_mapping_instead_of_passing_it_through(tmp_path: Path) -> None:
    """The private copy returned `safe_load(...) or {}`, so a YAML LIST came back as a list
    and `.get()` crashed. The shared parser returns {} for anything that is not a mapping."""
    p = _write(tmp_path / "listy" / "SKILL.md", "---\n- just\n- a list\n---\n\n# Listy\n")
    assert read_frontmatter(p) == {}


def test_skill_lint_names_the_fence_precisely(tmp_path: Path) -> None:
    """Tolerance is not a pass: lint must FAIL a fenced frontmatter and say what to do."""
    p = _write(tmp_path / "fenced-cli" / "SKILL.md", f"{FENCE}skill\n{FRONTMATTER}{FENCE}\n")
    result = CliRunner().invoke(skills_app, ["lint", str(p), "--json"], obj={})
    assert result.exit_code == 1, result.output
    report = json.loads(result.output)
    fails = {c["check"]: c["detail"] for c in report["fails"]}
    assert "frontmatter" in fails, report
    assert "code fence" in fails["frontmatter"]
    assert "START with `---`" in fails["frontmatter"]
    # It must NOT be mistaken for the generic "no frontmatter" warning.
    assert not any(c["check"] == "frontmatter" for c in report["warns"]), report


@pytest.mark.parametrize(
    "spelling",
    ["**id:** `pk`\n**description:** `x`", "**id**: `pk`\n**description**: `x`"],
    ids=["colon-inside-bold", "colon-outside-bold"],
)
def test_skill_lint_catches_both_pseudo_key_spellings(tmp_path: Path, spelling: str) -> None:
    """The detector matched only `**id:**`. The one shipped skill written with pseudo-keys
    (win-perf-tuner) used `**id**:` and PASSED lint while the loader left its description
    empty and took its name from the H1. Both spellings are the same defect."""
    p = _write(tmp_path / "pk" / "SKILL.md", f"# Skill: pk\n\n{spelling}\n\n## Purpose\nBody.\n")
    result = CliRunner().invoke(skills_app, ["lint", str(p), "--json"], obj={})
    assert result.exit_code == 1, result.output
    fails = {c["check"]: c["detail"] for c in json.loads(result.output)["fails"]}
    assert "pseudo-keys" in fails.get("frontmatter", ""), fails


def test_skill_lint_still_passes_a_correct_skill(tmp_path: Path) -> None:
    """The control: the new branch must not fire on a file that starts with ---."""
    p = _write(tmp_path / "plain-cli" / "SKILL.md", FRONTMATTER.replace("fenced-cli", "plain-cli"))
    result = CliRunner().invoke(skills_app, ["lint", str(p), "--json"], obj={})
    report = json.loads(result.output)
    assert not any(c["check"] == "frontmatter" for c in report["fails"]), report
