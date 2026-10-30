"""Monitoring surfaces as cards — meters, sheets, and one place to act.

The `/disk`, `/memory` and `/cpu` commands used to answer with a hand-built
string: a "━" rule, an escaped title, then blocks of ``<code>`` bars padded by
counting characters.  On a rich Telegram client that reads as a wall of
monospace, and every handler had to remember the same three-line shape.

This module gives those surfaces a card instead: one meter table (status icon,
bar, percent, the numbers behind it), an optional sheet of labelled facts, the
verdict as a footer, and the refresh/navigation buttons inside the bubble.  The
plain HTML is still produced by the caller as the degradation path, so a client
without rich messages keeps its text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from navig.telegram.card_doc import (
    Bullets,
    Button,
    Card,
    Para,
    PullQuote,
    Raw,
    Table,
    esc,
)

# Percentage thresholds shared with the classic helpers: amber at 60, red at 80.
AMBER_PCT = 60.0
RED_PCT = 80.0


@dataclass(frozen=True)
class Meter:
    """One measured thing: a label, a percentage, and the numbers behind it."""

    label: str
    pct: float
    used: float | None = None
    total: float | None = None
    unit: str = "GB"
    note: str = ""


def bar(pct: float, width: int = 14) -> str:
    """A unicode bar, e.g. ``██████░░░░░░░░`` for 43 %."""
    filled = int(round(max(0.0, min(100.0, float(pct))) / 100 * width))
    return "█" * filled + "░" * (width - filled)


def status_icon(pct: float) -> str:
    if pct >= RED_PCT:
        return "🔴"
    if pct >= AMBER_PCT:
        return "🟡"
    return "🟢"


def check_icon(ok: bool) -> str:
    """Green or red for a yes/no fact — a service that answers, or does not.

    Separate from :func:`status_icon` because a boolean has no amber: something
    either answers on its port or it does not, and "half online" is not a state
    a reader can act on.
    """
    return "🟢" if ok else "🔴"


# Units that run the daemon, in the order a card should name them.  Asking only
# for `navig-daemon` was a lie on every install that ships `navig-agent`: the
# unit does not exist, `systemctl show` answers `inactive`, and the card reported
# a dead daemon WHILE THE DAEMON WAS SERVING IT.
DAEMON_UNITS = ("navig-agent", "navig-daemon", "navig-gateway")


def parse_units(text: str) -> dict[str, tuple[str, str]]:
    """Parse ``systemctl show`` output into ``{unit: (active_state, since)}``.

    Blocks are separated by a blank line and each starts with ``Id=``.
    """
    out: dict[str, tuple[str, str]] = {}
    unit, state, since = "", "", ""
    for raw in str(text).splitlines():
        line = raw.strip()
        if not line:
            if unit:
                out[unit] = (state, since)
            unit, state, since = "", "", ""
            continue
        if line.startswith("Id="):
            unit = line.split("=", 1)[1].strip()
        elif line.startswith("ActiveState="):
            state = line.split("=", 1)[1].strip()
        elif line.startswith("ActiveEnterTimestamp="):
            value = line.split("=", 1)[1].strip()
            if value and value != "n/a":
                parts = value.split()
                since = parts[-2] if len(parts) >= 2 else value
    if unit:
        out[unit] = (state, since)
    return out


def daemon_fact(states: dict[str, tuple[str, str]]) -> tuple[bool, str] | None:
    """The one active daemon unit, as ``(ok, text)`` — ``None`` when none says active.

    Keys may carry the ``.service`` suffix that ``systemctl show`` prints, so the
    lookup accepts both spellings.
    """
    normalized = {unit.removesuffix(".service"): value for unit, value in states.items()}
    for unit in DAEMON_UNITS:
        state, since = normalized.get(unit, ("", ""))
        if state == "active":
            suffix = f", since {since}" if since else ""
            return True, f"active as {unit}{suffix}"
    return None


def bridge_fact(configured_port: int | None, listening: bool) -> str:
    """How the LLM bridge reads.

    An install served entirely by cloud providers has no local bridge at all —
    ``get_llm_port()`` returns ``None``.  That is not a fault, so it must not be
    red; only a bridge that was configured and does not answer is.
    """
    if configured_port is None:
        return "not used — no local bridge configured"
    state = "online" if listening else "offline"
    return f"{check_icon(listening)} {state} · port {configured_port}"


# Filesystems that are images, not disks: a squashfs snap is 100 % full by
# construction because it is a packed read-only image, and a tmpfs is RAM.
_NOT_A_VOLUME = frozenset({"squashfs", "tmpfs", "devtmpfs", "overlay", "ramfs", "iso9660"})

# Paths that are kernel or image surfaces, never a disk the operator can clean.
_VIRTUAL_MOUNTS = ("/snap", "/run", "/sys", "/proc", "/dev")


def is_measurable(mountpoint: str, fstype: str = "") -> bool:
    """Whether a mount point is a volume whose fill level means something.

    Without this, `/snap/core22/2437` reports 100 % on a healthy machine and the
    card turns red for an immutable image — the loudest thing on the screen is
    also the least actionable.
    """
    if str(fstype).lower() in _NOT_A_VOLUME:
        return False
    path = str(mountpoint).rstrip("/") or "/"
    return not any(path == head or path.startswith(head + "/") for head in _VIRTUAL_MOUNTS)


def _amount(meter: Meter) -> str:
    if meter.used is None:
        return ""
    if meter.total:
        return f"{meter.used:.1f} / {meter.total:.1f} {meter.unit}"
    return f"{meter.used:.1f} {meter.unit}"


def _meter_rows(meters: Sequence[Meter]) -> list[list[str | Raw]]:
    rows: list[list[str | Raw]] = []
    for meter in meters:
        cell = (
            f"{status_icon(meter.pct)} <b>{esc(meter.label)}</b>"
            f"<br/><code>{bar(meter.pct)}</code> <b>{float(meter.pct):.0f}%</b>"
        )
        detail = " · ".join(part for part in (_amount(meter), meter.note) if part)
        rows.append([Raw(cell), detail or Raw(" ")])
    return rows


def _add_buttons(card: Card, buttons: Sequence[tuple[str, str]]) -> Card:
    """Lay ``(label, callback_data)`` pairs out two per row, inside the bubble."""
    pairs = [(label, data) for label, data in buttons if label and data]
    for index in range(0, len(pairs), 2):
        card.buttons(*[Button(label, data=data) for label, data in pairs[index : index + 2]])
    return card


def meter_card(
    icon: str,
    title: str,
    *,
    meta: str = "",
    meters: Sequence[Meter] = (),
    notes: Sequence[tuple[str, str]] = (),
    extras: Sequence[object] = (),
    verdict: str = "",
    footer: str = "",
    buttons: Sequence[tuple[str, str]] = (),
) -> Card:
    """A monitoring card: the meters, then facts, then a verdict.

    ``notes`` are ``(label, value)`` pairs shown as a borderless sheet — the
    place for "cores", "load average", "updated" and similar.  ``extras`` are
    ready-made blocks inserted after the sheet.
    """
    card = Card(title, glyph=icon, meta=meta, big=True)
    if meters:
        card.add(Table(rows=_meter_rows(meters), bordered=False))
    if notes:
        card.add(Table(rows=[[label, Raw(value)] for label, value in notes], bordered=False))
    for block in extras:
        card.add(block)
    if verdict:
        card.add(Para(verdict, style="italic"))
    if footer:
        card.footer = footer
    return _add_buttons(card, buttons)


def sheet_card(
    icon: str,
    title: str,
    *,
    meta: str = "",
    sheet: Sequence[tuple[str, str | Raw]] = (),
    blocks: Sequence[object] = (),
    caption: str = "",
    verdict: str = "",
    footer: str = "",
    buttons: Sequence[tuple[str, str]] = (),
) -> Card:
    """A monitoring card of labelled facts — for ports, services, uptime."""
    card = Card(title, glyph=icon, meta=meta, big=True)
    if sheet:
        card.add(Table(rows=[[label, value] for label, value in sheet], caption=caption))
    for block in blocks:
        card.add(block)
    if verdict:
        card.add(PullQuote(verdict))
    if footer:
        card.footer = footer
    return _add_buttons(card, buttons)


def buttons_from_keyboard(
    keyboard: Sequence[Sequence[dict]] | None,
) -> list[tuple[str, str]]:
    """Flatten a classic inline keyboard into ``(label, callback_data)`` pairs."""
    pairs: list[tuple[str, str]] = []
    for row in keyboard or ():
        for button in row:
            data = str(button.get("callback_data") or "")
            if data:
                pairs.append((str(button.get("text") or "•"), data))
    return pairs


def process_table(
    rows: Sequence[tuple[str, str, float]],
    *,
    caption: str = "Top processes",
    limit: int = 8,
) -> Table:
    """``(pid, name, percent)`` triples as a right-aligned table."""
    return Table(
        header=["PID", "Process", "CPU"],
        rows=[
            [pid, esc(name), f"{float(pct):.1f}%"]
            for pid, name, pct in list(rows)[:limit]
        ],
        caption=caption,
    )


def bullets(lines: Sequence[str]) -> Bullets | None:
    """A bullet list, or ``None`` when there is nothing to list."""
    items = [str(line).strip() for line in lines if str(line).strip()]
    return Bullets(items) if items else None


__all__ = [
    "AMBER_PCT",
    "DAEMON_UNITS",
    "RED_PCT",
    "Meter",
    "bar",
    "bridge_fact",
    "bullets",
    "buttons_from_keyboard",
    "check_icon",
    "daemon_fact",
    "is_measurable",
    "meter_card",
    "parse_units",
    "process_table",
    "sheet_card",
    "status_icon",
]
