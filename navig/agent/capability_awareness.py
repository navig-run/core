"""Runtime-derived capability awareness — "can you access <machine>?" asked honestly.

Incident 2026-10-22: the operator asked *"Can you read my DeepSeek conversations on my
computer Neuron?"*. The answer needs three separate states that prose tends to blur:

* **configured** — is there a route at all (an SSH alias, a registered executor, a
  paired remote device)?
* **authorized** — may this surface read private files on that machine, or does it
  need explicit operator consent?
* **reachable** — is the machine online right now? This module deliberately does
  **no network probes**: a green "reachable" claim would be fabricated in an offline
  answer. It reports "not probed" and lets the model say so.

Everything here reads local state only (config dir, SSH config, the OS remote-device
registry), never executes anything, and never raises — the conversation must survive a
missing or unreadable file.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

__all__ = ["AccessState", "configured_routes", "describe", "prompt_block", "ssh_hosts"]

#: Words that make a message a capability question rather than ordinary chat.
_ACCESS_INTENT = re.compile(
    r"\b(access|connect|ssh|rdp|remote|read|open|scan|reach|control|run|execute)\b"
    r"|\b(computer|machine|laptop|server|device|phone|tablet)\b",
    re.IGNORECASE,
)


@dataclass
class AccessState:
    """What is known about reaching one machine, with the evidence for each part."""

    target: str = ""
    configured_routes: list[str] = field(default_factory=list)
    remote_devices: list[str] = field(default_factory=list)
    reachable: str = "not probed"
    requires_consent: bool = True
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "configured_routes": list(self.configured_routes),
            "remote_devices": list(self.remote_devices),
            "reachable": self.reachable,
            "requires_consent": self.requires_consent,
            "notes": list(self.notes),
        }


def _home(home: Path | None = None) -> Path:
    return Path(home) if home is not None else Path.home()


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def ssh_hosts(home: Path | None = None) -> list[str]:
    """Host aliases from ``~/.ssh/config`` (names only — never hostnames/keys)."""
    path = _home(home) / ".ssh" / "config"
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    hosts: list[str] = []
    for line in lines:
        m = re.match(r"\s*Host\s+(.+)", line, re.IGNORECASE)
        if not m:
            continue
        for alias in m.group(1).split():
            if alias and "*" not in alias and "?" not in alias and alias.lower() != "github.com":
                hosts.append(alias)
    return sorted(dict.fromkeys(hosts))


def configured_routes(home: Path | None = None) -> list[str]:
    """Routes this installation actually has, from local state only."""
    routes: list[str] = []
    base = _home(home)
    if _read_json(base / ".navig-os" / "brain-link.json"):
        routes.append("brain-link (this install is a CLIENT of another brain)")
    for alias in ssh_hosts(base):
        routes.append(f"ssh:{alias}")
    return routes


def _remote_devices(db_path: Path | None = None) -> list[dict[str, Any]]:
    """Paired NAVIG OS web devices (``kind='remote'``) — browsers, not executors."""
    path = Path(db_path) if db_path is not None else _home() / ".navig" / "data" / "os_remote.db"
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
        conn.execute("PRAGMA busy_timeout=5000")
        conn.row_factory = sqlite3.Row
        rows = list(conn.execute("select name, kind, last_seen, revoked_at from devices"))
        conn.close()
    except sqlite3.Error:
        return []
    return [
        {"name": str(r["name"] or ""), "kind": str(r["kind"] or ""), "last_seen": r["last_seen"]}
        for r in rows
        if not r["revoked_at"]
    ]


def describe(
    target: str = "", *, home: Path | None = None, db_path: Path | None = None
) -> AccessState:
    """Everything the runtime knows about reaching *target* (or any machine)."""
    state = AccessState(target=target.strip())
    state.configured_routes = configured_routes(home)
    state.remote_devices = sorted({d["name"] for d in _remote_devices(db_path) if d["name"]})
    if target:
        lowered = target.lower()
        aliases = [r.split(":", 1)[1] for r in state.configured_routes if r.startswith("ssh:")]
        if lowered in {a.lower() for a in aliases}:
            state.notes.append(f"{target} is a configured SSH alias.")
        elif lowered in {name.lower() for name in state.remote_devices}:
            state.notes.append(f"{target} is a paired NAVIG OS web device, not an executor.")
        else:
            state.notes.append(
                f"No configured route names {target}: no SSH alias, no executor, no paired device."
            )
    if state.configured_routes:
        state.notes.append("Reachability is not probed in a chat turn; say 'not verified' unless a live check ran.")
    return state


def prompt_block(
    message: str, *, home: Path | None = None, db_path: Path | None = None
) -> str:
    """A compact, runtime-derived block for the *user turn* when access is asked about.

    Returns "" for ordinary conversation so the block never pads normal replies, and
    never claims reachability that was not measured.
    """
    text = str(message or "")
    if not _ACCESS_INTENT.search(text) and not _ACCESS_INTENT.search(text.lower()):
        return ""
    state = describe("", home=home, db_path=db_path)
    lines = [
        "## Capability awareness (runtime-derived; no network probe was run)",
        f"- configured routes: {', '.join(state.configured_routes) or 'none'}",
        f"- paired NAVIG OS web devices: {len(state.remote_devices)} (browsers, not executors)",
        "- reachability: not probed in this turn — say 'not verified' rather than 'offline' or 'online'",
        "- reading private files on another machine requires explicit operator consent and a configured route",
    ]
    if state.remote_devices:
        lines.append(f"- device names: {', '.join(state.remote_devices[:6])}")
    return "\n".join(lines)
