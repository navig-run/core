"""Every Telegram command, reply action and locked name maps to exactly one extension.

The Telegram Extensions gate (``navig.gateway.channels.telegram_extensions``) is
DELIBERATELY fail-open: an unmapped command resolves to "enabled" so a
developer's omission can never silence the operator's bot.  This guard is what
makes that safe -- it turns "escaped the gate" from a silent runtime condition
into a build failure.

Companion guard: ``test_telegram_callback_prefixes_are_gated.py`` does the same
job for callback-button prefixes.
"""

from __future__ import annotations

import ast
import collections
from pathlib import Path

import pytest

from navig.gateway.channels import telegram_extensions as tx
from navig.gateway.channels.telegram_commands import LOCKED_COMMANDS

REPO = Path(__file__).resolve().parents[3]
CORE = REPO / "core" / "navig"
_COMMANDS_PY = CORE / "gateway" / "channels" / "telegram_commands.py"
_REPLY_ACTIONS_PY = CORE / "telegram" / "reply_actions.py"

# Anti-vacuity floors.  A guard that silently reads nothing looks exactly like a
# clean run; these numbers fail the build if the extraction breaks.
_MIN_REGISTRY_COMMANDS = 100
_MIN_EXTENSIONS = 15
_MIN_REPLY_ACTIONS = 18

#: Names claimed by the catalog that deliberately have no `_SLASH_REGISTRY` entry,
#: each with the reason it is not a rename that slipped through.
#: `test_ghost_exemptions_are_still_needed` fails if one stops being necessary.
_GHOST_EXEMPT: frozenset[str] = frozenset({
    # In LOCKED_COMMANDS but never a registry entry: the /settings surface moved
    # to the Deck (Account section). The name stays locked so a hand-edited
    # config cannot resurrect it as a disableable command; `core` claims it so
    # the "every locked command belongs to Core" invariant needs no special case.
    "settings",
})


def _module_level_value(path: Path, name: str) -> ast.AST | None:
    """Return the AST node assigned to a module-level ``name`` (Assign or AnnAssign)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", None) == name:
            return node.value
        if isinstance(node, ast.Assign) and any(
            getattr(t, "id", None) == name for t in node.targets
        ):
            return node.value
    return None


def _registry_entries() -> list[tuple[str, str]]:
    """(command, category) for every unique ``_SLASH_REGISTRY`` entry, first wins.

    Read from the AST rather than by importing, so this guard keeps working when
    the channel module's heavy optional imports are unavailable.
    """
    value = _module_level_value(_COMMANDS_PY, "_SLASH_REGISTRY")
    assert value is not None, "_SLASH_REGISTRY not found -- the extractor is broken"
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for el in value.elts:  # type: ignore[attr-defined]
        if not isinstance(el, ast.Call) or not el.args:
            continue
        cmd = el.args[0].value
        category = "general"
        for kw in el.keywords:
            if kw.arg == "category" and isinstance(kw.value, ast.Constant):
                category = kw.value.value
        if cmd in seen:
            continue
        seen.add(cmd)
        out.append((cmd, category))
    return out


def _canonical_reply_actions() -> set[str]:
    value = _module_level_value(_REPLY_ACTIONS_PY, "KEYWORDS")
    assert value is not None, "KEYWORDS not found -- the extractor is broken"
    return {v.value for v in value.values if isinstance(v, ast.Constant)}  # type: ignore[attr-defined]


@pytest.fixture(scope="module")
def registry() -> list[tuple[str, str]]:
    entries = _registry_entries()
    assert len(entries) >= _MIN_REGISTRY_COMMANDS, (
        f"only {len(entries)} registry commands parsed (floor {_MIN_REGISTRY_COMMANDS}) -- "
        "the AST extractor has probably drifted from _SLASH_REGISTRY's shape"
    )
    return entries


def test_catalog_is_not_vacuous() -> None:
    assert len(tx.EXTENSIONS) >= _MIN_EXTENSIONS
    assert any(e.locked for e in tx.EXTENSIONS), "the locked `core` extension vanished"


def test_every_command_resolves_to_an_extension(registry) -> None:
    """No command may escape the gate."""
    orphans = [
        (cmd, cat) for cmd, cat in registry
        if tx.extension_for_command(cmd, cat) is None
    ]
    assert not orphans, (
        "these Telegram commands belong to no extension, so they can never be "
        f"switched off: {orphans}\n"
        "Fix: add the command to the matching TelegramExtension.commands in "
        "core/navig/gateway/channels/telegram_extensions.py (preferred), or add "
        "its category to CATEGORY_TO_EXTENSION if the whole category belongs to "
        "one extension."
    )


def test_extensions_do_not_claim_the_same_command() -> None:
    counts = collections.Counter(c for e in tx.EXTENSIONS for c in e.commands)
    dupes = sorted(c for c, n in counts.items() if n > 1)
    assert not dupes, f"claimed by more than one extension: {dupes}"


def test_catalog_has_no_ghost_commands(registry) -> None:
    """A catalog entry naming a command that no longer exists is a silent un-gating.

    When a command is renamed, the old name keeps resolving to its extension while
    the NEW name falls through to the category fallback -- or to nothing.
    """
    known = {cmd for cmd, _ in registry}
    ghosts = sorted({c for e in tx.EXTENSIONS for c in e.commands} - known - _GHOST_EXEMPT)
    assert not ghosts, (
        f"named in the extension catalog but absent from _SLASH_REGISTRY: {ghosts}"
    )


def test_ghost_exemptions_are_still_needed(registry) -> None:
    """An exemption that stopped being necessary is stale scope -- drop it.

    Without this, _GHOST_EXEMPT silently grows into a place to hide renames.
    """
    known = {cmd for cmd, _ in registry}
    stale = sorted(_GHOST_EXEMPT & known)
    assert not stale, (
        f"these now have real _SLASH_REGISTRY entries and no longer need "
        f"exempting: {stale}"
    )


def test_locked_commands_all_belong_to_core() -> None:
    core = tx.get("core")
    assert core is not None and core.locked
    stray = sorted(set(LOCKED_COMMANDS) - core.commands)
    assert not stray, (
        f"LOCKED_COMMANDS not claimed by the locked `core` extension: {stray}. "
        "A locked command owned by a toggleable extension could be switched off "
        "through the extension, defeating the lock."
    )


def test_the_extensions_command_cannot_be_switched_off() -> None:
    """The switch for every other switch must be unreachable by any toggle."""
    owner = tx.extension_for_command("extensions")
    assert owner == "core", f"/extensions is owned by {owner!r}, not the locked core"
    assert tx.command_enabled("extensions") is True
    non_core = [e for e in tx.EXTENSIONS if e.id != "core"]
    assert not [e for e in non_core if "extensions" in e.commands or "ext" in e.commands]


def test_core_is_never_registered_as_a_module() -> None:
    """`tg:core` must not exist, so POST /modules/toggle 404s for it with no special case."""
    ids = {m.id for m in tx.module_defs()}
    assert "tg:core" not in ids
    assert len(ids) == len([e for e in tx.EXTENSIONS if not e.locked])
    assert all(i.startswith("tg:") for i in ids)


def test_every_reply_action_is_mapped() -> None:
    actions = _canonical_reply_actions()
    assert len(actions) >= _MIN_REPLY_ACTIONS, (
        f"only {len(actions)} reply actions parsed (floor {_MIN_REPLY_ACTIONS})"
    )
    unmapped = sorted(a for a in actions if tx.extension_for_action(a) is None)
    assert not unmapped, (
        f"reply-keyword actions belonging to no extension: {unmapped}. "
        "Add them to a TelegramExtension.reply_actions."
    )


def test_slash_callbacks_resolve_to_the_target_commands_extension() -> None:
    """``slash:<cmd>`` re-dispatches an arbitrary command -- it must not read as Core.

    ``_handle_health`` emits ``slash:habits``.  If the prefix itself were owned by
    the always-on Core extension, that button would still run /habits with the
    Habits extension switched off.
    """
    assert tx.extension_for_callback("slash:habits") == "habits"
    assert tx.extension_for_callback("slash:docker") == "remote"
    assert tx.extension_for_callback("slash:status") == "core"
    core = tx.get("core")
    assert core is not None
    assert "slash:" not in core.callback_prefixes


def test_display_order_is_independent_of_enabled_state(monkeypatch) -> None:
    """A toggled row must not move out from under the operator's finger."""
    before = [e.id for e in tx.all_extensions()]
    monkeypatch.setattr(tx, "is_enabled", lambda _id: False)
    assert [e.id for e in tx.all_extensions()] == before
    monkeypatch.setattr(tx, "is_enabled", lambda _id: True)
    assert [e.id for e in tx.all_extensions()] == before


def test_ungated_prefixes_each_carry_a_reason() -> None:
    for prefix, reason in tx.UNGATED_PREFIXES.items():
        assert prefix and isinstance(reason, str) and len(reason) > 15, (
            f"UNGATED_PREFIXES[{prefix!r}] needs a written reason, not a placeholder"
        )


def test_gate_fails_open_on_an_unknown_extension() -> None:
    assert tx.is_enabled("tg:does-not-exist") is True
    assert tx.command_enabled("a-command-nobody-declared") is True
    assert tx.callback_enabled("zzz_unknown_prefix:1") is True


def test_filter_keyboard_preserves_non_callback_buttons() -> None:
    """url / web_app buttons and undeclared prefixes are never stripped."""
    kb = {"inline_keyboard": [[
        {"text": "Docs", "url": "https://navig.run"},
        {"text": "App", "web_app": {"url": "https://navig.run"}},
        {"text": "Odd", "callback_data": "zzz_undeclared:1"},
    ]]}
    assert tx.filter_keyboard(kb) is kb


def test_filter_keyboard_leaves_reply_markups_alone() -> None:
    for markup in ({"keyboard": [["a"]]}, {"remove_keyboard": True}, {"force_reply": True}):
        assert tx.filter_keyboard(markup) is markup


def test_filter_keyboard_drops_a_disabled_extensions_buttons(monkeypatch) -> None:
    real = tx.is_enabled
    monkeypatch.setattr(
        tx, "is_enabled", lambda e: False if e == "habits" else real(e)
    )
    kb = {"inline_keyboard": [
        [{"text": "Done", "callback_data": "hb:t:1"}],
        [{"text": "Refresh", "callback_data": "slash:habits"},
         {"text": "Status", "callback_data": "slash:status"}],
    ]}
    out = tx.filter_keyboard(kb)
    assert out is not None
    assert [[b["text"] for b in row] for row in out["inline_keyboard"]] == [["Status"]]


def test_a_fully_emptied_keyboard_becomes_none(monkeypatch) -> None:
    """The caller must drop reply_markup entirely rather than send an empty one."""
    real = tx.is_enabled
    monkeypatch.setattr(
        tx, "is_enabled", lambda e: False if e == "habits" else real(e)
    )
    kb = {"inline_keyboard": [[{"text": "Done", "callback_data": "hb:t:1"}]]}
    assert tx.filter_keyboard(kb) is None


def test_legacy_keys_named_in_the_catalog_are_real(registry) -> None:
    """A legacy_key that nothing ever wrote would make the precedence rule a lie."""
    declared = {e.legacy_key for e in tx.EXTENSIONS if e.legacy_key}
    assert declared, "no legacy keys declared -- the migration path vanished"
    tree_text = "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in (CORE / "gateway" / "channels").rglob("*.py")
    ) + "\n".join(
        p.read_text(encoding="utf-8", errors="ignore")
        for p in (CORE / "telegram").rglob("*.py")
    )
    for key in sorted(declared):
        leaf = key.rsplit(".", 1)[-1]
        assert leaf in tree_text, (
            f"legacy_key {key!r} appears nowhere in the Telegram source -- "
            "either it was renamed (update the catalog) or it never existed."
        )


# ── Upgrade safety ───────────────────────────────────────────────────────────
# The operator installing this version has a config that predates extensions.
# Nothing they use may switch itself off.


def test_every_extension_defaults_to_on() -> None:
    """An extension that shipped off-by-default would silently remove a feature.

    `groups` is the case that made this a test: it bundles checklists and
    reactions, both of which have always defaulted to ON, so defaulting the
    bundle to off would have taken two working features away on upgrade.
    """
    off = [e.id for e in tx.EXTENSIONS if not e.default_enabled]
    assert not off, (
        f"these extensions default to OFF: {off}. Every feature they own would "
        "vanish for existing users on upgrade. Default it on, or split the "
        "off-by-default part into its own extension."
    )


def test_a_legacy_key_still_decides_until_the_new_toggle_is_touched(monkeypatch) -> None:
    """Precedence: operator override > legacy key > default. Nothing is migrated."""
    ext = next(e for e in tx.EXTENSIONS if e.legacy_key)
    store: dict[str, object] = {}
    monkeypatch.setattr(
        tx, "_config_value", lambda k: store.get(k, tx._MISSING)
    )

    # 1. Untouched config -> the extension's own default.
    assert tx.is_enabled(ext.id) is ext.default_enabled

    # 2. The legacy key alone decides. Stored as the STRING "false", which is what
    #    `navig config set` writes and what bool() gets wrong.
    store[ext.legacy_key] = "false"
    assert tx.is_enabled(ext.id) is False

    # 3. The new override shadows it permanently, in both directions.
    store[f"modules.overrides.{ext.module_id}"] = True
    assert tx.is_enabled(ext.id) is True
    store[f"modules.overrides.{ext.module_id}"] = "false"
    assert tx.is_enabled(ext.id) is False


def test_absent_is_distinguishable_from_present_and_false(monkeypatch) -> None:
    """"Not set" must not be collapsed into "set to false" — that is the whole
    reason `_config_value` returns a sentinel rather than None."""
    ext = next(e for e in tx.EXTENSIONS if e.legacy_key and e.default_enabled)
    monkeypatch.setattr(tx, "_config_value", lambda _k: tx._MISSING)
    assert tx.is_enabled(ext.id) is True, "an absent key must fall through to the default"


def test_the_store_hides_telegram_extensions_without_dropping_anything_else() -> None:
    """`navig store` must lose the 18 tg: rows and NOT the pre-existing hidden ones.

    Filtering on `hidden` instead of the `tg:` prefix looks equivalent and is not:
    `contacts`, `schedule` and `health` are hidden from the app grid but have
    always been listed in the store, so that filter would quietly drop three rows
    unrelated to extensions.
    """
    from navig.hub.aggregator import _modules

    ids = {item.id for item in _modules()}
    assert not [i for i in ids if i.startswith("module:tg:")], (
        "Telegram extensions belong to /extensions and the Deck, not the store"
    )
    hidden_but_listed = {"contacts", "schedule", "health"}
    still_there = {n for n in hidden_but_listed if f"module:{n}" in ids}
    assert still_there == hidden_but_listed, (
        f"the store filter dropped pre-existing rows: {sorted(hidden_but_listed - still_there)}"
    )
