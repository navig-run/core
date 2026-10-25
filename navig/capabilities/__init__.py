"""What this NAVIG install can do, and which accounts it can act as.

A coding agent (DeepSeek Harness, VS Code, Claude Code) only uses what it is told exists.
The MCP surface used to describe core domains only, so the ~1,000 plugin commands and the
accounts behind them (social networks, mailboxes, media provider keys, paired phones) were
invisible: the operator had to say "there is a navig tool for that" every time.

This package is the one place that answers three questions, for the CLI and for MCP:

* ``registry`` — which commands are installed (core + every importable plugin);
* ``probes``   — which accounts/keys/devices each plugin can use right now
  (entry-point group ``navig.capabilities``; presence only, never a secret value);
* ``pipelines``— given a file the agent just produced, which next steps are possible
  with what is installed and connected (edit, re-score, review, post).

``safety`` classifies a command for the approval gate, so ``navig_run`` posts and sends
only through NAVIG's existing approval layer.
"""

from __future__ import annotations

from navig.capabilities.pipelines import suggest
from navig.capabilities.probes import run_probes
from navig.capabilities.registry import command_help, commands, find, installed_plugins
from navig.capabilities.safety import classify

__all__ = [
    "classify",
    "command_help",
    "commands",
    "digest",
    "find",
    "installed_plugins",
    "overview",
    "run_probes",
    "suggest",
]


def overview(*, probes: bool = True, with_counts: bool = True) -> dict:
    """Installed plugins plus every probe's accounts — the agent's first look.

    Each row carries ``accounts_status`` so an empty ``accounts`` list can be read
    correctly. A plugin whose installed version registers no probe returns
    ``unreported``: that means "this build cannot say", NOT "the operator has no
    account" — the two used to be indistinguishable, and an agent that assumes the
    latter tells the operator their connected Instagram does not exist.
    """
    plugins = installed_plugins(with_counts=with_counts)
    results = run_probes() if probes else {}
    unreported: list[str] = []
    for plugin in plugins:
        name = plugin.get("probe")
        status = results.get(name) if name else None
        plugin["accounts"] = (status or {}).get("accounts", [])
        if status and status.get("notes"):
            plugin["notes"] = status["notes"]
        if not probes:
            plugin["accounts_status"] = "skipped"
        elif not name:
            plugin["accounts_status"] = "unreported"
            unreported.append(plugin["plugin"])
        elif status and status.get("error"):
            plugin["accounts_status"] = "error"
        else:
            plugin["accounts_status"] = "reported"
    return {
        "plugins": plugins,
        "probes_failed": sorted(n for n, r in results.items() if r.get("error")),
        "probes_unreported": sorted(unreported),
        "next": (
            "Call navig_capabilities action=find with a goal to get exact commands, "
            "action=suggest with a file you produced for next steps, then navig_run to "
            "execute one."
        ),
    }


def digest(max_chars: int = 600) -> str:
    """One short line per useful fact, for the MCP ``initialize`` instructions."""
    from navig.capabilities.digest import build_digest

    return build_digest(overview(with_counts=False), max_chars=max_chars)
