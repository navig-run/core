"""No U+FFFD (the "�" replacement character) in shipped Python source.

U+FFFD is what a decoder writes when bytes were not valid in the encoding it assumed. It is
never what someone meant to type. One reached users twice in the same way: a migration
re-saved telegram_keyboards.py and the Russian button labels "📖 Глубже" / "🔁 Новые идеи"
became "� Глубже" / "� Новые идеи" in every chat, while the English labels next to them
kept their emoji. The identity default in workspace.py had carried one since its first
commit.

A guard over the whole tree (core + every plugin + harbor), so it is in sourceGuardArgs: no
module name selects it. Empty baseline. A "�" in source is lost data: restore what was
there, or write it as an escape if a real U+FFFD is ever intended.
"""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
ROOTS = [REPO / "core" / "navig", REPO / "plugins", REPO / "private" / "harbor"]


def _sources() -> list[Path]:
    files: list[Path] = []
    for root in ROOTS:
        if not root.is_dir():
            continue
        for p in root.rglob("*.py"):
            rel = p.relative_to(root).parts
            if any(part.startswith(".") or part == "__pycache__" for part in rel):
                continue
            files.append(p)
    return files


def test_the_scan_reaches_core_and_plugins():
    files = _sources()
    assert (REPO / "core" / "navig").is_dir() and (REPO / "plugins").is_dir()
    assert sum(1 for f in files if "plugins" in f.parts) > 50, "the plugin tree was not scanned"
    assert sum(1 for f in files if "navig" in f.parts) > 500, "core was not scanned"


def test_no_replacement_character_in_source():
    hits = []
    for p in _sources():
        text = p.read_bytes().decode("utf-8", errors="replace")
        for n, line in enumerate(text.splitlines(), 1):
            if "\ufffd" in line:
                hits.append(f"{p.relative_to(REPO).as_posix()}:{n}: {line.strip()[:80]}")
    assert not hits, "U+FFFD (lost characters) in source:\n" + "\n".join(hits)
