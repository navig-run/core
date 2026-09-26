"""A skill whose frontmatter is wrapped in a code fence must still load, and must say so.

The bug this pins
-----------------
Fifteen builtin skills shipped with the whole file inside a ```skill fence. The loader
reads frontmatter only at byte 0, so it fell through to the plain-Markdown fallback and
every declared field -- name, description, navig-commands, user-invocable -- was silently
discarded. A ``Skill`` was still returned, so nothing looked wrong; the description was
simply empty, and a skill with no description is one the model never picks.

The builtin ones are repaired and guarded in the repo. This covers the user who makes the
same mistake in their OWN skill: it must load with its declared fields, and it must be
told, because the file is still wrong for every other loader.
"""

from pathlib import Path

import pytest
from loguru import logger

from navig.skills.loader import parse_skill_file

pytestmark = pytest.mark.integration

FENCE = "`" * 3

FRONTMATTER = """---
name: fenced-demo
description: A skill whose frontmatter is wrapped in a code fence.
user-invocable: true
navig-commands:
  - navig demo run
---

# Fenced demo

Body text.
"""


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def warnings() -> list[str]:
    """Loguru does not route through ``logging``, so ``caplog`` never sees the loader."""
    seen: list[str] = []
    handle = logger.add(lambda m: seen.append(m.record["message"]), level="WARNING")
    try:
        yield seen
    finally:
        logger.remove(handle)


def test_a_fenced_skill_loads_its_declared_fields(tmp_path: Path, warnings: list[str]) -> None:
    p = _write(tmp_path / "fenced-demo" / "SKILL.md", f"{FENCE}skill\n{FRONTMATTER}{FENCE}\n")
    skill = parse_skill_file(p)
    assert skill is not None
    # The declared fields, not the folder-name fallback.
    assert skill.name == "fenced-demo"
    assert skill.description == "A skill whose frontmatter is wrapped in a code fence."
    assert skill.user_invocable is True
    # And the author is told, naming the file, because every other loader will drop these.
    assert any("code fence" in m and "fenced-demo" in m for m in warnings), warnings


def test_the_same_skill_unfenced_loads_identically_and_silently(
    tmp_path: Path, warnings: list[str]
) -> None:
    """The control: the tolerant path must not change what a correct file yields."""
    p = _write(tmp_path / "plain-demo" / "SKILL.md", FRONTMATTER.replace("fenced-demo", "plain-demo"))
    skill = parse_skill_file(p)
    assert skill is not None
    assert skill.name == "plain-demo"
    assert skill.description == "A skill whose frontmatter is wrapped in a code fence."
    assert not any("code fence" in m for m in warnings), warnings


def test_a_file_that_merely_begins_with_a_code_sample_is_untouched(
    tmp_path: Path, warnings: list[str]
) -> None:
    """A leading fence that does NOT wrap frontmatter is legitimate Markdown -- leave it."""
    body = f"{FENCE}bash\nnavig demo run\n{FENCE}\n\n# Sample-first skill\n\nExplanation.\n"
    p = _write(tmp_path / "sample-first" / "SKILL.md", body)
    skill = parse_skill_file(p)
    assert skill is not None
    # Plain-Markdown fallback, exactly as before: id/name from the folder.
    assert skill.id == "sample-first"
    assert not any("code fence" in m for m in warnings), warnings


def test_a_fenced_skill_without_a_closing_fence_still_loads(tmp_path: Path) -> None:
    """Half-wrapped is the likelier hand-edit; the opening fence alone must not brick it."""
    p = _write(tmp_path / "half-fenced" / "SKILL.md", f"{FENCE}skill\n{FRONTMATTER}")
    skill = parse_skill_file(p)
    assert skill is not None
    assert skill.name == "fenced-demo"
