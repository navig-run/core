"""Which machine owns a capability, and where a tool call for it must run.

NAVIG has one Brain and many clients. Almost every MCP tool is a *local* capability: it
imports this machine's own files, windows, browser, dock, music player or repository, and
asking a Brain 2000 km away to read them would be nonsense. A handful are not — memory key
facts and the recall bank belong to the Brain, and answering them from a client's own
database is the defect this module ends.

So each tool family carries a contract, and a call is routed by it:

``data_authority``
    ``brain`` — the Brain owns the rows; a linked client must not read or write a local
    copy. ``local`` — this machine owns them.
``execution_target``
    ``local`` — the work happens here (files, screen, builds). ``brain`` — it happens on
    the Brain. ``either`` — it is a pure computation the caller may run anywhere.
``offline_policy``
    ``fail`` — with the Brain unreachable, return an error. ``degrade`` — serve what this
    machine can serve and say so. ``local`` — never leaves this machine, so being offline
    changes nothing.
``risk``
    The cost of getting the route wrong: ``high`` means a wrong route silently splits the
    operator's data.

Only the families where the answer is *contested* are listed. Everything else is local by
declaration, and :func:`capability_for` says so explicitly rather than by omission — an
unlisted tool is a local tool, and a test pins the list so a new Brain-owned family cannot
appear without a contract.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from navig.brain import link as _link
from navig.brain.client import BrainClient, BrainUnavailable, client_for_state
from navig.brain.lease import ROLE_ALL_IN_ONE, ROLE_CLIENT, ROLE_EXECUTOR

__all__ = [
    "AUTHORITY_BRAIN",
    "AUTHORITY_LOCAL",
    "Capability",
    "MODE_BRAIN",
    "MODE_LOCAL",
    "MODE_REFUSED",
    "OFFLINE_DEGRADE",
    "OFFLINE_FAIL",
    "OFFLINE_LOCAL",
    "ROUTE_ENV",
    "Route",
    "capability_for",
    "contracts",
    "failure_payload",
    "GRADE_LINKED",
    "GRADE_MISCONFIGURED",
    "GRADE_NO_LINK",
    "GRADE_UNREADABLE",
    "grade_link",
    "install_role",
    "reload",
    "resolve",
    "route_env_override",
]

logger = logging.getLogger("navig.mcp.capability")

AUTHORITY_BRAIN = "brain"
AUTHORITY_LOCAL = "local"

MODE_LOCAL = "local"
MODE_BRAIN = "brain"
MODE_REFUSED = "refused"

OFFLINE_FAIL = "fail"
OFFLINE_DEGRADE = "degrade"
OFFLINE_LOCAL = "local"

#: ``NAVIG_BRAIN_ROUTE=local|brain`` forces a route. It exists for installers, support and
#: tests; a production client should be routed by the link file, not by an env var, so
#: :func:`route_env_override` is the only reader and it logs whenever it fires.
ROUTE_ENV = "NAVIG_BRAIN_ROUTE"

#: How long a detected install role is trusted before it is read again. A role change is a
#: deliberate act (``navig brain role …``) followed by a restart, so a minute of staleness
#: is harmless; re-reading config on every tool call is not.
_ROLE_TTL_S = 60.0
_role_lock = threading.Lock()
_role_cache: tuple[float, str] | None = None


@dataclass(frozen=True)
class Capability:
    """One tool family's ownership contract."""

    family: str
    data_authority: str
    execution_target: str
    offline_policy: str
    risk: str
    reason: str = ""

    @property
    def brain_owned(self) -> bool:
        return self.data_authority == AUTHORITY_BRAIN

    def as_dict(self) -> dict[str, Any]:
        return {
            "family": self.family,
            "data_authority": self.data_authority,
            "execution_target": self.execution_target,
            "offline_policy": self.offline_policy,
            "risk": self.risk,
        }


def _local(family: str, reason: str, *, target: str = "local", risk: str = "low") -> Capability:
    return Capability(
        family=family,
        data_authority=AUTHORITY_LOCAL,
        execution_target=target,
        offline_policy=OFFLINE_LOCAL,
        risk=risk,
        reason=reason,
    )


#: Contested families first — these are why routing exists.
_FAMILIES: dict[str, Capability] = {
    # Key facts are the operator's long-term memory: one row, one owner, or two hosts
    # disagree forever. Brain-owned, write-side stricter than read-side (still ``fail``).
    "memory_key_facts": Capability(
        family="memory_key_facts",
        data_authority=AUTHORITY_BRAIN,
        execution_target="brain",
        offline_policy=OFFLINE_FAIL,
        risk="high",
        reason="the Brain's key-fact store is the only authoritative copy",
    ),
    # Recall: the BANK is canonical on the Brain, but ingestion reads this machine's own
    # session transcripts. Read = brain, ingest = brain-owned data produced from local
    # provenance. Provenance-preserving ingestion is Phase C; until it lands, a linked
    # client refuses rather than writing a second local bank.
    "recall": Capability(
        family="recall",
        data_authority=AUTHORITY_BRAIN,
        execution_target="brain",
        offline_policy=OFFLINE_FAIL,
        risk="high",
        reason="the recall bank is canonical on the Brain; clients keep provenance, not a copy",
    ),
    # Everything below is a genuinely local capability. Listed explicitly (not left to the
    # default) because "we thought about it and it is local" is a different claim from
    # "nobody has looked at it yet".
    "inventory": _local("inventory", "reads this machine's registered inventory"),
    "wiki": _local("wiki", "reads this machine's wiki and documentation tree"),
    "system": _local("system", "shells out on this machine by design"),
    "agent": _local(
        "agent",
        "this machine's agent sessions and goals — host work; goals move to the Brain in phase C",
        risk="moderate",
    ),
    "bay": _local("bay", "installs items into this machine's workspace"),
    "blocks": _local("blocks", "runs this machine's automation blocks"),
    # Missions and receipts are host work today. They are NOT declared Brain-owned because
    # the Brain data surface does not cover them yet, and a contract that claims a remote
    # authority the route cannot reach would be a lie the router then has to break. The
    # reason is recorded here so the gap is visible in `navig doctor` and in code review
    # rather than implied by an empty entry.
    "runtime": _local(
        "runtime",
        "this machine's missions and receipts — host work; moves to the Brain in phase C",
        risk="moderate",
    ),
    "filesystem": _local("filesystem", "this machine's disk"),
    "spaces": _local("spaces", "this machine's Spaces checkout"),
    "cdp": _local("cdp", "this machine's browser"),
    "dock": _local("dock", "this machine's browser dock"),
    "perplexity": _local("perplexity", "drives this machine's Perplexity app"),
    "music": _local("music", "this machine's audio"),
    "coordination": _local("coordination", "coordinates this machine's sessions"),
    "desktop": _local("desktop", "drives this machine's desktop windows"),
    "windows": _local("windows", "this machine's Windows shell integration"),
    "connectors": _local("connectors", "reads this machine's connector credentials"),
}

#: Outcome of grading a link against the detected role.
GRADE_NO_LINK = "no_link"
GRADE_LINKED = "linked"
GRADE_UNREADABLE = "unreadable"
GRADE_MISCONFIGURED = "misconfigured"


def contracts() -> dict[str, Capability]:
    """Every declared family contract, by name. Read-only copy for doctor and tests."""
    return dict(_FAMILIES)


def _family_of(tool: str) -> str:
    """``memory_key_facts_retrieve`` → ``memory_key_facts``; ``recall_search`` → ``recall``.

    Longest declared prefix wins, so ``memory`` and ``memory_key_facts`` could coexist.
    """
    best = ""
    for family in _FAMILIES:
        if tool == family or tool.startswith(family + "_"):
            if len(family) > len(best):
                best = family
    return best or tool.split("_", 1)[0]


def capability_for(tool: str) -> Capability:
    """This tool's contract. An unlisted family is local by declaration."""
    family = _family_of(tool)
    declared = _FAMILIES.get(family)
    if declared is not None:
        return declared
    return Capability(
        family=family,
        data_authority=AUTHORITY_LOCAL,
        execution_target="local",
        offline_policy=OFFLINE_LOCAL,
        risk="low",
        reason="no Brain-owned contract declares this family, so it is local",
    )


def install_role(*, use_cache: bool = True) -> str:
    """The role this install plays: ``brain`` / ``all-in-one`` / ``client`` / ``executor``.

    Read through :func:`navig.brain.roles.detect_install_context`, which is the single
    source of truth for the four user choices. Any failure answers ``all-in-one`` — the
    historical single-machine behaviour — because a config read error must not turn a
    working local install into a machine that refuses its own memory.
    """
    global _role_cache
    if use_cache:
        with _role_lock:
            if _role_cache is not None:
                stamp, role = _role_cache
                if (time.monotonic() - stamp) < _ROLE_TTL_S:
                    return role
    role = ROLE_ALL_IN_ONE
    try:
        from navig.brain.roles import detect_install_context

        role = str(detect_install_context().get("role") or ROLE_ALL_IN_ONE)
    except Exception:  # noqa: BLE001 — an unreadable config is not a routing decision
        logger.debug("capability: install role unreadable, assuming all-in-one", exc_info=True)
        role = ROLE_ALL_IN_ONE
    with _role_lock:
        _role_cache = (time.monotonic(), role)
    return role


def reload() -> None:
    """Forget the cached role and link. Tests and ``navig brain role`` use this."""
    global _role_cache
    with _role_lock:
        _role_cache = None


def route_env_override() -> str:
    """``local`` / ``brain`` from :data:`ROUTE_ENV`, or "" when unset or unrecognised."""
    import os

    raw = os.environ.get(ROUTE_ENV, "").strip().lower()
    if raw in (MODE_LOCAL, MODE_BRAIN):
        return raw
    if raw:
        logger.warning("capability: ignoring %s=%r (expected local|brain)", ROUTE_ENV, raw)
    return ""


def grade_link(state: _link.BrainLinkState | None = None, role: str | None = None) -> str:
    """Classify the install's routing situation without deciding a route.

    Kept separate from :func:`resolve` so doctor, the installer and tests can report the
    situation in words a human can act on, without a tool name in hand.
    """
    state = state or _link.read_brain_link_state()
    role = role or install_role()
    if state.is_unreadable:
        return GRADE_UNREADABLE
    if state.is_usable:
        return GRADE_LINKED
    if role in (ROLE_CLIENT, ROLE_EXECUTOR):
        # Says "I am a client" with no usable link: the Brain is somewhere, we cannot name
        # it. Writing locally here is how a replica is born.
        return GRADE_MISCONFIGURED
    return GRADE_NO_LINK


@dataclass(frozen=True)
class Route:
    """Where one call goes, and why. Returned to every tool that routes."""

    mode: str
    capability: Capability
    client: BrainClient | None = None
    role: str = ""
    link_state: _link.BrainLinkState | None = None
    refusal: dict[str, Any] = field(default_factory=dict)

    @property
    def is_local(self) -> bool:
        return self.mode == MODE_LOCAL

    @property
    def is_brain(self) -> bool:
        return self.mode == MODE_BRAIN

    @property
    def is_refused(self) -> bool:
        return self.mode == MODE_REFUSED

    def describe(self) -> str:
        if self.mode == MODE_BRAIN and self.link_state is not None:
            return f"brain:{self.capability.family} at {self.link_state.label}"
        if self.mode == MODE_REFUSED:
            return f"refused:{self.capability.family} ({self.refusal.get('reason', '')})"
        return f"local:{self.capability.family}"


def _refusal(
    capability: Capability,
    reason: str,
    message: str,
    *,
    role: str = "",
    state: _link.BrainLinkState | None = None,
    code: str = "",
) -> Route:
    payload: dict[str, Any] = {
        "error": message,
        "isError": True,
        "route": MODE_REFUSED,
        "reason": reason,
        "wrote": False,
        "capability": capability.as_dict(),
    }
    if code:
        payload["code"] = code
    if state is not None:
        payload["brain"] = state.label
        payload["link_state"] = state.state
    if role:
        payload["role"] = role
    return Route(
        mode=MODE_REFUSED,
        capability=capability,
        role=role,
        link_state=state,
        refusal=payload,
    )


def resolve(
    tool: str,
    *,
    state: _link.BrainLinkState | None = None,
    role: str | None = None,
) -> Route:
    """Decide where *tool* runs on this machine, now.

    The decision order is the whole safety argument, so it is linear and total:

    1. A local capability is local. No link, no network, no surprise.
    2. A Brain-owned capability on a *usable* link goes to the Brain.
    3. A Brain-owned capability with an *unreadable* link is refused: we cannot prove where
       the Brain is, and a local write may be a duplicate of the authority.
    4. A Brain-owned capability, no link, role says client/executor → refused (misconfigured):
       the install claims to be a client but names no Brain.
    5. Otherwise (Brain, all-in-one, or an install that never configured a role) the local
       store IS the authority, so it is served locally.
    """
    capability = capability_for(tool)
    role = role or install_role()
    if not capability.brain_owned:
        return Route(mode=MODE_LOCAL, capability=capability, role=role, link_state=state)

    override = route_env_override()
    state = state or _link.read_brain_link_state()
    if override == MODE_LOCAL:
        logger.warning("capability: %s forced local by %s", tool, ROUTE_ENV)
        return Route(mode=MODE_LOCAL, capability=capability, role=role, link_state=state)

    client = client_for_state(state)
    if client is not None:
        return Route(
            mode=MODE_BRAIN, capability=capability, client=client, role=role, link_state=state
        )

    if state.is_unreadable:
        return _refusal(
            capability,
            "link_unreadable",
            (
                f"{tool} is served by the Brain, but the brain link at "
                f"{state.path or _link.brain_link_path()} is unreadable "
                f"({state.detail or state.state}). Nothing was read or written locally: "
                "fix or remove the link file, then retry."
            ),
            role=role,
            state=state,
            code="link_unreadable",
        )

    if role in (ROLE_CLIENT, ROLE_EXECUTOR):
        return _refusal(
            capability,
            "no_brain_link",
            (
                f"This install is a {role} (brain.role), so {tool} belongs to its Brain — "
                "but there is no brain-link.json naming that Brain. Nothing was written "
                "locally. Link this machine with `navig os link`, or set the role to brain "
                "on the host that should own the data."
            ),
            role=role,
            state=state,
            code="no_brain_link",
        )

    return Route(mode=MODE_LOCAL, capability=capability, role=role, link_state=state)


def failure_payload(exc: BrainUnavailable, capability: Capability) -> dict[str, Any]:
    """The MCP error for a Brain call that did not happen."""
    payload = exc.as_payload()
    payload["capability"] = capability.as_dict()
    return payload
