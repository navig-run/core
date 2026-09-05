"""`allowed_updates` must come from ONE list, never a literal retyped at the call site.

Telegram only sends the update types you ask for, and `allowed_updates` is **sticky**:
whatever the last `setWebhook` / `getUpdates` declared is what the bot keeps receiving.
So a caller that passes a narrower list does not merely limit itself — it silently
switches those update types OFF for the whole bot, until something re-declares the full
set. Business updates are not in Telegram's default set, which is how the Business bot
went 100% deaf with every health signal green.

`navig/telegram/updates.py` says so in its own module docstring — "This list is therefore
shared by every caller instead of being retyped per site" — and TWO call sites retyped a
2-of-10 list anyway:

    navig/integrations/telegram_bridge.py     allowed_updates=["message", "callback_query"]
    navig/integrations/telegram_voice_bot.py  allowed_updates=["message", "callback_query"]

Both poll with the operator's bot token out of the vault, i.e. the SAME bot the gateway
channel serves. Neither had a caller when this landed, so nothing was broken in
production — but the bridge is already imported by `comms_router` for human-in-the-loop,
and its own docstring advertises a `navig telegram listen` command. The first person to
wire either one up would have narrowed the live bot's update set and, with
`drop_pending_updates=True`, discarded the operator's unread inbound messages as well.

That is the same shape as the SSRF-guard scope hole: an invariant the codebase states in
prose, enforced by nothing. A rule that lives only in a docstring is a rule that holds
until someone doesn't read it.

The check is DERIVED — any literal list handed to an `allowed_updates=` keyword, anywhere
in core or the plugins — rather than a list of known call sites, so a new one is caught
the moment it lands. Widening the canonical base is still fine and must stay fine:
`merge_allowed_updates(ALLOWED_UPDATES)` (the catalog's extra types) passes a NAME, not a
literal, and is exactly the intended way to ask for more.
"""

from __future__ import annotations

import ast
from pathlib import Path

# core/tests/quality/<this> -> parents[2] == core
_CORE = Path(__file__).resolve().parents[2]
_NAVIG_ROOT = _CORE / "navig"
_PLUGINS_ROOT = _CORE.parent / "plugins"

_SKIP_PARTS = frozenset({"__pycache__", "build", "dist", ".venv", "node_modules"})

# The one module allowed to spell the list out — it IS the list.
_CANONICAL = "navig/telegram/updates.py"


def _roots() -> list[Path]:
    out = [_NAVIG_ROOT]
    if _PLUGINS_ROOT.is_dir():
        out.append(_PLUGINS_ROOT)
    return out


def _rel(path: Path) -> str:
    try:
        return path.relative_to(_CORE).as_posix()
    except ValueError:
        return f"plugins/{path.relative_to(_PLUGINS_ROOT).as_posix()}"


def _literal_allowed_updates(tree: ast.AST) -> list[int]:
    """Line numbers where a LITERAL list/tuple/set is passed as ``allowed_updates=``."""
    hits: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "allowed_updates":
                continue
            if isinstance(kw.value, (ast.List, ast.Tuple, ast.Set)):
                hits.append(kw.value.lineno)
    return hits


def _scan() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for root in _roots():
        for py in root.rglob("*.py"):
            if _SKIP_PARTS & set(py.parts):
                continue
            rel = _rel(py)
            if rel == _CANONICAL:
                continue
            try:
                tree = ast.parse(py.read_text(encoding="utf-8-sig"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            hits = _literal_allowed_updates(tree)
            if hits:
                found[rel] = hits
    return found


def test_no_call_site_retypes_the_allowed_updates_list() -> None:
    offenders = _scan()
    assert not offenders, (
        "a literal `allowed_updates=[...]` — Telegram remembers this list, so a narrower "
        "one here switches those update types off for the WHOLE bot (business_* are not "
        "in Telegram's default set, so omitting them makes the Business bot deaf):\n  "
        + "\n  ".join(f"{rel}:{lines}" for rel, lines in sorted(offenders.items()))
        + "\n\nPass `navig.telegram.updates.ALLOWED_UPDATES`. To ask for MORE types, widen "
        "the canonical base — `merge_allowed_updates(ALLOWED_UPDATES)` — rather than "
        "spelling out a new list."
    )


def test_the_canonical_list_still_carries_the_business_types() -> None:
    """The reason the shared list exists at all.

    `business_*` are NOT in Telegram's default set. If they ever fall out of this list,
    every caller silently stops receiving them — the original outage, restored.
    """
    from navig.telegram.updates import ALLOWED_UPDATES

    for required in (
        "business_connection",
        "business_message",
        "edited_business_message",
        "deleted_business_messages",
    ):
        assert required in ALLOWED_UPDATES, (
            f"{required!r} left the canonical list — the Business bot goes deaf and "
            "every health signal stays green"
        )


def test_widening_the_canonical_base_is_not_flagged() -> None:
    """Anti-vacuity in the permissive direction: the catalog's merge must stay legal.

    A guard that also banned `merge_allowed_updates(ALLOWED_UPDATES)` would force the
    next person needing an extra update type to work around it — and working around an
    SSRF-style guard is how the rule dies.
    """
    legal = (
        "app.start_polling(allowed_updates=ALLOWED_UPDATES)\n"
        "bot.set_webhook(allowed_updates=merge_allowed_updates(ALLOWED_UPDATES))\n"
    )
    assert _literal_allowed_updates(ast.parse(legal)) == []


def test_the_detector_catches_a_retyped_literal() -> None:
    """Anti-vacuity in the strict direction."""
    bad = 'app.start_polling(allowed_updates=["message", "callback_query"])\n'
    assert len(_literal_allowed_updates(ast.parse(bad))) == 1

    also_bad = "bot.set_webhook(allowed_updates=('message',))\n"
    assert len(_literal_allowed_updates(ast.parse(also_bad))) == 1


def test_the_scan_actually_reaches_the_call_sites_it_guards() -> None:
    """A scan that silently walks nothing reports a clean tree forever.

    Pins that the roots resolve and that the modules this guard exists for are in scope.
    """
    scanned = set()
    for root in _roots():
        for py in root.rglob("*.py"):
            if not (_SKIP_PARTS & set(py.parts)):
                scanned.add(_rel(py))

    assert len(scanned) > 500, f"only {len(scanned)} files walked — the roots are wrong"
    for rel in (
        "navig/telegram/updates.py",
        "navig/integrations/telegram_bridge.py",
        "navig/integrations/telegram_voice_bot.py",
        "navig/gateway/channels/telegram.py",
    ):
        assert rel in scanned, f"{rel} is not in scope — the guard would not see a retype there"
