"""
NAVIG Narrator — styled "story" output for high-level operator events.

This is NOT a logger. The per-line log format (``navig/core/logging.py``)
stays as-is for ops/grep. The narrator is for the handful of moments where
the daemon should tell the operator what it's doing in a way they can read
at a glance: boot phases, cloud-manager state changes, /start agent traces,
big incident decisions.

Visual language (inspired by the user's mockup):

    [brain]  NAVIG is reasoning ...
    [radio]  Connecting to production-01 ...
    [anchor] Session established. Latency: 18ms.
    [gear]   Fetching nginx metrics ...

       worker_connections: 512 (at limit)
       request_queue: 847 pending
       avg_response_time: 4.2s

    [brain]  Root cause: Worker pool saturated.
    [brain]  Fix: Scale workers or enable upstream cache.
    [shield] Confidence: 94%. Action queued.
    [wave]   Drift score: 0. No pending changes.

Three rendering paths:
  - TTY with Unicode-capable encoding: full color + emoji + box drawing
  - TTY without Unicode:               color + ASCII fallback glyphs
  - non-TTY (piped, file, cron):       silent (the regular logger picks up)

Use sparingly. Every narrator call costs an operator's attention; reserve
them for events worth interrupting their eye for.
"""

from __future__ import annotations

import os
import sys
from typing import Iterable, Literal

# Glyph map: (unicode, ASCII fallback). Picked to read as "what kind of event".
Icon = Literal[
    "brain",    # reasoning, decisions, LLM output
    "radio",    # network, connecting
    "anchor",   # established, stable, ready
    "gear",     # initializing, fetching, working
    "shield",   # security, auth, confidence
    "wave",     # state change, drift
    "check",    # success
    "warn",     # warning
    "cross",    # failure
    "spark",    # ready / online
    "lock",     # privacy / private
    "globe",    # public / cloud
    "dot",      # neutral bullet
]

_GLYPHS_UNICODE: dict[Icon, str] = {
    "brain":  "🧠",
    "radio":  "📡",
    "anchor": "⚓",
    "gear":   "⚙ ",
    "shield": "🛡 ",
    "wave":   "≈",     # plain math wave; renders everywhere
    "check":  "✓",
    "warn":   "⚠",
    "cross":  "✗",
    "spark":  "✨",
    "lock":   "🔒",
    "globe":  "◉",     # was 🌐 -- doesn't render in legacy PS consoles
    "dot":    "•",
}
_GLYPHS_ASCII: dict[Icon, str] = {
    "brain":  "(*)",
    "radio":  "((·))",
    "anchor": "[~]",
    "gear":   "[#]",
    "shield": "[!]",
    "wave":   "~~~",
    "check":  "[ok]",
    "warn":   "[!]",
    "cross":  "[x]",
    "spark":  "[+]",
    "lock":   "[#]",
    "globe":  "[@]",
    "dot":    "*",
}

# Color names per icon (rich style strings). Tuned to feel like the mockup:
# cool blues/teals for connection, green for success, amber for warning,
# muted gray for ambient/dim, bold cyan for "the daemon is thinking".
_STYLES: dict[Icon, str] = {
    "brain":  "bold magenta",
    "radio":  "cyan",
    "anchor": "bright_cyan",
    "gear":   "bright_blue",
    "shield": "yellow",
    "wave":   "blue",
    "check":  "bold green",
    "warn":   "bold yellow",
    "cross":  "bold red",
    "spark":  "bold green",
    "lock":   "bright_white",
    "globe":  "bright_cyan",
    "dot":    "dim",
}


def _is_unicode_capable() -> bool:
    """Best-effort: does stdout's encoding support our emoji glyphs?"""
    enc = (getattr(sys.stdout, "encoding", "") or "").lower()
    if "utf" in enc:
        return True
    # NAVIG_FORCE_ASCII=1 escape hatch for environments that lie about utf-8
    return False


def _is_tty() -> bool:
    if os.environ.get("NAVIG_FORCE_NARRATOR") == "1":
        return True
    if os.environ.get("NAVIG_NO_NARRATOR") == "1":
        return False
    return bool(sys.stdout and sys.stdout.isatty())


def _glyph(icon: Icon) -> str:
    table = _GLYPHS_UNICODE if _is_unicode_capable() else _GLYPHS_ASCII
    return table.get(icon, "")


def is_active() -> bool:
    """True when narrator output will actually render (TTY, not disabled).

    Callers use this to suppress redundant *console* logging while the styled
    story is on screen — e.g. the gateway lifts INFO log chatter off the
    console during boot only when the narrator is the one telling the story.
    When this is False (piped/cron/file) the narrator stays silent and the
    regular logger must remain the source of truth, so callers should NOT
    quiet anything.
    """
    return _is_tty()


_console = None


def _get_console():
    """Lazy-load the shared console_helper Console proxy."""
    global _console
    if _console is None:
        from navig import console_helper as _ch
        _console = _ch.console
    return _console


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def phase(text: str, icon: Icon = "brain") -> None:
    """Top-level phase header. Bold, colored, with a leading glyph.

    Use for "I'm starting a meaningful piece of work" moments. Boot phases,
    incident root-cause statements, cloud-state transitions.
    """
    if not _is_tty():
        return
    try:
        c = _get_console()
        g = _glyph(icon).rstrip()
        style = _STYLES.get(icon, "bold")
        c.print(f"[{style}]{g}[/{style}]  [bold]{text}[/bold]")
    except Exception:  # noqa: BLE001
        # Narrator must never break the boot path. Fall back to plain print.
        print(f"  {text}", flush=True)


def step(text: str, icon: Icon = "dot") -> None:
    """Indented sub-step. Dim color, smaller glyph -- ambient activity."""
    if not _is_tty():
        return
    try:
        c = _get_console()
        g = _glyph(icon).rstrip()
        style = _STYLES.get(icon, "dim")
        c.print(f"  [{style}]{g}[/{style}] [dim]{text}[/dim]")
    except Exception:  # noqa: BLE001
        print(f"    {text}", flush=True)


def step_row(
    label: str,
    value: str = "",
    *,
    note: str = "",
    icon: Icon = "dot",
    label_width: int = 0,
    value_width: int = 0,
) -> None:
    """Prose sub-step row: ``<icon>  label  · value  note``.

    The boot/health "story" line from the mockup: a color-coded glyph, the
    label in readable white, then dim trailing detail (a timing as ``· 0.04s``
    and/or a ``note`` such as an address). Deliberately NOT column-aligned —
    leading emoji render as 1 or 2 cells depending on the terminal, so any
    fixed-width label column drifts; prose reads cleanly regardless. The glyph
    is rstrip'd so the icon→label gap is uniform across rows. ``label_width`` /
    ``value_width`` are accepted for call-site compatibility and ignored.
    """
    if not _is_tty():
        return
    try:
        c = _get_console()
        g = _glyph(icon).rstrip()
        style = _STYLES.get(icon, "dim")
        detail = ""
        if value:
            detail += f"  [dim]· {value}[/dim]"
        if note:
            detail += f"  [dim]{note}[/dim]"
        c.print(f"  [{style}]{g}[/{style}]  [white]{label}[/white]{detail}")
    except Exception:  # noqa: BLE001
        bits = [b for b in (label, value, note) if b]
        print("    " + "  ".join(bits), flush=True)


def metrics(rows: Iterable[tuple[str, str]], border_color: str = "cyan") -> None:
    """Left-bordered key:value block. Like the nginx metrics block in the mockup.

    rows is an iterable of (label, value) pairs. Values are rendered with a
    subtle accent color so the eye lands on them first.
    """
    if not _is_tty():
        return
    try:
        c = _get_console()
        rows_list = list(rows)
        if not rows_list:
            return
        max_label = max(len(label) for label, _ in rows_list)
        for label, value in rows_list:
            pad = " " * (max_label - len(label))
            c.print(
                f"     [{border_color}]│[/{border_color}]  "
                f"[white]{label}[/white]{pad}: [bold bright_cyan]{value}[/bold bright_cyan]"
            )
    except Exception:  # noqa: BLE001
        for label, value in rows:
            print(f"    {label}: {value}", flush=True)


def verdict(
    text: str,
    *,
    confidence: int | None = None,
    icon: Icon = "shield",
) -> None:
    """Final-result line with optional confidence percentage.

    Pattern: ``[shield] Confidence: 94%. Action queued.``
    """
    if not _is_tty():
        return
    try:
        c = _get_console()
        g = _glyph(icon)
        style = _STYLES.get(icon, "bold")
        suffix = f" [bold]{confidence}%[/bold]" if confidence is not None else ""
        c.print(f"[{style}]{g}[/{style}]  [bold]{text}[/bold]{suffix}")
    except Exception:  # noqa: BLE001
        print(f"  {text}", flush=True)


def divider(text: str = "") -> None:
    """A thin section divider, optionally labelled."""
    if not _is_tty():
        return
    try:
        c = _get_console()
        if text:
            c.print(f"\n[dim]── {text} {'─' * max(0, 40 - len(text))}[/dim]\n")
        else:
            c.print("\n[dim]" + "─" * 50 + "[/dim]\n")
    except Exception:  # noqa: BLE001
        if text:
            print(f"\n-- {text} --\n", flush=True)
        else:
            print("\n" + "-" * 50 + "\n", flush=True)


def blank() -> None:
    """Just a blank line. Pairs well with a phase()/verdict() sandwich."""
    if not _is_tty():
        return
    try:
        _get_console().print("")
    except Exception:  # noqa: BLE001
        print("", flush=True)
