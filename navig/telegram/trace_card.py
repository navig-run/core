"""`/trace` as a card: what the session is doing, in sheets instead of a dump.

The old trace printed one long HTML blob: a divider, a paragraph of fields, then a
column of `- label -> value` lines whose arrows and glyphs had been mangled into
plain hyphens.  It was also unreachable by button — "Refresh" *deleted* the message
and sent a new one, so a trace left open in a chat multiplied.

The card keeps the same facts and gives each one a home: a sheet for the state,
bullets for the recent turns and for daemon warnings, a preformatted block for the
latency bars (they are drawn with block characters and need a monospace face), and
buttons that redraw the message they are on.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Sequence

from navig.telegram.card_doc import (
    Bullets,
    Button,
    Card,
    Code,
    Para,
    PullQuote,
    Raw,
    Table,
    esc,
)

#: How much of the message budget the latency bars may assume is already spent.
#: Generating them needs a number before the card exists; this is a budget, not a
#: measurement, so the bars can never be the reason the trace fails to send.
LATENCY_BUDGET = 1200

ROLE_MARK = {"user": "→ you", "human": "→ you", "assistant": "← navig", "system": "· sys"}


def _row(label: str, value: str | Raw) -> list[str | Raw]:
    return [label, value if isinstance(value, Raw) else esc(value)]


def state_rows(
    *,
    bridge_online: bool,
    bridge_url: str,
    messages: int,
    sessions: int,
    tier: str,
    host: str,
    voice: str,
    vault: str,
) -> list[list[str | Raw]]:
    """The four facts a trace is really asked for, as sheet rows."""
    bridge = (
        Raw(f"<b>online</b> <code>{esc(bridge_url)}</code>")
        if bridge_online
        else Raw("<b>offline</b> — the model router answers")
    )
    counts = Raw(f"<b>{messages}</b> msgs · {sessions} session(s)")
    session = Raw(
        f"tier <code>{esc(tier)}</code> · host <code>{esc(host)}</code> · voice <code>{esc(voice)}</code>"
    )
    return [
        _row("🔌 Bridge Grid", bridge),
        _row("🧠 Memory", counts),
        _row("🎚 Session", session),
        _row("🔐 Vault", vault),
    ]


def recent_bullets(recent: Sequence[dict]) -> list[Raw]:
    """One bullet per turn: time, who, and a short preview."""
    bullets: list[Raw] = []
    for message in recent:
        role = str(message.get("role") or "?")
        preview = str(message.get("preview") or "").strip() or "(empty)"
        stamp = str(message.get("time") or "").strip()
        mark = ROLE_MARK.get(role, f"· {esc(role)}")
        head = f"<code>{esc(stamp)}</code> " if stamp else ""
        bullets.append(Raw(f"{head}{mark}: {esc(preview)}"))
    return bullets


def verdict(*, bridge_online: bool, warnings: int, messages: int) -> tuple[str, bool]:
    """One sentence, and whether it deserves a callout."""
    if warnings:
        return (
            f"{warnings} line(s) in the daemon log want a look — newest last.",
            True,
        )
    if not bridge_online:
        return ("No local bridge on this host — routing is answering directly.", False)
    if not messages:
        return ("This session has no messages yet — say something and trace it.", False)
    return ("Everything answers: bridge, memory, vault.", False)


def trace_card(
    *,
    bridge_online: bool,
    bridge_url: str = "",
    messages: int = 0,
    sessions: int = 0,
    tier: str = "auto",
    host: str = "?",
    voice: str = "?",
    vault: str = "",
    tiers: Sequence[tuple[str, str]] = (),
    recent: Sequence[dict] = (),
    warnings: Sequence[str] = (),
    latency: Sequence[str] = (),
    now: datetime | None = None,
) -> Card:
    """The whole trace as one card."""
    moment = now or datetime.now(timezone.utc)
    card = Card(
        "Recent trace",
        glyph="🔬",
        meta=moment.strftime("%a %d %b · %H:%M UTC"),
        big=True,
    )

    card.add(
        Table(
            rows=state_rows(
                bridge_online=bridge_online,
                bridge_url=bridge_url,
                messages=messages,
                sessions=sessions,
                tier=tier,
                host=host,
                voice=voice,
                vault=vault,
            ),
            bordered=False,
        )
    )

    if tiers:
        card.add(
            Table(
                rows=[[f"· {label}", Raw(f"<code>{esc(route)}</code>")] for label, route in tiers],
                caption="Tiers",
                bordered=False,
            )
        )

    sentence, urgent = verdict(
        bridge_online=bridge_online, warnings=len(warnings), messages=messages
    )
    card.add(PullQuote(sentence) if urgent else Para(sentence, style="italic"))

    if recent:
        card.add(Bullets(recent_bullets(recent)))
    else:
        card.add(Para("No recent activity to show.", style="italic"))

    if warnings:
        card.add(
            Para(f"<b>Daemon log</b> — {len(warnings)} line(s)", style="bold")
        )
        card.add(Bullets([Raw(f"<code>{esc(line[:100])}</code>") for line in warnings[:5]]))

    if latency:
        # Block characters need a monospace face; a table would shear the bars.
        card.add(Code("\n".join(latency), language="text"))

    card.buttons(
        Button("🔄 Refresh", data="trace_refresh", style="primary"),
        Button("🧩 Providers", data="trace_providers"),
        Button("🎛 Model", data="trace_model"),
    )
    card.buttons(Button("✕ Close", data="trace_close", style="danger"))
    return card


__all__ = [
    "LATENCY_BUDGET",
    "ROLE_MARK",
    "recent_bullets",
    "state_rows",
    "trace_card",
    "verdict",
]
