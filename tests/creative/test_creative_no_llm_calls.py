"""Guard: nothing under navig/creative (or its monitor) may import an LLM.

The agent-boundary law keeps every inference call inside ``navig/agent/``.
Creative's half is deliberately deterministic Python — index reads, detectors,
a pure ladder, file/git work — so this test parses the actual import graph
rather than trusting a docstring claim ("written, tested, never wired").
"""

from __future__ import annotations

import ast
from pathlib import Path

_CORE_ROOT = Path(__file__).resolve().parents[2]
_CREATIVE = _CORE_ROOT / "navig" / "creative"
_SOURCES = sorted(_CREATIVE.glob("*.py")) + [
    _CORE_ROOT / "navig" / "notify" / "monitors" / "creative_scan.py",
]

#: Modules whose import would mean Creative is calling a model itself.
_FORBIDDEN_ROOTS = (
    "navig.llm",
    "navig.ai",
    "navig.agent.conv",
    "navig.agent.agent",  # conversational engine
    "openai",
    "anthropic",
    "google.generativeai",
    "mistralai",
)


def _imported_modules(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.append(node.module)
    return found


def test_sources_exist_not_a_husk():
    """Scan floor — an empty glob would make every assertion vacuous."""
    assert len(_SOURCES) >= 8, [str(p) for p in _SOURCES]
    assert (_CREATIVE / "index.py").exists()
    assert (_CREATIVE / "decide.py").exists()


def test_no_llm_imports_anywhere_in_creative():
    violations: list[str] = []
    for path in _SOURCES:
        for module in _imported_modules(path):
            for forbidden in _FORBIDDEN_ROOTS:
                if module == forbidden or module.startswith(forbidden + "."):
                    violations.append(f"{path.name}: imports {module}")
    assert violations == [], violations


def test_no_inference_calls_hidden_in_strings():
    """Cheap second net: common call sites pasted into a string/attribute."""
    needles = ("llm_generate", "complete_raw(", "chat.completions", "client.messages")
    hits: list[str] = []
    for path in _SOURCES:
        text = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle in text:
                hits.append(f"{path.name}: contains {needle!r}")
    assert hits == [], hits
