"""Shared fixtures for navig.selfheal tests."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from navig.selfheal.scanner import ScanFinding


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sample_finding() -> ScanFinding:
    """A single critical-severity ScanFinding fixture."""
    return ScanFinding(
        file="navig/commands/example.py",
        line=42,
        severity="critical",
        category="bug",
        description="Bare except clause swallows all errors.",
        suggested_fix="Replace `except:` with `except Exception as exc:`.",
        confidence=0.93,
    )


@pytest.fixture()
def sample_findings(sample_finding: ScanFinding) -> list[ScanFinding]:
    """A mixed-severity list of ScanFinding fixtures."""
    return [
        sample_finding,
        ScanFinding(
            file="navig/commands/example.py",
            line=88,
            severity="high",
            category="readability",
            description="Function lacks type hints.",
            suggested_fix="Add `def foo(x: int) -> str:` signature.",
            confidence=0.85,
        ),
        ScanFinding(
            file="navig/commands/other.py",
            line=10,
            severity="medium",
            category="performance",
            description="Redundant list copy in hot path.",
            suggested_fix="Use a generator expression instead.",
            confidence=0.72,
        ),
        ScanFinding(
            file="navig/commands/other.py",
            line=25,
            severity="low",
            category="readability",
            description="Missing docstring.",
            suggested_fix="Add a one-line docstring.",
            confidence=0.65,
        ),
    ]


@pytest.fixture()
def mock_llm_response() -> str:
    """JSON string that a real LLM would return for a scan prompt."""
    findings = [
        {
            "file": "navig/commands/example.py",
            "line": 42,
            "severity": "critical",
            "category": "bug",
            "description": "Bare except clause swallows all errors.",
            "suggested_fix": "Replace `except:` with `except Exception as exc:`.",
            "confidence": 0.93,
        }
    ]
    return json.dumps(findings)


@pytest.fixture()
def tmp_repo(tmp_path: Path) -> Path:
    """A minimal fake repo with a .git directory and a Python source file."""
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    src = tmp_path / "navig" / "commands"
    src.mkdir(parents=True)
    (src / "example.py").write_text(
        "def foo():\n"
        "    try:\n"
        "        pass\n"
        "    except:\n"
        "        pass\n",
        encoding="utf-8",
    )
    return tmp_path
