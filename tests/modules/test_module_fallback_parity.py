"""Guard: the desktop OS *offline* MODULES fallback must cover every registry app.

`apps/os/apps/webui/src/renderer/lib/modules.ts` ships a hardcoded `MODULES`
array — the OFFLINE fallback rendered when the brain is unreachable (a live
`GET /api/deck/modules` overrides it). Its docstring promises *"the CI parity
test guards drift on the core side"*, and the sibling `test_icon_map_parity`
guards the lucide ICON names — but nothing guarded the app SET itself. Add an
app to the registry with an `os-tile:` surface and the offline sidebar silently
omits it (wrong until the brain is reached once); ship it in the wrong `app_category`
and it lands in the wrong sidebar group offline. That is the exact drift class the
icon test was created to kill (``drama``/``flag``/``webhook`` shipped unmapped),
left open for the app id-set + categories.

This tripwire closes the gap: every desktop-eligible ``BUILTIN_MODULES`` app
(an ``os-tile:`` surface + an ``app_category``, and not a hidden merged tab) must
appear in the ``MODULES`` fallback with the SAME ``appCategory`` and ``scope``.
The fallback may carry EXTRA entries — default-enabled first-party PLUGIN apps
(Games, Mobile) that aren't core built-ins — so the contract is
``registry ⊆ fallback``, never equality.

Cross-language contract test (mirrors `test_icon_map_parity`): it reads the TS
file as text and skips cleanly when the ``apps/`` tree isn't present (a shipped
core-only wheel).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from navig.modules.registry import BUILTIN_MODULES

# core/tests/modules/ -> repo root -> the desktop OS module catalog.
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

# The `export const MODULES: ModuleDef[] = [ … ]` array, up to the `]` that
# closes it on its own line (the next `export const` follows).
_MODULES_BLOCK = re.compile(r"export const MODULES\b[^\[]*\[(.*?)^\]", re.DOTALL | re.MULTILINE)
# One entry per line: `{ id: 'finance', …, appCategory: 'Money', scope: 'space', … }`.
_ID = re.compile(r"\bid:\s*'([^']+)'")
_APP_CATEGORY = re.compile(r"\bappCategory:\s*'([^']+)'")
_SCOPE = re.compile(r"\bscope:\s*'([^']+)'")


def _parse_fallback(text: str) -> dict[str, dict[str, str]]:
    """id → {appCategory, scope} for every object literal in the MODULES array.

    A missing `scope` defaults to ``brain`` (the ModuleDef default), so an entry
    that omits it is not falsely flagged as drift."""
    block = _MODULES_BLOCK.search(text)
    assert block, "could not locate the `export const MODULES` array in modules.ts (regex stale?)"
    out: dict[str, dict[str, str]] = {}
    for line in block.group(1).splitlines():
        mid = _ID.search(line)
        if not mid:
            continue
        cat = _APP_CATEGORY.search(line)
        scope = _SCOPE.search(line)
        out[mid.group(1)] = {
            "appCategory": cat.group(1) if cat else "",
            "scope": scope.group(1) if scope else "brain",
        }
    assert out, "parsed zero MODULES entries — the modules.ts format changed; update this test"
    return out


def _expected_desktop_apps() -> dict[str, dict[str, str]]:
    """The registry's desktop-eligible built-ins — the same predicate the OS
    selector (`isDesktopApp` + `collectDesktopApps`) uses to render a tile:
    an `os-tile:` surface, an `app_category`, and not a hidden merged tab."""
    expected: dict[str, dict[str, str]] = {}
    for m in BUILTIN_MODULES:
        if m.hidden:
            continue
        if not any(s.startswith("os-tile:") for s in m.surfaces):
            continue
        if m.app_category is None:
            continue
        expected[m.id] = {"appCategory": m.app_category, "scope": m.scope}
    return expected


def test_offline_fallback_covers_every_registry_app() -> None:
    if not _MODULES_TS.exists():
        pytest.skip("apps/os tree not present (core-only checkout / shipped wheel)")

    fallback = _parse_fallback(_MODULES_TS.read_text(encoding="utf-8"))
    expected = _expected_desktop_apps()

    missing = sorted(mid for mid in expected if mid not in fallback)
    assert not missing, (
        "registry desktop apps missing from the OS offline MODULES fallback "
        f"({_MODULES_TS.relative_to(Path(__file__).resolve().parents[3])}): {missing}. "
        "Add each to the `MODULES` array (with icon, appCategory, scope) so the "
        "offline sidebar isn't missing apps until the brain is first reached."
    )

    mismatched: list[str] = []
    for mid, want in expected.items():
        got = fallback[mid]
        if got["appCategory"] != want["appCategory"]:
            mismatched.append(
                f"{mid}: appCategory registry={want['appCategory']!r} fallback={got['appCategory']!r}"
            )
        if got["scope"] != want["scope"]:
            mismatched.append(
                f"{mid}: scope registry={want['scope']!r} fallback={got['scope']!r}"
            )
    assert not mismatched, (
        "OS offline MODULES fallback disagrees with the registry — the offline "
        "sidebar would group/scope these apps wrong:\n  " + "\n  ".join(sorted(mismatched))
    )
