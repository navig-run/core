"""The process killer's four answers, as cards.

`/kill` is the one surface where the wrong press is expensive, and it used to answer in
padded text with a two-button keyboard *outside* the bubble: no way to rehearse, no list of
what matched beyond five names, and the cancel button left its own Kill button alive in the
chat. Each state is now a card:

- **usage** — the syntax in a copyable block plus one rehearsal button per common group;
- **dry run** — the whole candidate list, the verdict that nothing was killed, and a button
  that carries on into the real kill;
- **confirm** — the matched processes, the mode, and a warning callout when the group can
  take the daemon or the operator's own shell with it;
- **result** — what was killed, skipped and failed, which rewrites the confirming message so
  the chat keeps one record per kill.

Callbacks stay `kill_confirm:<target>:<soft|force>` and `kill_cancel`, and the rehearsal /
"kill now" buttons are `nav:cmd:` links to real commands, so nothing downstream changed.
"""

from __future__ import annotations

from typing import Any, Iterable

from navig.core.i18n import t
from navig.telegram.card_doc import Button, Card, Code, Para, PullQuote, Raw, Table, esc

_FALLBACK = {
    "kill.card.title": "Kill processes",
    "kill.card.usage": "Usage",
    "kill.card.groups": "Built-in groups",
    "kill.card.groups_hint": "Any other word is matched against process names.",
    "kill.card.no_match": "No matching processes.",
    "kill.card.dry_title": "Dry run",
    "kill.card.candidates": "{n} candidate(s) would be killed",
    "kill.card.confirm_title": "Confirm kill",
    "kill.card.match": "{n} process(es) match",
    "kill.card.warning": (
        "This group can take the NAVIG daemon (Python) or your own shell (PowerShell) "
        "with it. Self-protection skips the daemon's PID and parent."
    ),
    "kill.card.mode": "Mode",
    "kill.card.mode_force": "--force (SIGKILL)",
    "kill.card.mode_soft": "graceful terminate, escalates after 3 s",
    "kill.card.dry_hint": "Nothing was killed — this was a rehearsal.",
    "kill.card.footer_confirm": "Nothing runs until you press Kill.",
    "kill.card.result_title": "Kill complete",
    "kill.card.killed": "Killed",
    "kill.card.skipped": "Skipped",
    "kill.card.failed": "Failed",
    "kill.card.skipped_note": "self-protected or already gone",
    "kill.card.cancelled_title": "Kill cancelled",
    "kill.card.cancelled_hint": "Nothing was touched. The buttons are gone, so no stray press can kill later.",
    "kill.card.pid": "PID",
    "kill.card.process": "Process",
    "kill.card.reason": "Reason",
    "kill.card.kill_now": "🛑 Kill {n} now",
    "kill.card.force": "⚡ Force kill",
    "kill.card.rehearse": "🔍 Dry run",
    "kill.card.rehearse_again": "🔍 Rehearse again",
    "kill.card.copy_usage": "📋 Copy usage",
    "kill.card.cancel": "✕ Cancel",
    "kill.card.close": "✕ Close",
    "kill.card.more": "… +{n} more",
    "kill.card.risky": "Risky group",
    "kill.card.no_match_hint": "Check the group name, or run /kill on its own for the list.",
}

# The groups worth one press: the four the operator reaches for, rehearsed first.
COMMON_GROUPS = ("chrome-tabs", "node", "python", "powershell")

USAGE = "/kill <group|name> [--force] [--dry-run]"


def _label(key: str, fallback: str = "") -> str:
    """A translated label, or the English default when the locale has no key yet."""
    try:
        value = t(key)
    except Exception:  # noqa: BLE001 - locale lookup must never break a card
        value = None
    if not value or value == key:
        return _FALLBACK.get(key, fallback)
    return str(value)


def _f(key: str, **kwargs: Any) -> str:
    text = _label(key)
    for name, value in kwargs.items():
        text = text.replace("{" + name + "}", str(value))
    return text


def proc_rows(candidates: Iterable[dict[str, Any]], *, limit: int = 20) -> list[list[Any]]:
    """PID and name for each match, as table rows."""
    rows: list[list[Any]] = []
    for item in list(candidates)[:limit]:
        pid = item.get("pid")
        name = str(item.get("name") or "").strip()
        rows.append(
            [
                Raw(f"<code>{esc(str(pid if pid is not None else '?'))}</code>"),
                Raw(esc(name) if name else "<i>—</i>"),
            ]
        )
    return rows


def _header_rows() -> list[str]:
    return [_label("kill.card.pid", "PID"), _label("kill.card.process", "Process")]


def usage_card(*, groups: Iterable[str], common: Iterable[str] = COMMON_GROUPS) -> Card:
    """No target given: the syntax, the groups, and one rehearsal press per common group."""
    card = Card(_label("kill.card.title"), glyph="🛑")
    card.add(Para(Raw(f"<b>{esc(_label('kill.card.usage'))}</b>")))
    card.add(Code(USAGE, language="text"))
    card.add(Para(Raw(f"<b>{esc(_label('kill.card.groups'))}</b>")))
    names = [str(g) for g in groups if str(g).strip()]
    if names:
        card.add(Para(Raw(" · ".join(f"<code>{esc(g)}</code>" for g in names))))
    card.add(Para(_label("kill.card.groups_hint")))
    buttons = [
        Button(f"🔍 {name}", data=f"nav:cmd:/kill {name} --dry-run")
        for name in common
        if name
    ]
    for start in range(0, len(buttons), 2):
        card.buttons(*buttons[start : start + 2])
    card.buttons(Button(_label("kill.card.copy_usage"), copy=USAGE, style="primary"))
    card.footer = _label("kill.card.groups_hint")
    return card


def no_match_card(target: str) -> Card:
    """Nothing matched: say so and leave the syntax one press away."""
    card = Card(_f("kill.card.dry_title"), glyph="🛑", meta=f"/kill {target}")
    card.add(PullQuote(_label("kill.card.no_match")))
    card.add(Para(_label("kill.card.no_match_hint")))
    card.add(Code(USAGE, language="text"))
    card.buttons(Button(_label("kill.card.copy_usage"), copy=USAGE, style="primary"))
    return card


def dry_run_card(
    target: str,
    candidates: Iterable[dict[str, Any]],
    *,
    total: int | None = None,
    force: bool = False,
) -> Card:
    """What would be killed, stated as a rehearsal that killed nothing."""
    items = list(candidates)
    count = total if total is not None else len(items)
    card = Card(_label("kill.card.dry_title"), glyph="🔍", meta=f"/kill {target}")
    card.add(Para(_f("kill.card.candidates", n=count)))
    card.add(Table(header=_header_rows(), rows=proc_rows(items), bordered=False))
    if count > len(items):
        card.add(Para(_label("kill.card.more").replace("{n}", str(count - len(items)))))
    card.add(Para(_label("kill.card.dry_hint"), style="italic"))
    card.buttons(
        Button(
            _f("kill.card.kill_now", n=count),
            data=f"kill_confirm:{target}:{'force' if force else 'soft'}",
            style="danger",
        ),
        Button(_label("kill.card.cancel"), data="kill_cancel"),
    )
    if not force:
        card.buttons(
            Button(
                f"⚡ {_label('kill.card.mode_force')}",
                data=f"kill_confirm:{target}:force",
                style="danger",
            )
        )
    return card


def confirm_card(
    target: str,
    candidates: Iterable[dict[str, Any]],
    *,
    total: int,
    force: bool = False,
    self_risky: bool = False,
) -> Card:
    """The real question: this many processes, this mode, this warning."""
    items = list(candidates)
    card = Card(
        _label("kill.card.confirm_title"),
        glyph="🛑",
        meta=f"/kill {target}",
        big=True,
    )
    card.add(Para(_f("kill.card.match", n=total)))
    card.add(Table(header=_header_rows(), rows=proc_rows(items, limit=5), bordered=False))
    if total > 5:
        card.add(Para(_label("kill.card.more").replace("{n}", str(total - 5))))
    mode = (
        _label("kill.card.mode_force")
        if force
        else _label("kill.card.mode_soft")
    )
    card.add(
        Table(
            rows=[[_label("kill.card.mode"), Raw(f"<code>{esc(mode)}</code>")]],
            bordered=False,
        )
    )
    if self_risky:
        card.add(PullQuote(_label("kill.card.warning")))
    card.buttons(
        Button(
            _f("kill.card.kill_now", n=total),
            data=f"kill_confirm:{target}:{'force' if force else 'soft'}",
            style="danger",
        ),
        Button(_label("kill.card.cancel"), data="kill_cancel"),
    )
    if not force:
        card.buttons(
            Button(
                f"⚡ {_label('kill.card.mode_force')}",
                data=f"kill_confirm:{target}:force",
                style="danger",
            )
        )
    card.buttons(
        Button(
            _label("kill.card.rehearse_again"),
            data=f"nav:cmd:/kill {target} --dry-run",
        )
    )
    card.footer = (
        _label("kill.card.warning") if self_risky else _label("kill.card.footer_confirm")
    )
    return card


def result_card(
    target: str,
    *,
    killed: Iterable[dict[str, Any]] = (),
    skipped: Iterable[dict[str, Any]] = (),
    failed: Iterable[dict[str, Any]] = (),
    force: bool = False,
) -> Card:
    """What actually happened, as the record that replaces the confirming card."""
    killed_list = list(killed)
    skipped_list = list(skipped)
    failed_list = list(failed)
    mode = _label("kill.card.mode_force") if force else _label("kill.card.mode_soft")
    glyph = "🛑" if failed_list else "✅"
    card = Card(_label("kill.card.result_title"), glyph=glyph, meta=f"/kill {target}")
    sheet: list[list[Any]] = [
        [_label("kill.card.killed"), Raw(f"<b>{len(killed_list)}</b>")],
    ]
    if skipped_list:
        sheet.append(
            [
                _label("kill.card.skipped"),
                Raw(f"<b>{len(skipped_list)}</b> <i>{esc(_label('kill.card.skipped_note'))}</i>"),
            ]
        )
    if failed_list:
        sheet.append([_label("kill.card.failed"), Raw(f"<b>{len(failed_list)}</b>")])
    sheet.append([_label("kill.card.mode"), Raw(f"<code>{esc(mode)}</code>")])
    card.add(Table(rows=sheet, bordered=False))
    if failed_list:
        rows = [
            [
                Raw(f"<code>{esc(str(item.get('pid') or '?'))}</code>"),
                Raw(esc(str(item.get("reason") or "?"))),
            ]
            for item in failed_list[:5]
        ]
        card.add(
            Table(
                header=[_label("kill.card.pid"), _label("kill.card.reason")],
                rows=rows,
                bordered=False,
            )
        )
    card.buttons(
        Button(
            _label("kill.card.rehearse_again"),
            data=f"nav:cmd:/kill {target} --dry-run",
            style="primary",
        ),
        Button(_label("kill.card.close"), data="kill_cancel"),
    )
    return card


def cancelled_card(target: str = "") -> Card:
    """The confirming card, defused: no buttons, one sentence."""
    card = Card(
        _label("kill.card.cancelled_title"),
        glyph="✕",
        meta=f"/kill {target}" if target else "",
    )
    card.add(Para(_label("kill.card.cancelled_hint")))
    return card


async def cancel(channel: Any, chat_id: int, message_id: int) -> bool:
    """Rewrite the confirming message as the defused card, with its buttons removed.

    Omitting the keyboard is not the same as emptying it: an edit that leaves
    ``reply_markup`` out keeps whatever buttons the message already had, so a
    cancelled kill card stayed armed. An empty inline keyboard is the payload that
    actually takes the danger away.
    """
    from navig.telegram import card_transport

    return await card_transport.edit_card(
        channel, chat_id, message_id, cancelled_card(), keyboard=[]
    )


def build_usage(*, groups: Iterable[str], common: Iterable[str] = COMMON_GROUPS) -> tuple[str, str, list]:
    card = usage_card(groups=groups, common=common)
    return card.classic_html(), card.rich_html(), card.keyboard()


def build_confirm(
    target: str,
    candidates: Iterable[dict[str, Any]],
    *,
    total: int,
    force: bool = False,
    self_risky: bool = False,
) -> tuple[str, str, list]:
    card = confirm_card(
        target, candidates, total=total, force=force, self_risky=self_risky
    )
    return card.classic_html(), card.rich_html(), card.keyboard()


__all__ = [
    "COMMON_GROUPS",
    "USAGE",
    "build_confirm",
    "build_usage",
    "cancelled_card",
    "confirm_card",
    "dry_run_card",
    "no_match_card",
    "proc_rows",
    "result_card",
    "usage_card",
]
