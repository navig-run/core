"""The four Brain choices, in words a person can act on.

The installer used to ask for a "profile" (``cli`` / ``minimal`` / ``server`` /
``full``) — true, but it never says *what the machine will do for you* or *what
happens when you close the lid*. These four roles are the same underlying
configuration, named by consequence:

    run on this computer      all-in-one — everything here, nothing to join
    connect to existing Brain client     — a window onto a Brain elsewhere
    set up always-on Brain    brain      — this host answers for the deployment
    migrate existing Brain    migrate    — move the Brain off the current machine

``role`` is persisted as ``brain.role`` (and mirrored to ``onboarding.role``, the
historical key the join scripts write). Legacy values keep working: ``node`` means
``client``, ``brain`` means ``brain``, absent means ``all-in-one``.

This module is pure data + inspection: it never mutates config and never talks to
the network. The installer and ``navig brain status`` both read it, so there is one
description of each choice instead of two that drift.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from navig.brain.identity import (
    host_is_always_on,
    installation_fingerprint,
)
from navig.brain.identity import (
    owner_id as default_owner_id,
)
from navig.brain.lease import (
    ROLE_ALL_IN_ONE,
    ROLE_BRAIN,
    ROLE_CLIENT,
    LeaseStore,
    brain_settings,
    normalize_role,
)

__all__ = [
    "CHOICES",
    "CHOICE_IDS",
    "choice",
    "choice_for_profile",
    "detect_install_context",
    "host_dependency_warning",
    "role_for_profile",
]

#: Installer profile → brain role. The mapping is the honest one: a "cli"/"minimal"
#: install has no daemon, so it can only be a client of somebody else's Brain.
_PROFILE_ROLE: dict[str, str] = {
    "cli": ROLE_CLIENT,
    "minimal": ROLE_CLIENT,
    "server": ROLE_BRAIN,
    "full": ROLE_ALL_IN_ONE,
    # legacy profiles
    "node": ROLE_CLIENT,
    "operator": ROLE_ALL_IN_ONE,
    "architect": ROLE_ALL_IN_ONE,
    "system_standard": ROLE_ALL_IN_ONE,
    "system_deep": ROLE_ALL_IN_ONE,
}

#: The four choices, in the order they are offered. ``consequence`` is the sentence
#: that stops the misunderstanding behind the incident: an install that borrows a
#: workstation's Brain dies the moment that workstation is switched off.
CHOICES: tuple[dict[str, Any], ...] = (
    {
        "id": ROLE_ALL_IN_ONE,
        "title": "Run on this computer",
        "role": ROLE_ALL_IN_ONE,
        "profile": "full",
        "summary": "NAVIG on this machine does everything: the Brain, the daemon, "
        "Telegram and the UI.",
        "runs": ["daemon", "gateway", "Telegram bot", "scheduler", "deck"],
        "does_not_run": ["a second Brain"],
        "host_dependency": "workstation",
        "consequence": "Everything works while this computer is on. Turn it off and the "
        "bot, scheduled jobs and every other device go quiet — that is expected for "
        "this choice, not a fault.",
        "next_step": "navig service install && navig service start",
    },
    {
        "id": ROLE_CLIENT,
        "title": "Connect to an existing Brain",
        "role": ROLE_CLIENT,
        "profile": "cli",
        "summary": "This machine is a window: it runs commands and UI against a Brain "
        "that lives elsewhere.",
        "runs": ["CLI", "deck/OS client", "remote commands"],
        "does_not_run": ["daemon", "gateway", "Telegram bot", "scheduler"],
        "host_dependency": "none",
        "consequence": "This machine owns no bot, no scheduler and no identity, so it is "
        "safe to switch off. It only works while the Brain is reachable.",
        "next_step": "navig brain status  # then point it at the Brain (navig os link)",
    },
    {
        "id": ROLE_BRAIN,
        "title": "Set up an always-on Brain",
        "role": ROLE_BRAIN,
        "profile": "server",
        "summary": "This host answers for the deployment: bot, scheduler, vault "
        "authority and API, with no desktop session needed.",
        "runs": ["daemon", "gateway", "Telegram bot", "scheduler", "remote API"],
        "does_not_run": ["the desktop UI"],
        "host_dependency": "always-on",
        "consequence": "Every other machine and the bot depend on this host staying up. "
        "If it is a laptop that sleeps, choose 'Connect to an existing Brain' instead.",
        "next_step": "navig brain claim --role brain --reason \"always-on host\"",
    },
    {
        "id": "migrate",
        "title": "Migrate an existing Brain",
        "role": ROLE_BRAIN,
        "profile": "server",
        "summary": "Move the Brain off the current machine: discover, inspect, back up, "
        "transfer, validate, cut over, verify.",
        "runs": ["the guided migration", "then the Brain singletons here"],
        "does_not_run": ["two Brains at once"],
        "host_dependency": "always-on",
        "consequence": "The source machine is stopped and fenced (it writes a moved "
        "marker) so it cannot silently re-claim. Completion requires the remote "
        "capabilities to VERIFY — copied files or installed packages never count.",
        "next_step": "navig brain migrate --to <host> --dry-run",
    },
)

CHOICE_IDS: tuple[str, ...] = tuple(str(c["id"]) for c in CHOICES)


def role_for_profile(profile: str) -> str:
    """The Brain role an installer profile maps to (``all-in-one`` when unknown)."""
    return normalize_role(_PROFILE_ROLE.get(str(profile or "").strip().lower(), ROLE_ALL_IN_ONE))


def choice_for_profile(profile: str) -> str:
    """The choice id an installer profile corresponds to."""
    role = role_for_profile(profile)
    for item in CHOICES:
        if item["id"] == role:
            return str(item["id"])
    return ROLE_ALL_IN_ONE


def choice(role_or_id: str) -> dict[str, Any] | None:
    """Look a choice up by id or role; also accept legacy spellings."""
    key = str(role_or_id or "").strip().lower()
    for item in CHOICES:
        if key in (item["id"], item["role"]):
            return dict(item)
    normalized = normalize_role(key)
    for item in CHOICES:
        if normalized in (item["id"], item["role"]):
            return dict(item)
    return None


def _read_yaml(path: Path) -> dict[str, Any]:
    """Read a config file, treating unreadable exactly like missing (read-only use)."""
    try:
        import yaml  # noqa: PLC0415

        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — inspection, not mutation
        return {}
    return data if isinstance(data, dict) else {}


def detect_install_context(config_dir: Path | None = None) -> dict[str, Any]:
    """What is already installed here — so a choice is never offered blind.

    Reports: an existing config, a configured role, a live/stale lease, a moved
    marker (this NAVIG now lives elsewhere), the deck key fingerprint (never the
    key) and the install profile. Every read is best-effort; a missing file is
    simply absent from the picture.
    """
    from navig.platform import paths  # noqa: PLC0415

    base = Path(config_dir) if config_dir is not None else paths.config_dir()
    config_path = base / "config.yaml"
    cfg = _read_yaml(config_path)
    onboarding = cfg.get("onboarding") if isinstance(cfg.get("onboarding"), dict) else {}
    brain_cfg = cfg.get("brain") if isinstance(cfg.get("brain"), dict) else {}
    deck = cfg.get("deck") if isinstance(cfg.get("deck"), dict) else {}

    configured_role = ""
    if isinstance(onboarding, dict) and onboarding.get("role"):
        configured_role = normalize_role(onboarding.get("role"))
    elif brain_cfg.get("role"):
        configured_role = normalize_role(brain_cfg.get("role"))

    moved: dict[str, Any] = {}
    moved_path = base / "daemon" / "moved.json"
    if moved_path.exists():
        try:
            loaded = json.loads(moved_path.read_text(encoding="utf-8"))
            moved = loaded if isinstance(loaded, dict) else {"unreadable": True}
        except (OSError, ValueError):
            moved = {"unreadable": True}

    store = LeaseStore(base / "brain" / "lease.json")
    try:
        lease = store.read()
        lease_state = lease.state() if lease is not None else "free"
        lease_owner = lease.owner_id if lease is not None else ""
        lease_host = lease.hostname if lease is not None else ""
        lease_epoch = lease.epoch if lease is not None else 0
    except Exception as exc:  # noqa: BLE001 — report it, never crash a status view
        lease_state = f"unreadable: {exc}"
        lease_owner = lease_host = ""
        lease_epoch = 0

    profile = str(cfg.get("install", {}).get("profile") or "") if isinstance(
        cfg.get("install"), dict
    ) else ""

    return {
        "config_dir": str(base),
        "has_config": config_path.exists(),
        "role": configured_role or brain_settings()["role"],
        "role_configured": bool(configured_role),
        "profile": profile,
        "lease": {
            "state": lease_state,
            "owner_id": lease_owner,
            "hostname": lease_host,
            "epoch": lease_epoch,
            "path": str(store.path),
        },
        "moved": moved,
        "deck_key_fingerprint": installation_fingerprint(str(deck.get("api_key") or "")),
        "this_owner": default_owner_id(),
        "always_on_host": host_is_always_on(),
    }


def host_dependency_warning(role: str, *, always_on: bool | None = None) -> str | None:
    """Warn when a Brain would live on a machine that can be switched off.

    Returns ``None`` when there is nothing to warn about. This is a warning, never a
    refusal: plenty of people run a Brain on a desktop on purpose.
    """
    normalized = normalize_role(role)
    if normalized not in (ROLE_BRAIN, ROLE_ALL_IN_ONE):
        return None
    always = host_is_always_on() if always_on is None else always_on
    if always:
        return None
    if normalized == ROLE_BRAIN:
        return (
            "This host was chosen as an always-on Brain, but it looks like a workstation "
            "(no system-service config dir). If it can be powered off, every client and "
            "the bot stop with it — move the Brain to an always-on host, or accept that."
        )
    return (
        "This install runs the Brain on a workstation. Everything works while it is on; "
        "when it is off, the bot, scheduled jobs and every other device go quiet. That is "
        "this choice working as designed — if you need it always available, set up an "
        "always-on Brain."
    )
