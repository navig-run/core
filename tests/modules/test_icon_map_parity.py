"""Guard: the desktop OS icon map must cover every registry icon.

Each built-in module names a lucide `icon` (e.g. ``icon="drama"``). The desktop
OS renders it through a hand-maintained allow-list — the ``ICONS`` map in
``apps/os/apps/webui/src/renderer/lib/modules.ts`` — that maps the kebab name to
an imported lucide component. When a new module (or a rename) introduces an icon
that isn't in that map, the app tile silently falls back to a generic box.

That drift happened repeatedly (``drama``/``flag``/``webhook`` shipped unmapped).
This test is the tripwire: every ``BUILTIN_MODULES`` icon must exist in the map,
so adding an app without wiring its icon fails CI instead of shipping a box.

Cross-language contract test: it reads the TS file as text. It only covers
core built-ins (plugins own their own icons + map entries); it skips cleanly
when the ``apps/`` tree isn't present (e.g. a shipped core-only wheel).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from navig.modules.registry import BUILTIN_MODULES

# core/tests/modules/ -> repo root -> the desktop OS icon map.
_MODULES_TS = (
    Path(__file__).resolve().parents[3]
    / "apps"
    / "os"
    / "apps"
    / "webui"
    / "src"
    / "renderer"
    / "lib"
    / "modules.ts"
)

# The `const ICONS: Record<string, LucideIcon> = { … }` block, then its keys
# (`activity:` or `'book-open':`). Values are PascalCase components with no
# trailing colon, so they never match the key pattern.
_ICONS_BLOCK = re.compile(r"const ICONS\b.*?\{(.*?)^\}", re.DOTALL | re.MULTILINE)
_ICON_KEY = re.compile(r"^\s*'?([a-z0-9-]+)'?\s*:", re.MULTILINE)


def _ts_icon_keys(text: str) -> set[str]:
    block = _ICONS_BLOCK.search(text)
    assert block, "could not locate the `const ICONS` map in modules.ts (regex stale?)"
    keys = set(_ICON_KEY.findall(block.group(1)))
    assert keys, "parsed zero ICONS keys — the modules.ts format changed; update this test"
    return keys


def test_registry_icons_are_in_desktop_os_icon_map() -> None:
    if not _MODULES_TS.exists():
        pytest.skip("apps/os tree not present (core-only checkout / shipped wheel)")

    mapped = _ts_icon_keys(_MODULES_TS.read_text(encoding="utf-8"))
    used = {m.icon for m in BUILTIN_MODULES}
    missing = sorted(used - mapped)

    assert not missing, (
        "registry module icons are missing from the desktop OS ICONS map "
        f"({_MODULES_TS.relative_to(Path(__file__).resolve().parents[3])}): {missing}. "
        "Import the lucide component and add a map entry so the app tile isn't a generic box."
    )
