"""
Guard tests for the deck→desktop apps migration (locked 2026-07-09; deck code
deleted app-by-app through 2026-07-12 — the desktop OS hosts the full catalog).

The migration-complete contract these tripwires enforce:
1. Surface split — wallet stays deck-only (no `os-tile:`); telegram renders on
   BOTH (the Mini App keeps Settings → Telegram, NAVIG OS has the Telegram app);
   every other catalog APP renders on the desktop (`os-tile:`) unless it is a
   hidden merged tab. Exclusions are by construction, never special-case UI code.
2. No builtin outside the deck-native set may declare a `deck-section:`
   surface — the deck apps grid is gone. The two deliberate exceptions are
   surfaces the deck still ships OUTSIDE the grid (Context embedded in
   Settings; the Inbox tab).
"""

from __future__ import annotations

from navig.modules.registry import APP_CATEGORY_ORDER, BUILTIN_MODULES, ModuleKind

# Deck-only apps: coupled to the Telegram Mini App runtime (TON connect).
DECK_ONLY = {"wallet"}

# Apps that render on BOTH surfaces: a deck-section in the Mini App AND an os-tile
# on the desktop. Telegram moved here when NAVIG OS gained its Telegram app.
DECK_AND_DESKTOP = {"telegram"}

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
    """TON Wallet is deck-only — excluded from the desktop sidebar by construction
    (no os-tile: surface), never by special-case code."""
    for mid in sorted(DECK_ONLY):
        m = _builtin(mid)
        assert m is not None, f"{mid} missing from BUILTIN_MODULES"
        os_tiles = [s for s in m.surfaces if s.startswith("os-tile:")]
        assert not os_tiles, f"{mid} must stay deck-only but declares {os_tiles}"
        deck_sections = [s for s in m.surfaces if s.startswith("deck-section:")]
        assert deck_sections, f"{mid} is deck-native — it must declare its deck-section surface"


def test_dual_surface_apps_declare_both():
    """Telegram renders in the Mini App (deck-section) AND on the desktop (os-tile)."""
    for mid in sorted(DECK_AND_DESKTOP):
        m = _builtin(mid)
        assert m is not None, f"{mid} missing from BUILTIN_MODULES"
        assert any(s.startswith("os-tile:") for s in m.surfaces), f"{mid}: no os-tile surface"
        assert any(s.startswith("deck-section:") for s in m.surfaces), f"{mid}: no deck-section surface"
        assert m.app_category is not None, f"{mid}: an os-tile app needs an app_category"


def test_desktop_apps_have_os_tile():
    """The migrated desktop set is renderable: each declares an os-tile surface."""
    # NB: "social" (the compose/schedule/publish app) is NOT here — it's a PLUGIN
    # app (navig-social, os-tile:social), like games/mobile/generate/email, so it
    # isn't in BUILTIN_MODULES. It used to be a duplicate builtin `studio` tile;
    # consolidated into the plugin module 2026-07-20.
    desktop_set = {
        "finance", "projects", "messages", "tasks", "system", "remote",
        "database", "nettools", "connections", "context", "inbox", "spaces",
        "knowledge", "passport", "vault", "life", "goals", "devops", "telegram", "discord",
    }
    for mid in sorted(desktop_set):
        m = _builtin(mid)
        assert m is not None, f"{mid} missing from BUILTIN_MODULES"
        assert any(s.startswith("os-tile:") for s in m.surfaces), f"{mid}: no os-tile surface"


def test_every_catalog_app_renders_somewhere():
    """Inverted tripwire (migration-complete contract): every kind=APP builtin
    with an app_category must declare an os-tile surface — unless it is
    deck-only (wallet) or a hidden merged tab (renders inside its
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
    allowed = DECK_ONLY | DECK_AND_DESKTOP | DECK_EMBEDDED
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
    """A merged app must point at a real host app, as one of two kinds:

    - hidden (contacts→messages, schedule/health→life): ALWAYS a tab of the host,
      so it never carries an os-tile surface of its own;
    - conditional (hidden=False + merged_section): a section of the host while the
      host can show it, its own tile otherwise — so it MUST keep its os-tile, or a
      Free user with a locked host would see it nowhere.
    """
    builtin_ids = {m.id for m in BUILTIN_MODULES}
    for m in BUILTIN_MODULES:
        if m.merged_into is not None:
            assert m.merged_into in builtin_ids, f"{m.id}: merged host {m.merged_into!r} missing"
            assert_merge_kind_is_coherent(m)


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
    # The Projects hub lists every project on the brain — not one space's book.
    assert _builtin("projects").scope == "brain"


def test_reset_entry_points_rearms_discovery(monkeypatch):
    """After an in-process install, `reset_entry_points()` must clear the
    once-guard so the next `discover()` re-runs entry-point registration —
    the no-daemon-restart contract for Bay/store installs."""
    import navig.modules.registry as reg_mod

    monkeypatch.setattr(reg_mod, "_EP_PLUGINS_LOADED", True)
    reg_mod.reset_entry_points()
    assert reg_mod._EP_PLUGINS_LOADED is False


# ── Conditional folds (merged_section) ─────────────────────────────────────────


def assert_merge_kind_is_coherent(m) -> None:
    """Shared by builtins and plugin defs: a merge is either hidden or conditional."""
    tile = any(s.startswith("os-tile:") for s in m.surfaces)
    if m.merged_section is not None:
        assert m.merged_into is not None, f"{m.id}: merged_section without merged_into"
    if m.merged_into is None:
        return
    if m.hidden:
        assert not tile, f"{m.id}: hidden merged tile must not declare an os-tile surface"
    else:
        assert m.merged_section, (
            f"{m.id}: a visible merged app must name the host section it folds into "
            f"(merged_section) — otherwise it is just a tile that claims a host"
        )
        assert tile, (
            f"{m.id}: a conditional fold must keep its own os-tile — it is the only "
            f"place the app renders while its host is locked"
        )


def test_merged_section_is_on_the_wire():
    """`GET /api/deck/modules` carries merged_section, so surfaces learn the fold
    from the catalog instead of each keeping its own table."""
    from navig.modules.registry import ModuleDef

    row = _builtin("system").to_dict(capabilities=["core_ops"], enabled=True)
    assert row["merged_section"] is None
    fold = ModuleDef(
        id="sales", label="Store sales", description="", surfaces=["os-tile:sales"],
        app_category="Money", merged_into="finance", merged_section="Store",
    ).to_dict(capabilities=[], enabled=True)
    assert fold["merged_into"] == "finance" and fold["merged_section"] == "Store"
    assert fold["hidden"] is False


def test_merge_kind_rules_reject_incoherent_defs():
    import pytest

    from navig.modules.registry import ModuleDef

    def make(**kw):
        return ModuleDef(id="x", label="X", description="", **kw)

    # a visible merge that names no section, and one that drops its own tile
    for bad in (
        make(surfaces=["os-tile:x"], merged_into="finance"),
        make(surfaces=[], merged_into="finance", merged_section="Store"),
        make(surfaces=["os-tile:x"], merged_section="Store"),
        make(surfaces=["os-tile:x"], hidden=True, merged_into="messages"),
    ):
        with pytest.raises(AssertionError):
            assert_merge_kind_is_coherent(bad)
    assert_merge_kind_is_coherent(make(surfaces=["os-tile:x"], merged_into="finance", merged_section="Store"))
    assert_merge_kind_is_coherent(make(surfaces=[], hidden=True, merged_into="messages"))
