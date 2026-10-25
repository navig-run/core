"""The short capability digest placed in the MCP ``initialize`` instructions.

A client shows ``instructions`` to the model before its first tool call, on every session,
so this is the one place where "NAVIG can generate images and you have Instagram
connected" reaches the agent without being asked. It is budgeted (default 600 chars) and
built only from the overview — plugin names and account states, never a value.

When no plugin in the install registers a capability probe, the digest says so instead of
printing a bare plugin list: an agent that reads "social" with no accounts tends to tell the
operator their Instagram is not connected, when the truth is that this build cannot see it.
"""

from __future__ import annotations

from typing import Any

#: Plugins whose presence most changes what an agent should offer, in display order.
_HEADLINE = (
    "navig-generate", "navig-audio", "navig-video", "navig-social", "navig-distribution",
    "navig-email", "navig-telegram", "navig-mobile", "navig-download", "navig-pipeline",
)  # fmt: skip

_GLYPH = {"connected": "✓", "expires": "expiring", "rejected": "rejected", "missing": "—"}

#: Said only when nothing in the install could report an account (see the module docstring).
_NO_PROBES = (
    ". Account probes are not registered in this install, so an empty list here proves "
    "nothing — ask the operator before saying an account is missing"
)

#: Said when a registered probe failed, so "no account" there was never actually checked.
_PROBE_ERRORS = (
    ". {n} account check(s) failed, so a missing account above is unverified — retry "
    "navig_capabilities before telling the operator it is not connected"
)

_TAIL = (
    ". Before telling the operator something is impossible, call navig_capabilities "
    "(find/overview). After you create or receive media, call navig_capabilities "
    "action=suggest and offer the next steps (edit, audio, review, post) before acting."
)


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
    reported = sum(1 for p in plugins.values() if p.get("accounts_status") == "reported")
    failed = sum(1 for p in plugins.values() if p.get("accounts_status") == "error")
    if failed:
        # A failed check is not an empty one: say so, or the agent reads silence as "no account".
        note = _PROBE_ERRORS.format(n=failed)
    elif reported:
        note = ""
    else:
        note = _NO_PROBES

    text = head + note + _TAIL
    if len(text) <= max_chars:
        return text
    # Trim the plugin list, never the instruction that makes the agent use it.
    room = max_chars - len(_TAIL) - len(note) - len("Installed:  …")
    if room <= 0:  # a budget smaller than the instruction itself
        return _TAIL.strip()
    kept = "; ".join(lines)[:room].rsplit("; ", 1)[0]
    return f"Installed: {kept} …{note}{_TAIL}"
