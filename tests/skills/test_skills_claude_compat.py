"""Claude Code / Agent Skill compatibility for the NAVIG skill loader.

A SKILL.md authored for Claude Code (just ``name`` + ``description``, optionally
``allowed-tools``) must load unchanged in NAVIG, mapping ``allowed-tools`` → tools
and defaulting to a safe, cross-platform, user-invocable skill.
"""

from pathlib import Path

import pytest

from navig.skills.loader import parse_skill_file

pytestmark = pytest.mark.integration


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_claude_skill_with_allowed_tools(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "pdf-processing" / "SKILL.md",
        """---
name: pdf-processing
description: Extract text and tables from PDF files.
allowed-tools:
  - bash
  - read
---

# PDF processing

Do the thing.
""",
    )
    skill = parse_skill_file(p)
    assert skill is not None
    assert skill.id == "pdf-processing"
    assert skill.name == "pdf-processing"
    assert skill.description == "Extract text and tables from PDF files."
    # allowed-tools maps onto NAVIG's tools
    assert skill.tools == ["bash", "read"]
    # Claude defaults: safe, cross-platform, user-invocable
    assert skill.safety == "safe"
    assert skill.user_invocable is True
    assert skill.platforms == ["linux", "macos", "windows"]


def test_minimal_claude_skill_name_description_only(tmp_path: Path) -> None:
    # Mirrors openclaw/Anthropic skills that ship only name + description.
    p = _write(
        tmp_path / "qr-code" / "SKILL.md",
        """---
name: qr-code
description: Generate QR codes from text or URLs.
metadata:
  emoji: "🔳"
---

Body.
""",
    )
    skill = parse_skill_file(p)
    assert skill is not None
    assert skill.name == "qr-code"
    assert skill.description.startswith("Generate QR codes")
    assert skill.user_invocable is True
    assert skill.safety == "safe"


def test_allowed_tools_as_comma_string(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "deploy" / "SKILL.md",
        """---
name: deploy
description: Deploy the app.
allowed-tools: bash, ssh, read
---

Body.
""",
    )
    skill = parse_skill_file(p)
    assert skill is not None
    assert skill.tools == ["bash", "ssh", "read"]


def test_navig_native_format_still_wins(tmp_path: Path) -> None:
    # A NAVIG-native skill (id/tags/safety present) must NOT be downgraded to the
    # Claude adapter even though it also has name + description.
    p = _write(
        tmp_path / "native" / "SKILL.md",
        """---
id: native-skill
name: native-skill
description: A native skill.
safety: elevated
tags: ops, infra
---

Body.
""",
    )
    skill = parse_skill_file(p)
    assert skill is not None
    assert skill.id == "native-skill"
    assert skill.safety == "elevated"
    assert skill.tags == ["ops", "infra"]
