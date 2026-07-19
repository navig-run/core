"""
Guard tests for the deck→desktop apps migration (locked 2026-07-09; deck code
deleted app-by-app through 2026-07-12 — the desktop OS hosts the full catalog).

The migration-complete contract these tripwires enforce:
1. Surface split — telegram + wallet stay deck-only (no `os-tile:`); every
   other catalog APP renders on the desktop (`os-tile:`) unless it is a hidden
   merged tab. Exclusions are by construction, never special-case UI code.
2. No builtin outside the deck-native set may declare a `deck-section:`
   surface — the deck apps grid is gone. The two deliberate exceptions are
   surfaces the deck still ships OUTSIDE the grid (Context embedded in
   Settings; the Inbox tab).
"""

from __future__ import annotations

from navig.modules.registry import APP_CATEGORY_ORDER, BUILTIN_MODULES, ModuleKind

# Deck-only apps: coupled to the Telegram Mini App runtime (TON connect / MTProto).
DECK_ONLY = {"telegram", "wallet"}

# Deck surfaces that legitimately remain OUTSIDE the (deleted) apps grid:
# context + vault + personas render embedded in deck Settings (Context /
# Vault / AI persona); inbox is the deck's Inbox TAB (asks + drop-upload).
DECK_EMBEDDED = {"context", "inbox", "vault", "personas"}


def _builtin(mid: str):
    for m in BUILTIN_MODULES:
        if m.id == mid:
            return m
    return None


def test_deck_only_apps_have_no_os_tile():
    """Telegram Manager + TON Wallet are deck-only — excluded from the desktop
    sidebar by construction (no os-tile: surface), never by special-case code."""
    for mid in sorted(DECK_ONLY):
        m = _builtin(mid)
        assert m is not None, f"{mid} missing from BUILTIN_MODULES"
        os_tiles = [s for s in m.surfaces if s.startswith("os-tile:")]
        assert not os_tiles, f"{mid} must stay deck-only but declares {os_tiles}"
        deck_sections = [s for s in m.surfaces if s.startswith("deck-section:")]
        assert deck_sections, f"{mid} is deck-native — it must declare its deck-section surface"


def test_desktop_apps_have_os_tile():
    """The migrated desktop set is renderable: each declares an os-tile surface."""
    desktop_set = {
        "finance", "projects", "messages", "studio", "tasks", "system", "remote",
        "database", "nettools", "connections", "context", "inbox", "spaces",
        "knowledge", "passport", "vault", "life", "goals", "devops",
    }
    for mid in sorted(desktop_set):
        m = _builtin(mid)
        assert m is not None, f"{mid} missing from BUILTIN_MODULES"
        assert any(s.startswith("os-tile:") for s in m.surfaces), f"{mid}: no os-tile surface"


def test_every_catalog_app_renders_somewhere():
    """Inverted tripwire (migration-complete contract): every kind=APP builtin
    with an app_category must declare an os-tile surface — unless it is
    deck-only (telegram/wallet) or a hidden merged tab (renders inside its
    host app). An app that fails this is invisible on every surface."""
    for m in BUILTIN_MODULES:
        if m.kind is not ModuleKind.APP or m.app_category is None:
            continue
        if m.id in DECK_ONLY:
            continue
        if m.hidden and m.merged_into is not None:
            continue
        assert any(s.startswith("os-tile:") for s in m.surfaces), (
            f"{m.id}: a catalog app with app_category={m.app_category!r} but no "
            f"os-tile: surface — it renders nowhere (the deck grid is gone)"
        )


def test_no_stray_deck_sections():
    """The deck apps grid was deleted: no builtin outside the deck-native set
    (+ the two deliberate embedded surfaces) may declare a deck-section:
    surface. A new deck-section here means someone is re-growing the grid —
    the desktop OS is the app surface now."""
    allowed = DECK_ONLY | DECK_EMBEDDED
    for m in BUILTIN_MODULES:
        deck_sections = [s for s in m.surfaces if s.startswith("deck-section:")]
        if deck_sections:
            assert m.id in allowed, (
                f"{m.id}: declares {deck_sections} but the deck apps grid was "
                f"deleted (deck→OS migration). Deck-native exceptions: {sorted(allowed)}"
            )


def test_app_categories_are_canonical():
    """app_category values come from the locked seven-group vocabulary, AND every
    desktop (os-tile) app declares one.

    The second half is the important tripwire: the desktop grid/sidebar
    (`collectDesktopApps` in os `lib/modules.ts`) silently DROPS any os-tile app
    whose `app_category` is None — so a new app added with an os-tile surface but
    no category vanishes from the UI with no error. Data-driven so it auto-covers
    every future app, unlike the hardcoded lists above.
    """
    for m in BUILTIN_MODULES:
        if m.app_category is not None:
            assert m.app_category in APP_CATEGORY_ORDER, (
                f"{m.id}: app_category {m.app_category!r} not in {APP_CATEGORY_ORDER}"
            )
        elif any(s.startswith("os-tile:") for s in m.surfaces):
            raise AssertionError(
                f"{m.id}: declares an os-tile: surface but has no app_category — it "
                f"would be invisible in the desktop grid + sidebar. Set one of "
                f"{APP_CATEGORY_ORDER}."
            )


def test_merged_apps_reference_existing_hosts():
    """A hidden merged tile (contacts→messages, schedule/health→life) must point
    at a real host app, and hidden tiles never carry an os-tile surface."""
    builtin_ids = {m.id for m in BUILTIN_MODULES}
    for m in BUILTIN_MODULES:
        if m.merged_into is not None:
            assert m.hidden, f"{m.id}: merged_into set but not hidden"
            assert m.merged_into in builtin_ids, f"{m.id}: merged host {m.merged_into!r} missing"
            assert not any(s.startswith("os-tile:") for s in m.surfaces), (
                f"{m.id}: hidden merged tile must not declare an os-tile surface"
            )


def test_to_dict_carries_app_surface_fields():
    """The wire shape (`GET /api/deck/modules`) exposes the new metadata."""
    row = _builtin("system").to_dict(capabilities=["core_ops"], enabled=True)
    assert row["scope"] == "brain"
    assert row["app_category"] == "Systems"
    assert row["hidden"] is False
    assert row["merged_into"] is None
    merged = _builtin("contacts").to_dict(capabilities=["core_ops"], enabled=True)
    assert merged["hidden"] is True
    assert merged["merged_into"] == "messages"
    # Finance follows the active space's BOOK (locked rule #6) — scope=space.
    assert _builtin("finance").scope == "space"
    assert _builtin("projects").scope == "space"


def test_reset_entry_points_rearms_discovery(monkeypatch):
    """After an in-process install, `reset_entry_points()` must clear the
    once-guard so the next `discover()` re-runs entry-point registration —
    the no-daemon-restart contract for Bay/store installs."""
    import navig.modules.registry as reg_mod

    monkeypatch.setattr(reg_mod, "_EP_PLUGINS_LOADED", True)
    reg_mod.reset_entry_points()
    assert reg_mod._EP_PLUGINS_LOADED is False
