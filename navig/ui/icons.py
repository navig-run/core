"""
navig.ui.icons — Semantic icon resolution with ASCII fallbacks.

All icons in NAVIG CLI output go through `icon()` — never hardcode
Unicode directly in command modules.

Usage:
    from navig.ui.icons import icon
    icon("ok")      # "✓" or "[ok]" in safe mode
    icon("daemon")  # "◉" or "[~]"
"""
from __future__ import annotations

from navig.ui.theme import SAFE_MODE

# (rich_icon, safe_icon)
_ICONS: dict[str, tuple[str, str]] = {
    "ok":        ("✓", "[ok]"),
    "fail":      ("✗", "[!!]"),
    "warn":      ("⚠", "[!]"),
    "info":      ("ℹ", "[i]"),
    "daemon":    ("◉", "[~]"),
    "offline":   ("○", "[ ]"),
    "node":      ("⬡", "[n]"),
    "host":      ("⬛", "[h]"),
    "app":       ("▣", "[a]"),
    "vault":     ("🔑", "[k]"),
    "arrow":     ("→", "->"),
    "bullet":    ("•", "-"),
    "flag":      ("⚑", ">>"),
    "spinner":   ("◌", "..."),
    "ai":        ("✦", "[ai]"),
    "check":     ("☑", "[x]"),
    "cross":     ("☒", "[ ]"),
    "add":       ("+", "+"),
    "remove":    ("-", "-"),
    "context":   ("│", "|"),
    "git":       ("⎇", "[git]"),
    "cloud":     ("☁", "[cld]"),
    "lock":      ("🔒", "[lk]"),
    "debug":     ("⊙", "[dbg]"),
}

_FALLBACK = ("?", "?")


def icon(name: str) -> str:
    """Return the appropriate icon for *name* based on SAFE_MODE."""
    pair = _ICONS.get(name, _FALLBACK)
    return pair[1] if SAFE_MODE else pair[0]


def icon_pair(name: str) -> tuple[str, str]:
    """Return (rich_icon, safe_icon) tuple without mode detection."""
    return _ICONS.get(name, _FALLBACK)
