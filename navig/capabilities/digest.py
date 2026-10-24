"""The short capability digest placed in the MCP ``initialize`` instructions.

A client shows ``instructions`` to the model before its first tool call, on every session,
so this is the one place where "NAVIG can generate images and you have Instagram
connected" reaches the agent without being asked. It is budgeted (default 600 chars) and
built only from the overview — plugin names and account states, never a value.
"""

from __future__ import annotations

from typing import Any

#: Plugins whose presence most changes what an agent should offer, in display order.
_HEADLINE = (
    "navig-generate", "navig-audio", "navig-video", "navig-social", "navig-distribution",
    "navig-email", "navig-telegram", "navig-mobile", "navig-download", "navig-pipeline",
)  # fmt: skip

_GLYPH = {"connected": "✓", "expires": "expiring", "rejected": "rejected", "missing": "—"}


def _accounts_text(accounts: list[dict[str, Any]]) -> str:
    parts = []
    for account in accounts:
        state = account.get("state") or "unknown"
        if state == "unknown":
            continue
        label = account.get("label") or ""
        name = f"{account['provider']} {label}".strip()
        parts.append(f"{name} {_GLYPH.get(state, state)}")
    return ", ".join(parts)


def build_digest(overview: dict[str, Any], *, max_chars: int = 600) -> str:
    plugins = {p["plugin"]: p for p in overview.get("plugins") or []}
    if not plugins:
        return ""
    lines = []
    ordered = [plugins[n] for n in _HEADLINE if n in plugins]
    for plugin in ordered:
        short = plugin["plugin"].removeprefix("navig-")
        accounts = _accounts_text(plugin.get("accounts") or [])
        lines.append(f"{short} ({accounts})" if accounts else short)
    others = len(plugins) - len(ordered)
    head = "Installed: " + "; ".join(lines)
    if others > 0:
        head += f"; +{others} more plugins"
    tail = (
        ". Before telling the operator something is impossible, call navig_capabilities "
        "(find/overview). After you create or receive media, call navig_capabilities "
        "action=suggest and offer the next steps (edit, audio, review, post) before acting."
    )
    text = head + tail
    if len(text) <= max_chars:
        return text
    # Trim the plugin list, never the instruction that makes the agent use it.
    room = max(0, max_chars - len(tail) - len("Installed: …"))
    return "Installed: " + "; ".join(lines)[:room].rsplit("; ", 1)[0] + " …" + tail
