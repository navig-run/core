"""Unified Capability Index — one descriptor per logical capability, folded
from the Store's existing items.

Answers "what can NAVIG do, how is it invoked, what does it need, is it
healthy" **without a second registry**: every backend is a
:class:`navig.hub.aggregator.StoreItem` (plugins, modules, connectors, MCP
servers, skills — the same rows `navig store list` and `GET /api/deck/store`
serve), and a *capability* is the deterministic fold of the items that share
one normalized key. Perplexity is the reference case: ``module:perplexity``
(the desktop app tile), ``plugin:navig-perplexity`` (the CDP engine),
``connector:perplexity`` (the Sonar API) and the plugin's skills fold into a
single ``perplexity`` capability with four backends.

Design rules, each enforced by tests in ``core/tests/hub/test_capabilities.py``:

- **Adapter, not registry.** No state, no registration, no cache — a pure fold
  over ``collect_store()``. Invalidation is therefore trivial: the next call
  reflects whatever the store reflects, including a fresh install or a disable.
- **Deterministic.** Sorted keys, sorted backends; two calls over the same
  store return equal payloads.
- **No untrusted code.** Metadata comes from manifests, config and the
  generated command map. The MCP tool probe executes only first-party core
  bundle ``register()`` functions against a throwaway object (the same seam
  ``navig mcp tools`` uses); third-party plugin code is never imported to
  describe it.
- **No secrets.** Auth is reported as mode + field NAMES (``oauth``,
  ``api_key``, ``["api_key"]``) — never values, tokens or vault contents.
- **Partial beats none.** A reader that fails (broken manifest, missing
  dependency) costs one backend, not the index; failures land in ``errors``.

Consumers: ``navig store capabilities`` (CLI), ``GET /api/deck/store?capabilities=1``
(deck/OS), both sharing this one implementation.
"""

from __future__ import annotations

import functools
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from navig.hub.aggregator import StoreItem, WireState

logger = logging.getLogger("navig.hub.capabilities")

__all__ = [
    "BackendRef",
    "CapabilityDescriptor",
    "collect_capabilities",
    "capability_key",
]

# Precedence for choosing a capability's display name / description / type:
# the user-facing tile first, then the service, then the package.
_LABEL_PRIORITY = ("module", "connector", "plugin", "mcp", "skill", "webapp", "app")

#: Backend kinds whose work runs against a remote service (vs. on this machine).
#: MCP servers are deliberately NOT listed: a server's transport (stdio vs http)
#: is not in the store item, and guessing would be a lie.
_REMOTE_KINDS = frozenset({"connector"})

_NOISE_PREFIXES = ("navig-", "navig_")


def capability_key(item: StoreItem) -> str:
    """The stable capability id an item groups under.

    Provider-owned items (a skill or MCP server that ships inside a plugin)
    group under their provider; everything else under its own name. The name
    is lowercased, a ``navig-`` vendor prefix is stripped, and everything
    non-alphanumeric is removed — so ``plugin:navig-perplexity``,
    ``module:perplexity`` and ``connector:perplexity`` all fold into
    ``perplexity``, while a local skill ``python-cooking`` stays
    ``pythoncooking``.
    """
    raw = item.provider or item.id.partition(":")[2]
    raw = str(raw).lower()
    for prefix in _NOISE_PREFIXES:
        if raw.startswith(prefix):
            raw = raw[len(prefix) :]
            break
    return re.sub(r"[^a-z0-9]+", "", raw)


@dataclass
class BackendRef:
    """One implementation of a capability — a Store item, plus derived facts."""

    source_id: str  # store item id, e.g. "connector:perplexity"
    kind: str  # plugin | module | connector | mcp | skill | webapp | app
    label: str
    state: str  # wired | unwired | available | broken (store vocabulary)
    degraded: bool = False
    origin: str = ""  # plugin/provider id when known, else the store's source
    #: "local" | "remote" | None (None = not derivable from the store item).
    execution: str | None = None
    #: Names only. {"mode": "oauth"|"api_key"|"manual_credentials", …}.
    auth: dict[str, Any] | None = None
    #: {"read": bool, "write": bool} where derivable (connector capability flags).
    permissions: dict[str, bool] | None = None
    #: One `navig …` line that configures/enables this backend.
    config_hint: str | None = None
    #: CLI verbs this backend contributes (plugin entry points, module surfaces).
    verbs: list[str] = field(default_factory=list)
    #: Backend-specific extras (format, path, status…). Names only, no secrets.
    detail: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "source_id": self.source_id,
            "kind": self.kind,
            "label": self.label,
            "state": self.state,
            "degraded": self.degraded,
            "origin": self.origin,
            "execution": self.execution,
            "auth": self.auth,
            "permissions": self.permissions,
            "config_hint": self.config_hint,
        }
        if self.verbs:
            out["verbs"] = sorted(self.verbs)
        if self.detail:
            out["detail"] = self.detail
        return out


@dataclass
class CapabilityDescriptor:
    """One logical capability and every implementation the Store knows."""

    id: str
    display_name: str
    description: str
    capability_type: str  # connector domain | module category | primary kind
    backends: list[BackendRef]
    #: Rollup across backends (store vocabulary); see _rollup().
    state: str = WireState.UNWIRED.value
    degraded: bool = False
    #: Owning packages/providers (plugin ids), sorted.
    origins: list[str] = field(default_factory=list)
    #: Full CLI paths from the generated command manifest, e.g.
    #: ``["navig perplexity ask", "navig perplexity backend", …]``.
    cli_commands: list[str] = field(default_factory=list)
    #: MCP tool names this capability exposes (derived, never guessed).
    mcp_tools: list[str] = field(default_factory=list)
    #: Store skill item ids grouped under this capability.
    skills: list[str] = field(default_factory=list)
    #: "local" | "remote" | "mixed" — derived from backend execution.
    execution: str = "local"
    #: Per-backend health problems (broken backends), kept visible even when
    #: another backend keeps the capability wired.
    health: list[dict[str, str]] = field(default_factory=list)
    #: Same-kind collisions (two plugins normalizing to one key) — a signal to
    #: inspect, never an automatic merge or delete.
    overlaps: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "description": self.description,
            "capability_type": self.capability_type,
            "state": self.state,
            "degraded": self.degraded,
            "execution": self.execution,
            "origins": sorted(self.origins),
            "backends": [b.to_dict() for b in self.backends],
            "cli_commands": list(self.cli_commands),
            "mcp_tools": list(self.mcp_tools),
            "skills": list(self.skills),
            "health": list(self.health),
            "overlaps": list(self.overlaps),
        }


# ── backend construction ─────────────────────────────────────────────────────


def _backend_from_item(item: StoreItem, errors: list[dict[str, str]]) -> BackendRef | None:
    """Derive a BackendRef from one Store item. Never raises."""
    try:
        kind = item.kind
        detail = dict(item.detail or {})
        # Origin = the owning package/provider, when one exists. The Store's
        # `source` field means different things per kind (connector: domain;
        # module: builtin|plugin), so only an explicit provider or a plugin's
        # own id counts as ownership — a domain is a classification, not an owner.
        origin = str(item.provider or "")
        if not origin and kind == "plugin":
            origin = item.id.partition(":")[2]
        if kind == "connector" and item.source:
            detail.setdefault("domain", item.source)
        verbs: list[str] = []
        auth: dict[str, Any] | None = None
        permissions: dict[str, bool] | None = None
        execution: str | None = None
        config_hint: str | None = None

        if kind == "connector":
            execution = "remote"
            auth_mode = str(detail.get("auth_mode", "") or "")
            requires_oauth = bool(detail.get("requires_oauth", False))
            key_fields = list(detail.get("key_fields", []) or [])
            can_search = detail.get("can_search")
            can_fetch = detail.get("can_fetch")
            can_act = detail.get("can_act")
            if auth_mode or key_fields or requires_oauth:
                auth = {
                    "mode": auth_mode or ("oauth" if requires_oauth else ""),
                    "requires_oauth": requires_oauth,
                    "key_fields": [str(k) for k in key_fields],  # NAMES, never values
                }
            if can_search is not None or can_act is not None:
                permissions = {
                    "read": bool(can_search or can_fetch),
                    "write": bool(can_act),
                }
            cid = item.id.partition(":")[2]
            config_hint = f"navig connector connect {cid}"
            # No CLI verb: connector commands live under the shared
            # `navig connector <action> <id>` tree (already the config_hint),
            # and mapping the bare verb "connector" would attach every
            # connector command in the manifest to every connector capability.
        elif kind == "plugin":
            execution = "local"
            verbs = [str(v) for v in (detail.get("commands") or [])]
            config_hint = f"navig store enable {item.id}"
        elif kind == "module":
            execution = "local"
            for surface in detail.get("surfaces") or []:
                surface = str(surface)
                if surface.startswith("cli:"):
                    verbs.append(surface.split(":", 1)[1])
            config_hint = f"navig store enable {item.id}"
        elif kind == "skill":
            execution = "local"
        elif kind == "mcp":
            # Transport (stdio vs http) is not in the item — leave execution
            # unset rather than guess local-vs-remote.
            config_hint = "config: mcp.servers / mcp.clients (navig mcp)"
        elif kind in ("webapp", "app"):
            execution = "remote"
            config_hint = "navig store open " + item.id

        return BackendRef(
            source_id=item.id,
            kind=kind,
            label=item.label or item.id,
            state=item.state,
            degraded=item.degraded,
            origin=origin,
            execution=execution,
            auth=auth,
            permissions=permissions,
            config_hint=config_hint,
            verbs=verbs,
            detail=detail,
        )
    except Exception as exc:  # noqa: BLE001 — one bad item must not kill the fold
        errors.append({"source": item.id, "error": f"{type(exc).__name__}: {exc}"})
        return None


def _rollup(backends: list[BackendRef]) -> tuple[str, bool]:
    """Best-available state across backends + a degraded/health flag.

    wired > unwired > available > broken: a capability whose desktop backend
    is up stays usable even when its API backend is broken — but the broken
    backend is reported in ``health``, never swallowed.
    """
    states = {b.state for b in backends}
    if WireState.WIRED.value in states:
        state = WireState.WIRED.value
    elif WireState.UNWIRED.value in states:
        state = WireState.UNWIRED.value
    elif WireState.AVAILABLE.value in states:
        state = WireState.AVAILABLE.value
    else:
        state = WireState.BROKEN.value
    degraded = any(b.degraded or b.state == WireState.BROKEN.value for b in backends)
    return state, degraded


def _execution_rollup(backends: list[BackendRef]) -> str:
    known = {b.execution for b in backends if b.execution}
    if not known:
        return "local"
    if known == {"remote"}:
        return "remote"
    if known == {"local"}:
        return "local"
    return "mixed"


def _overlaps(backends: list[BackendRef]) -> list[dict[str, Any]]:
    """Same-kind groups with more than one backend (duplicate registrations)."""
    by_kind: dict[str, list[str]] = {}
    for b in backends:
        by_kind.setdefault(b.kind, []).append(b.source_id)
    return [
        {"kind": kind, "sources": sorted(sources)}
        for kind, sources in sorted(by_kind.items())
        if len(sources) > 1
    ]


# ── derived references (CLI manifest, MCP tools) ─────────────────────────────


@functools.lru_cache(maxsize=1)
def _command_paths() -> tuple[str, ...]:
    """Every command path in the generated manifest, loaded once per process."""
    try:
        from navig.cli.registry import _load_generated_manifest

        manifest = _load_generated_manifest()
        if not manifest:
            return ()
        return tuple(
            str(c.get("path") or "")
            for c in manifest.get("commands", [])
            if c.get("path")
        )
    except Exception as exc:  # noqa: BLE001 — a bad manifest costs command refs only
        logger.debug("command manifest unavailable for capability index: %s", exc)
        return ()


def _cli_commands_for(verbs: list[str]) -> list[str]:
    """Manifest paths under the given top-level verbs (``navig perplexity *``)."""
    if not verbs:
        return []
    prefixes = tuple(f"navig {v} " for v in sorted(set(verbs)))
    exact = {f"navig {v}" for v in set(verbs)}
    return sorted(p for p in _command_paths() if p in exact or p.startswith(prefixes))


@functools.lru_cache(maxsize=1)
def _mcp_probe() -> Any | None:
    """A throwaway server object, or None when the MCP tool platform is absent.

    Importing ``navig.mcp.tools`` costs ~0.9 s the first time in a process
    (its package __init__ imports every bundle); the result is cached for the
    process, so the cost is paid once per CLI invocation at most.
    """

    class _Probe:
        def __init__(self) -> None:
            self.tools: dict = {}
            self._tool_handlers: dict = {}
            self._tool_safety: dict = {}

    try:
        from navig.mcp.tools import register_all_tools

        probe = _Probe()
        register_all_tools(probe)
        return probe
    except Exception as exc:  # noqa: BLE001 — index without MCP refs, never crash
        logger.debug("MCP tool probe unavailable for capability index: %s", exc)
        return None


def _mcp_tools_for(backends: list[BackendRef]) -> list[str]:
    """Connector-derived MCP tool names — one per ENABLED capability flag,
    mirroring the exact naming rule ``list_connector_tools`` uses
    (``connector_<id>_<op>``). Bundle tools (``perplexity_ask``…) are added
    by the probe pass in :func:`collect_capabilities`."""
    tools: set[str] = set()
    for b in backends:
        if b.kind != "connector":
            continue
        cid = b.source_id.partition(":")[2]
        detail = b.detail
        if detail.get("can_search", True):
            tools.add(f"connector_{cid}_search")
        if detail.get("can_fetch", True):
            tools.add(f"connector_{cid}_fetch")
        if detail.get("can_act", True):
            tools.add(f"connector_{cid}_act")
    return sorted(tools)


# ── the fold ─────────────────────────────────────────────────────────────────


def collect_capabilities(
    items: list[StoreItem] | None = None,
    *,
    mcp_probe: bool = True,
) -> tuple[list[CapabilityDescriptor], list[dict[str, str]]]:
    """Fold Store items into capability descriptors.

    Args:
        items: Pre-collected store items (a caller that already swept the
            store passes them so no second sweep runs). ``None`` collects.
        mcp_probe: Derive MCP tool names (imports ``navig.mcp.tools`` once
            per process, ~0.9 s the first time). False skips that surface.

    Returns:
        ``(descriptors, errors)`` — descriptors sorted by id; errors record
        any backend that could not be built (a broken manifest, a reader
        failure) so a partial index reports honestly instead of silently
        dropping a row.
    """
    from navig.hub.aggregator import collect_store as _collect

    if items is None:
        items = _collect(include_available=True)

    errors: list[dict[str, str]] = []
    grouped: dict[str, list[StoreItem]] = {}
    for item in items:
        try:
            grouped.setdefault(capability_key(item), []).append(item)
        except Exception as exc:  # noqa: BLE001
            errors.append({"source": item.id, "error": f"{type(exc).__name__}: {exc}"})

    out: list[CapabilityDescriptor] = []
    for cap_id in sorted(grouped):
        group = grouped[cap_id]
        # Skills are ASSOCIATIONS, not implementations (the mission's
        # descriptor model lists them as their own field). They join the
        # `skills` list; only non-skill items become backends. A group of
        # ONLY skills (a standalone skill with no owning plugin) still gets
        # a descriptor — the skill is the capability.
        skill_items = [i for i in group if i.kind == "skill"]
        impl_items = [i for i in group if i.kind != "skill"]
        backends: list[BackendRef] = []
        for item in sorted(impl_items, key=lambda i: (_LABEL_PRIORITY.index(i.kind) if i.kind in _LABEL_PRIORITY else 99, i.id)):
            ref = _backend_from_item(item, errors)
            if ref is not None:
                backends.append(ref)
        if not backends and not skill_items:
            continue
        if not backends:
            # Standalone-skill capability: one synthetic backend so state
            # rollup and execution still describe something real.
            s0 = skill_items[0]
            backends = [
                BackendRef(
                    source_id=s0.id,
                    kind="skill",
                    label=s0.label or s0.id,
                    state=s0.state,
                    origin=str(s0.provider or ""),
                    execution="local",
                )
            ]

        # Display name / description: first non-empty by kind precedence.
        display_name = next((b.label for b in backends if b.label), cap_id)
        description = next(
            (i.description for i in group if i.description),
            "",
        )

        # Type: connector domain, else module category, else primary kind.
        capability_type = next(
            (str(b.detail.get("domain")) for b in backends if b.kind == "connector" and b.detail.get("domain")),
            next(
                (str(b.detail.get("category")) for b in backends if b.kind == "module" and b.detail.get("category")),
                backends[0].kind,
            ),
        )

        state, degraded = _rollup(backends)
        health = [
            {"backend": b.source_id, "error": str(b.detail.get("error") or b.state)}
            for b in backends
            if b.state == WireState.BROKEN.value
        ]
        origins = {b.origin for b in backends if b.origin}
        verbs = sorted({v for b in backends for v in b.verbs})
        skills = sorted(i.id for i in group if i.kind == "skill")

        # Connector-derived MCP tools need no probe (manifest flags only);
        # bundle tools (perplexity_ask…) are added by the probe pass below.
        out.append(
            CapabilityDescriptor(
                id=cap_id,
                display_name=display_name,
                description=description,
                capability_type=capability_type,
                backends=backends,
                state=state,
                degraded=degraded,
                origins=sorted(origins),
                cli_commands=_cli_commands_for(verbs),
                mcp_tools=_mcp_tools_for(backends),
                skills=skills,
                execution=_execution_rollup(backends),
                health=health,
                overlaps=_overlaps(backends),
            )
        )

    # MCP bundle tools need the full probe pass once, after grouping: a
    # capability whose only backend is a module (e.g. "memory") still owns
    # the navig_memory_* MCP tools registered by the core bundle of that name.
    if mcp_probe:
        probe = _mcp_probe()
        if probe is not None:
            tools = getattr(probe, "tools", {}) or {}
            for cap in out:
                prefix = f"{cap.id}_"
                extra = sorted(n for n in tools if str(n).startswith(prefix) and str(n) not in cap.mcp_tools)
                if extra:
                    cap.mcp_tools = sorted(set(cap.mcp_tools) | set(extra))

    return out, errors
