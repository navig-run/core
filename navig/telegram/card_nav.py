"""The three affordances every card ends with: step, close, and a way out.

A card that is a *list* is not a wall of text with buttons under it — it is a
small screen the operator walks through: one item at a time, a counter that says
where they are, and a close button that settles the card instead of leaving its
buttons live forever. The same three shapes recurred in every family this
review touched (todos, agenda, reminders, briefings), so they live here once:

* :func:`pager` — ``◀   2 / 5   ▶``; the counter is an inert chip, and a step
  that cannot happen is drawn greyed instead of disappearing (a row whose ends
  change width as you walk it makes the operator aim again every step).
* :func:`close_row` — ``✕ Close``, which strips every button and leaves one
  quiet line (:func:`settle`).
* :func:`menu_rows` — the buttons that open a sub-screen, so a card with depth
  never has to choose between "show everything" and "show nothing".

Two conventions keep the callbacks honest:

* every button carries ``<prefix>:<verb>[:<rest>]``, parsed by :func:`parse`;
* a button that does nothing — a counter, a greyed step — answers the press
  itself. The dispatcher swallows ``noop*`` callbacks before any handler chain
  (see ``navig/gateway/channels/telegram.py``); an unanswered callback spins for
  30 s in the client and reads as a broken card.

Nothing here sends a message: these helpers return plain ``inline_keyboard``
rows, so they work with the classic renderer and the rich one alike.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from navig.telegram.card_doc import esc

#: Verb names, shared by every card that pages or closes.
VERB_PREV = "prev"
VERB_NEXT = "next"
VERB_CLOSE = "close"

#: Callback for an inert button. Kept as a constant so the dispatcher's
#: ``noop`` branch and every card agree on one spelling.
NOOP = "noop:chip"
DISABLED_NOOP = "noop:disabled"

CLOSE_LABEL = "✕ Close"


def close_label() -> str:
    """The close button's text in the operator's language.

    The label sits on every card, so it is the one string a broken locale is
    noticed on. ``CLOSE_LABEL`` stays as the English fallback: a missing entry
    must never leave a button blank.
    """
    try:
        from navig.core import i18n  # noqa: PLC0415 — a label is not worth an import cycle

        text = i18n.t("card.close")
        return CLOSE_LABEL if text == "card.close" else text
    except Exception:  # noqa: BLE001 — a label is never worth an exception
        return CLOSE_LABEL


# -- rows ------------------------------------------------------------------


def chip(label: str) -> dict[str, Any]:
    """A button that shows state and does nothing.

    Carries both the real Bot API 10.3 ``disabled`` marker and a ``noop``
    callback: a server that predates the field would otherwise reject a button
    with no action, and a client that honours it never sends the callback at
    all. Either way the press is answered.
    """
    return {"text": label, "callback_data": NOOP, "disabled": {}}


def step(label: str, data: str, *, enabled: bool = True) -> dict[str, Any]:
    """A navigation button that greys out (instead of vanishing) at the ends."""
    if not enabled:
        return chip(label)
    return {"text": label, "callback_data": data}


def pager(index: int, total: int, *, prefix: str) -> list[dict[str, Any]]:
    """``◀   2 / 5   ▶`` for a card sequence. Empty when there is nothing to walk.

    ``index`` is 0-based. The counter is inert: it reports position, it is not a
    button. One item means no pager row at all — a lone ``1 / 1`` is noise.
    """
    if total <= 1:
        return []
    index = max(0, min(index, total - 1))
    return [
        step("◀", f"{prefix}:{VERB_PREV}", enabled=index > 0),
        chip(f"{index + 1} / {total}"),
        step("▶", f"{prefix}:{VERB_NEXT}", enabled=index < total - 1),
    ]


def close_row(prefix: str, *, label: str | None = None) -> list[dict[str, Any]]:
    """The ``✕ Close`` row. One card, one way out."""
    return [{"text": label or close_label(), "callback_data": f"{prefix}:{VERB_CLOSE}"}]


def menu_rows(*rows: Sequence[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """Drop empty rows, keep order — the shell of a card's keyboard."""
    return [list(row) for row in rows if row]


async def settle(
    channel: Any,
    chat_id: int,
    message_id: int | None,
    *,
    title: str = "",
    note: str = "",
) -> bool:
    """Close a card in place: one quiet line, every button gone.

    Editing to a dim ``✕`` line rather than deleting keeps the transcript honest
    — the operator can see the card was there and that it is settled. The empty
    keyboard is explicit (``{"inline_keyboard": []}``): omitting ``reply_markup``
    leaves the old buttons live, and a press on a settled card is the exact
    "dead button" this review keeps finding.
    """
    if not message_id:
        return False
    body = f"<i>✕ {esc(title)}</i>" if title else "<i>✕</i>"
    if note:
        body += f"\n<i>{esc(note)}</i>"
    edit = getattr(channel, "edit_message", None)
    try:
        if edit is not None:
            result = await edit(chat_id, int(message_id), body, keyboard=[])
        else:
            # The card modules in navig/telegram talk to ``_api_call`` directly; a
            # close must work there too rather than silently doing nothing.
            api = getattr(channel, "_api_call", None)
            if api is None:
                return False
            result = await api(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": int(message_id),
                    "text": body,
                    "parse_mode": "HTML",
                    "reply_markup": {"inline_keyboard": []},
                },
            )
    except Exception:  # noqa: BLE001 — a close must never take the dispatcher down
        return False
    return result is not None


# -- callbacks -------------------------------------------------------------


def parse(data: str, prefix: str) -> tuple[str, str]:
    """Split ``"<prefix>:<verb>:<rest>"`` into ``("verb", "rest")``.

    A button may carry no rest (``"todo:close"``) and a rest may itself contain
    colons (``"todo:open:2026-10-25T14:00"``), so the split stops after the verb.
    Anything that does not match this prefix yields ``("", "")`` — the caller
    treats that as "not mine" and lets the dispatcher move on.
    """
    want = prefix + ":"
    if not data.startswith(want):
        return "", ""
    tail = data[len(want) :]
    verb, _, rest = tail.partition(":")
    return verb, rest


def walk(index: int, total: int, data: str, prefix: str) -> int | None:
    """The new index for a ``prev``/``next`` press, or None if nothing moves.

    Clamping here rather than in each family means a stale card — its list shrank
    since the keyboard was drawn — still lands on a REAL item instead of an
    ``IndexError`` an hour later. A stale index does not also move one step: the
    press re-shows the clamped item, because the operator aimed at item 5 and
    the honest answer is "here is the last one", not "here is the one before the
    one you never saw".
    """
    verb, _rest = parse(data, prefix)
    if verb not in (VERB_PREV, VERB_NEXT) or total <= 0:
        return None
    clamped = max(0, min(index, total - 1))
    if index != clamped:
        return clamped
    if verb == VERB_PREV:
        return max(0, clamped - 1)
    return min(total - 1, clamped + 1)


def sequence(rows: Iterable[Any]) -> list[Any]:
    """Materialise a view once, so index math and rendering see the same list."""
    return list(rows)
