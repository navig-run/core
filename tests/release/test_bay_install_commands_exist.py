"""Every install command the Bay hands a user must name a navig command that exists.

The Bay catalog (what navig.run/bay and `navig` itself show) carried 30 install lines
naming verbs no navig release has ever had — `navig persona use`, `navig prompt install`,
`navig formation install`, `navig install webapp` — plus the hidden, deprecated
`navig plugin install`. A copy-paste from the store failed on the first word that mattered.
"""

from __future__ import annotations

import json
from pathlib import Path

CORE = Path(__file__).resolve().parents[2]
CATALOG = CORE / "navig" / "data" / "bay-catalog.json"
MANIFEST = CORE / "generated" / "commands.json"
DEPRECATED = {"navig plugin install"}


def _command_paths() -> tuple[set[str], set[str]]:
    """(leaf commands, groups) from the manifest."""
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    leaves = {c["path"] for c in data["commands"]}
    groups = set()
    for p in leaves:
        parts = p.split()
        for i in range(2, len(parts)):
            groups.add(" ".join(parts[:i]))
    return leaves, groups


def _problem(cmd: str, leaves: set[str], groups: set[str]) -> str | None:
    """None when `cmd` starts with a real command; otherwise why not."""
    words = cmd.split("#", 1)[0].split()
    best = None
    for n in range(len(words), 1, -1):
        prefix = " ".join(words[:n])
        if prefix in leaves or prefix in groups:
            best, rest = prefix, words[n:]
            break
    if best is None:
        return "no such command"
    if best in DEPRECATED:
        return "deprecated alias"
    # A group followed by a bare word means an unknown sub-command (`navig persona use`).
    if best in groups and best not in leaves and rest and not rest[0].startswith("-"):
        return f"`{best}` has no sub-command `{rest[0]}`"
    return None


def test_every_bay_install_command_exists() -> None:
    items = json.loads(CATALOG.read_text(encoding="utf-8"))["items"]
    assert len(items) > 50, "catalog read nothing — the check would pass vacuously"
    leaves, groups = _command_paths()
    bad = []
    checked = 0
    for it in items:
        cmd = (it.get("install") or "").strip()
        if not cmd.startswith("navig "):
            continue
        checked += 1
        why = _problem(cmd, leaves, groups)
        if why:
            bad.append(f"{it['slug']}: {cmd}  ({why})")
    assert checked > 20, f"only {checked} navig install lines found — parser broken?"
    assert not bad, "Bay install commands that do not exist:\n  " + "\n  ".join(bad)
