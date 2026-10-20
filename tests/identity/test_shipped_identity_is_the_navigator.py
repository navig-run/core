"""The shipped default identity is **The Navigator**, not a "Living Octopus-Mind".

Owner decision (2026-07-08, `58aec4725f`): the default identity across runtime surfaces is
**NAVIG — The Navigator**; the octopus stays as the visual mark and the *Octopus Doctrine*
keeps its name, but "Octopus-Mind" is retired as the identity noun. Commit `0857b0760e`
(#144, packaging) re-shipped the old identity inside `core/navig/builtin/`, so every fresh
install wrote it back into the Operator's prompt file. Nothing caught that — until this file.

The guard scans the surfaces that are shipped with every install, so the retired identity
cannot silently return through a packaging or prompt edit.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
BUILTIN = REPO_ROOT / "core" / "navig" / "builtin"
RESOURCES = REPO_ROOT / "core" / "navig" / "resources"

#: "octopus-mind", "Octopus‑Mind" (U+2011 non-breaking hyphen), "octopus mind".
RETIRED_IDENTITY = re.compile(r"octopus[\s\u2010\u2011-]*mind", re.IGNORECASE)

#: (root, glob) pairs covering every prompt/persona file a new install can receive.
IDENTITY_SURFACES: tuple[tuple[Path, str], ...] = (
    (BUILTIN / "prompts", "*.md"),
    (BUILTIN / "agents", "*.md"),
    (BUILTIN / "agents", "*.json"),
    (RESOURCES, "*.md"),
)

#: Files a fresh install reads as the identity itself — each must name The Navigator.
PRIMARY_IDENTITY_FILES = (
    BUILTIN / "prompts" / "system" / "main.md",
    BUILTIN / "prompts" / "system" / "core_identity.md",
    BUILTIN / "prompts" / "system" / "default_ai_prompt.md",
    BUILTIN / "agents" / "navig" / "persona.md",
    BUILTIN / "agents" / "navig" / "soul.md",
    BUILTIN / "agents" / "navig" / "space.json",
)


def identity_files() -> list[Path]:
    """Every shipped prompt/persona file, deduplicated and stable-ordered."""
    seen: dict[Path, None] = {}
    for root, pattern in IDENTITY_SURFACES:
        for path in sorted(root.rglob(pattern)):
            if path.is_file() and "__pycache__" not in path.parts:
                seen.setdefault(path, None)
    return list(seen)


def violations(files: list[Path]) -> list[str]:
    """`path:line: text` for every line that names the retired identity."""
    found: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if RETIRED_IDENTITY.search(line):
                rel = path.relative_to(REPO_ROOT).as_posix()
                found.append(f"{rel}:{lineno}: {line.strip()[:110]}")
    return found


def test_no_shipped_identity_file_names_the_retired_mind() -> None:
    files = identity_files()
    assert len(files) > 30, f"scanned only {len(files)} files — the globs stopped matching"
    found = violations(files)
    assert not found, (
        "the retired 'Octopus-Mind' identity is back in shipped prompt content "
        "(owner decision 2026-07-08: default identity is The Navigator, the octopus is the "
        "visual mark only):\n  " + "\n  ".join(found)
    )


def test_the_guard_catches_every_spelling(tmp_path: Path) -> None:
    """Teeth: both hyphen forms, the space form, and any case are caught."""
    sample = tmp_path / "main.md"
    sample.write_text(
        "You are NAVIG - the Living Octopus-Mind.\n"
        "I am NAVIG, the living octopus\u2011mind.\n"
        "We are an octopus mind.\n"
        "The octopus stays as the mark.\n",
        encoding="utf-8",
    )
    found = violations([sample])
    assert len(found) == 3, found
    assert not RETIRED_IDENTITY.search("The octopus stays as the mark.")


def test_every_primary_identity_file_names_the_navigator() -> None:
    for path in PRIMARY_IDENTITY_FILES:
        assert path.is_file(), f"missing shipped identity file {path}"
        text = path.read_text(encoding="utf-8")
        assert "The Navigator" in text, (
            f"{path.relative_to(REPO_ROOT).as_posix()} no longer names The Navigator as the "
            "identity — a fresh install would get a nameless prompt"
        )
