"""Deliver a library prompt to Telegram.

The bot sends a **rich message** (Bot API 10.2 ``sendRichMessage``). That
format holds up to 32k characters and collapses long text behind Telegram's own
"Show More". The prompt body goes in a fenced code block, which gets a
one-tap copy button. When rich messages are unavailable,
``send_rich_message`` already falls back to HTML ``<pre>``, split at 4096
characters.

Two cases send a ``.md`` file instead:
* the body is too big for one rich message;
* the body itself contains fences, because a nested fence would garble the copy block.

Both the ``/prompt`` command and the OS "Send to Telegram" button call
:func:`send_prompt`. The two surfaces therefore cannot drift apart.
"""

from __future__ import annotations

import html
import logging
from typing import Any

logger = logging.getLogger(__name__)

RICH_LIMIT = 30_000  # sendRichMessage caps at 32,768; leave room for the header
CB_PREFIX = "pr:"
PICK_LIMIT = 8


def short_key(pid: str) -> str:
    """A stable 10-char key for ``callback_data`` (64-byte cap; ids are longer).

    Derived from the id rather than parked in the in-memory CallbackStore, so a
    button keeps working after a daemon restart or days later.
    """
    import hashlib

    return hashlib.sha1(pid.encode("utf-8")).hexdigest()[:10]  # noqa: S324 — not security


def from_short_key(key: str, *, store: Any = None) -> Any:
    from navig.store.prompt_library import get_prompt_store

    store = store or get_prompt_store()
    for pid in store.all_ids(include_archived=True):
        if short_key(pid) == key:
            return store.get(pid)
    return None


def _t(key: str, default: str, **fields: Any) -> str:
    try:
        from navig.core import i18n

        text = i18n.t(key, **fields)
        if text and text != key:
            return text
    except Exception:  # noqa: BLE001 — a missing locale never blocks a send
        logger.debug("i18n lookup failed for %s", key, exc_info=True)
    if not default:
        return key  # never send an empty message; a raw key is visible and greppable
    try:
        return default.format(**fields)
    except (KeyError, IndexError, ValueError):
        return default


def _md_escape(text: str) -> str:
    # Rich-message markdown: keep it readable, neutralise the few characters that
    # would turn a title or description into formatting.
    for ch in ("\\", "*", "_", "`", "[", "]"):
        text = text.replace(ch, "\\" + ch)
    return text


def header_markdown(rec: Any) -> str:
    where = " · ".join(x for x in (rec.domain, rec.category, rec.collection) if x)
    lines = [f"**{_md_escape(rec.title)}**"]
    if where:
        step = f" · step {rec.step}" if rec.step else ""
        lines.append(f"_{_md_escape(where)}{step}_")
    if rec.use_when:
        lines.append(f"> {_md_escape(rec.use_when)}")
    if rec.variables:
        shown = ", ".join(f"`{v.replace('`', '')}`" for v in rec.variables[:8])
        more = f" +{len(rec.variables) - 8}" if len(rec.variables) > 8 else ""
        lines.append(_t("prompts.tg.vars", "✏️ Fill in: {vars}", vars=shown + more))
    return "\n".join(lines)


def build_markdown(rec: Any, body: str | None = None) -> str | None:
    """The full rich-message markdown, or ``None`` when a file is the right format."""
    body = rec.body if body is None else body
    if "```" in body:
        return None
    md = f"{header_markdown(rec)}\n\n```markdown\n{body.rstrip()}\n```"
    return md if len(md) <= RICH_LIMIT else None


def prompt_keyboard(rec: Any, *, store: Any = None) -> list[list[dict]]:
    """⭐ favourite · ▶ next step (collections) · 📄 as file."""
    fav_label = (
        _t("prompts.btn.unfav", "★ Favorite") if rec.favorite else _t("prompts.btn.fav", "☆ Favorite")
    )
    row = [
        {"text": fav_label, "callback_data": f"{CB_PREFIX}f:{short_key(rec.id)}"},
        {"text": _t("prompts.btn.file", "📄 .md file"), "callback_data": f"{CB_PREFIX}d:{short_key(rec.id)}"},
    ]
    nxt = next_step(rec, store=store)
    if nxt is not None:
        row.append(
            {"text": _t("prompts.btn.next", "▶ Next step"), "callback_data": f"{CB_PREFIX}s:{short_key(nxt.id)}"}
        )
    return [row]


def next_step(rec: Any, *, store: Any = None) -> Any:
    if not rec.collection or rec.step is None:
        return None
    from navig.store.prompt_library import get_prompt_store

    steps = (store or get_prompt_store()).collection_steps(rec.collection)
    later = [s for s in steps if s.step is not None and s.step > rec.step and s.domain == rec.domain]
    return min(later, key=lambda s: s.step) if later else None


async def send_prompt(
    channel: Any,
    chat_id: int,
    rec: Any,
    *,
    as_file: bool = False,
    body: str | None = None,
    reply_to_message_id: int | None = None,
    store: Any = None,
) -> bool:
    """Send one prompt. Returns True when Telegram accepted it."""
    from navig.store.prompt_library import get_prompt_store

    store = store or get_prompt_store()
    text = rec.body if body is None else body
    keyboard = prompt_keyboard(rec, store=store)
    md = None if as_file else build_markdown(rec, text)
    result: Any = None
    if md is not None:
        result = await channel.send_rich_message(
            chat_id, markdown=md, reply_to_message_id=reply_to_message_id, keyboard=keyboard
        )
    else:
        caption = f"<b>{html.escape(rec.title)}</b>"
        if rec.use_when:
            caption += f"\n<i>{html.escape(rec.use_when[:700])}</i>"
        filename = (rec.id.rsplit("/", 1)[-1] or "prompt") + ".md"
        payload = f"# {rec.title}\n\n{text.rstrip()}\n".encode()
        result = await channel.send_document(
            chat_id, payload, filename=filename, caption=caption[:1000],
            reply_to_message_id=reply_to_message_id,
        )
        if result:
            # Documents take no inline keyboard through this helper; send the actions after.
            await channel.send_message(
                chat_id, f"📄 <b>{html.escape(rec.title)}</b>", parse_mode="HTML", keyboard=keyboard
            )
    ok = bool(result)
    if ok:
        try:
            store.record_use(rec.id)
        except Exception:  # noqa: BLE001 — usage stats must never fail a delivery
            logger.debug("record_use failed", exc_info=True)
    return ok


def picker_html(query: str, matches: list[Any]) -> str:
    if query:
        return _t("prompts.tg.pick", "📚 <b>{count}</b> prompts match <b>{query}</b>. Pick one:",
                  count=len(matches), query=html.escape(query))
    return _t("prompts.tg.browse", "📚 <b>Prompt library</b>. Send <code>/prompt name or words</code>, or pick one:")


def picker_keyboard(matches: list[Any]) -> list[list[dict]]:
    rows = []
    for rec in matches[:PICK_LIMIT]:
        star = "★ " if rec.favorite else ""
        label = f"{star}{rec.title}"
        if len(label) > 60:
            label = label[:59] + "…"
        rows.append([{"text": label, "callback_data": f"{CB_PREFIX}o:{short_key(rec.id)}"}])
    return rows
