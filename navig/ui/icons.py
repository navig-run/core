"""
navig.ui.icons — Semantic icon resolution with ASCII fallbacks.

All icons in NAVIG CLI output go through `icon()` — never hardcode
Unicode directly in command modules.

Usage:
    from navig.ui.icons import icon, nf_icon
    icon("ok")       # "✓" or "[ok]" in safe mode
    icon("daemon")   # "◉" or "[~]"
    nf_icon("bolt")  # Nerd Font U+F0E7 codepoint, or icon() fallback
"""

from __future__ import annotations

from navig.ui.theme import NERD_FONT_AVAILABLE, SAFE_MODE

# (rich_icon, safe_icon)
_ICONS: dict[str, tuple[str, str]] = {
    "ok": ("✓", "[ok]"),
    "fail": ("✗", "[!!]"),
    "warn": ("⚠", "[!]"),
    "info": ("ℹ", "[i]"),
    "daemon": ("◉", "[~]"),
    "offline": ("○", "[ ]"),
    "node": ("⬡", "[n]"),
    "host": ("⬛", "[h]"),
    "app": ("▣", "[a]"),
    "vault": ("🔑", "[k]"),
    "arrow": ("→", "->"),
    "bullet": ("•", "-"),
    "flag": ("⚑", ">>"),
    "spinner": ("◌", "..."),
    "ai": ("✦", "[ai]"),
    "check": ("☑", "[x]"),
    "cross": ("☒", "[ ]"),
    "add": ("+", "+"),
    "remove": ("-", "-"),
    "context": ("│", "|"),
    "git": ("⎇", "[git]"),
    "cloud": ("☁", "[cld]"),
    "lock": ("🔒", "[lk]"),
    "debug": ("⊙", "[dbg]"),
    # ── AI / Bot ──────────────────────────────────────────────────────────────
    "bolt":      ("⚡", ">>"),     # speed / small model tier / bridge
    "brain":     ("🧠", "[AI]"),   # big model tier
    "robot":     ("🤖", "[bot]"),  # bot / NAVIG ready
    "computer":  ("💻", "[pc]"),   # code model tier
    # ── Actions ───────────────────────────────────────────────────────────────
    "search":    ("🔍", "[?]"),    # explain / search
    "pencil":    ("✏",  "[e]"),    # correct / edit
    "improve":   ("⬆",  "[^]"),   # improve / upgrade
    "puzzle":    ("🧩", "[s]"),    # simplify
    "globe":     ("🌐", "[G]"),    # translate
    "idea":      ("💡", "[*]"),    # brainstorm
    "note":      ("📝", "[doc]"),  # proofread / standard confirm / sessions
    "palette":   ("🎨", "[art]"),  # creative
    "clipboard": ("📋", "[=]"),    # summary
    # ── UI / Misc ─────────────────────────────────────────────────────────────
    "tick":      ("✅", "[Y]"),    # selection confirmed
    "reminder":  ("⏰", "[t]"),    # reminders / timer
    "voice":     ("🔊", "[v]"),    # voice replies
    "focus":     ("🎯", "[>]"),    # focus mode
    "auth":      ("🔐", "[A]"),    # authentication
}

_FALLBACK = ("?", "?")


def icon(name: str) -> str:
    """Return the appropriate icon for *name* based on SAFE_MODE."""
    pair = _ICONS.get(name, _FALLBACK)
    return pair[1] if SAFE_MODE else pair[0]


def icon_pair(name: str) -> tuple[str, str]:
    """Return (rich_icon, safe_icon) tuple without mode detection."""
    return _ICONS.get(name, _FALLBACK)


# ── Nerd Font codepoints ──────────────────────────────────────────────────────────────────────
# Used by the PS modules (navig-icons.psm1 mirrors these exactly) and by
# Python consumers that want Nerd Font glyphs when the terminal supports them.
# Keys match _ICONS where applicable; nf_icon() falls back to icon() otherwise.
_NF_ICONS: dict[str, str] = {
    # AI / Agents
    "agent":    "\U000F06D4",  # nf-md-robot
    "brain":    "\U000F18B4",  # nf-md-brain
    "code":     "\uf121",     # nf-fa-code
    "magic":    "\uf0d0",     # nf-fa-magic
    # Status
    "ready":    "\uf111",     # nf-fa-circle
    "stopped":  "\uf111",     # nf-fa-circle
    "loading":  "\uf110",     # nf-fa-spinner
    "ok":       "\uf058",     # nf-fa-check_circle
    "fail":     "\uf057",     # nf-fa-times_circle
    "warn":     "\uf071",     # nf-fa-exclamation_triangle
    # Performance
    "bolt":     "\uf0e7",     # nf-fa-bolt
    "rocket":   "\uf135",     # nf-fa-rocket
    "chart":    "\uf080",     # nf-fa-bar_chart
    "gpu":      "\uf878",     # nf-fa-microchip
    # Powerline separators
    "sep":      "\ue0b0",     # nf-pl-right_hard_divider
    "sep_thin": "\ue0b1",     # nf-pl-right_soft_divider
    "pl_prompt": "\ue0b6",    # nf-pl-left_half_circle_thick
    "pl_arrow": "\ue0b4",     # nf-pl-right_half_circle_thick
    # Files / System
    "folder":   "\uf115",     # nf-fa-folder_open
    "file":     "\uf15b",     # nf-fa-file
    "terminal": "\uf489",     # nf-dev-terminal
    "linux":    "\uf17c",     # nf-fa-linux
    "apple":    "\uf179",     # nf-fa-apple
    "windows":  "\uf17a",     # nf-fa-windows
    "docker":   "\uf308",     # nf-dev-docker
    "git":      "\uf1d3",     # nf-dev-git
}


def nf_icon(name: str) -> str:
    """Return the Nerd Font glyph for *name*, falling back to ``icon(name)``.

    If ``NERD_FONT_AVAILABLE`` is False (no Nerd Font detected / installed),
    returns the same value as ``icon(name)`` so callers never get replacement
    boxes on unconfigured terminals.  Callers that prefer Nerd Font glyphs
    should use this; callers that only need a terminal-safe symbol use
    ``icon()``.
    """
    if not NERD_FONT_AVAILABLE:
        return icon(name)
    return _NF_ICONS.get(name, icon(name))
