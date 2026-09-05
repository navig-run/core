"""navig.mcp.trust — the trust boundary for externally-defined MCP tools.

**Nothing outside this module reads a tool's ``annotations``.**

An MCP server describes its own tools, and one of the things it may claim is
``readOnlyHint``. That claim decides whether NAVIG runs the tool immediately or holds it
for the operator, so it is the single point where a remote party's self-description
becomes a local policy decision. Concentrating it here is what makes the decision
auditable: :attr:`ClassifiedTool.classified_by` records *whose word* the classification
rested on, so a later audit can find every call taken on a server's say-so.

Two trust tiers, decided by the deployment rather than by the server describing itself:

``vetted``
    An operator asserted this endpoint's annotations are reliable; they may drive
    auto-approval.
``byo``
    Someone pasted a URL. ``readOnlyHint`` still classifies reads; nothing the server
    says can auto-apply a write.

Honouring ``readOnlyHint`` on ``byo`` is a knowing tradeoff, not a free win: a tool the
server mislabels runs with no approval where an unlabelled one would have been held.
Auto-*applying* a write is not accepted on the same terms and additionally requires a
vetted endpoint, so the operator rather than the server casts the deciding vote.

**Every annotation test is ``is True`` / ``is False``, never truthiness.** Python makes
this sharper than the TypeScript original it is ported from: ``annotations.get("x")``
returning ``1`` or ``"true"`` is truthy, and both must fail. A server that did not follow
the spec is a server that did not annotate, and the safe reading of "did not annotate" is
"this is an action". Note in particular that :func:`navig.core.coerce.coerce_bool` is
**deliberately not used here** — it exists for the ``navig config set`` raw-string trap
and accepts ``"true"``/``"on"``/``"yes"``, which is right for the *operator's* config and
wrong for adversarial input.

Ported from Cloudflare OS, ``packages/mcp-shared/src/{tools,scope}.ts``.
"""

from __future__ import annotations

import enum
import hashlib
import re
from dataclasses import dataclass
from typing import Any

from loguru import logger

__all__ = [
    "ServerTrust",
    "ClassificationSource",
    "ToolMode",
    "ClassifiedTool",
    "classify_tool",
    "catalog_revision",
    "namespaced_tool_name",
    "trust_for_server",
    "honor_read_only_hint",
    "allowed_tools_for_server",
    "auto_approve_tools_for_server",
    "tool_is_in_scope",
    "MAX_TOOLS_PER_SERVER",
    "TOOL_NAME_PREFIX",
]

#: Upper bound on tools taken from one endpoint, to keep generated schemas bounded.
MAX_TOOLS_PER_SERVER = 200

#: Prefix marking a registry name as externally defined. The approval gate keys its
#: default-deny shape rule on this, so it is load-bearing rather than cosmetic.
#: Matches Claude Code's own convention, so operators recognise it on sight.
TOOL_NAME_PREFIX = "mcp__"

#: OpenAI function names must match ``^[a-zA-Z0-9_-]{1,64}$``.
_MAX_TOOL_NAME = 64
_INVALID_NAME_CHARS = re.compile(r"[^a-zA-Z0-9-]+")


class ServerTrust(str, enum.Enum):
    """How far an endpoint's self-description is trusted."""

    VETTED = "vetted"
    BYO = "byo"


class ClassificationSource(str, enum.Enum):
    """Which side decided a tool's read/action classification."""

    SERVER_ANNOTATION = "server-annotation"
    DEFAULT = "default"


class ToolMode(str, enum.Enum):
    """``read`` runs immediately; ``action`` is held for the operator."""

    READ = "read"
    ACTION = "action"


@dataclass(frozen=True)
class ClassifiedTool:
    """A tool plus the decisions this module has made about it."""

    wire_name: str
    """Exactly as the server spells it — what ``tools/call`` sends."""

    registry_name: str
    """``mcp__<server>__<tool>`` — what the model sees and the gate reads."""

    server: str
    mode: ToolMode
    auto_approvable: bool
    """Whether the deployment may let this action through without a prompt."""

    classified_by: ClassificationSource
    """Whose word :attr:`mode` rests on. Recorded rather than re-derived, so no
    consumer can answer it differently from the classifier that decided it."""

    description: str = ""


def _claims(annotations: dict[str, Any]) -> tuple[bool, bool, bool]:
    """The three annotations that feed a policy decision, as strict tri-state reads."""
    return (
        annotations.get("readOnlyHint") is True,
        annotations.get("destructiveHint") is False,
        annotations.get("idempotentHint") is True,
    )


def classify_tool(
    name: str,
    *,
    server: str,
    annotations: dict[str, Any] | None = None,
    trust: ServerTrust = ServerTrust.BYO,
    description: str = "",
    honor_read_only: bool = True,
) -> ClassifiedTool:
    """The single place a server's self-description becomes a policy decision.

    Every test is ``is True`` / ``is False``, so an unannotated tool fails all of them
    and comes out as an action that can never auto-apply.

    Args:
        name: The tool name exactly as the server spells it.
        server: The server/client id the tool came from.
        annotations: The server's ``annotations`` object, if any.
        trust: The deployment's trust tier for this endpoint.
        description: The server's description, carried for the approval prompt.
        honor_read_only: When False, ``readOnlyHint`` is ignored entirely and every
            tool is an action — the paranoid setting, for an operator who does not
            want a remote party deciding what counts as a read.
    """
    ann = annotations or {}
    read_only, not_destructive, idempotent = _claims(ann)
    read_only = read_only and honor_read_only

    auto_approvable = (
        not read_only
        and trust is ServerTrust.VETTED
        and not_destructive
        and idempotent
    )

    return ClassifiedTool(
        wire_name=name,
        registry_name=namespaced_tool_name(server, name),
        server=server,
        mode=ToolMode.READ if read_only else ToolMode.ACTION,
        auto_approvable=auto_approvable,
        classified_by=(
            ClassificationSource.SERVER_ANNOTATION
            if read_only
            else ClassificationSource.DEFAULT
        ),
        description=description,
    )


# ---------------------------------------------------------------------------
# Naming
# ---------------------------------------------------------------------------


def _sanitize_segment(raw: str) -> str:
    """Collapse anything outside the OpenAI charset to ``_``."""
    return _INVALID_NAME_CHARS.sub("_", raw).strip("_") or "x"


def namespaced_tool_name(server: str, wire_name: str) -> str:
    """Return the registry key for an externally-defined tool.

    Namespacing is what lets the approval gate tell "externally defined" from "one of
    ours" using the name alone — the gate holds only a string, so provenance has to
    travel in it. It also stops a server publishing ``bash_exec`` from shadowing the
    real one in the agent registry.

    Truncation keeps a deterministic hash suffix so two long names that share a prefix
    cannot collapse onto one key.
    """
    base = f"{TOOL_NAME_PREFIX}{_sanitize_segment(server)}__{_sanitize_segment(wire_name)}"
    if len(base) <= _MAX_TOOL_NAME:
        return base
    digest = hashlib.sha256(f"{server}\0{wire_name}".encode()).hexdigest()[:8]
    return f"{base[: _MAX_TOOL_NAME - 9]}_{digest}"


def catalog_revision(tools: list[dict[str, Any]]) -> str:
    """Stable fingerprint of a tool catalog, for detecting a server changing under us.

    Covers each tool's name and every claim a grant was decided against. Descriptions
    are excluded so a copy edit does not fire the signal. Claims are recorded
    tri-state, so a server starting or stopping making a claim is visible even where
    both readings lead to the same decision today.
    """

    def claim_char(value: Any) -> str:
        return "1" if value is True else "0" if value is False else "-"

    canonical = "\x01".join(
        sorted(
            "{}\x00{}{}{}".format(
                tool.get("name", ""),
                "r" if (tool.get("annotations") or {}).get("readOnlyHint") is True else "w",
                claim_char((tool.get("annotations") or {}).get("destructiveHint")),
                claim_char((tool.get("annotations") or {}).get("idempotentHint")),
            )
            for tool in tools
        )
    )
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Scope — which of a server's tools this deployment will use at all
# ---------------------------------------------------------------------------
#
# `trust` says *how far the server's own claims are believed*; scope says *which tools
# it may offer in the first place*. They are independent, and both are needed: a
# `vetted` server's declared reads run unprompted, so a tool it adds tomorrow is
# unprompted the day it appears. An allowlist is the containment for that.
#
# (The reference encodes this grant in a resource-URL fragment. That fits a system whose
# capabilities *are* URLs; NAVIG addresses servers by config key, so the grant lives
# beside the rest of the server's settings instead of being smuggled into a URL that
# stdio servers do not even have.)


def tool_is_in_scope(tool_name: str, allowed: tuple[str, ...] | None) -> bool:
    """Whether *tool_name* is within a server's configured scope.

    ``None`` means no restriction was configured — every tool, now and later. An
    **empty** tuple is a configured restriction that happens to name nothing, and denies
    everything.

    That distinction is the whole point and it is deliberately fail-closed: collapsing
    "configured but unusable" into "not configured" would turn a typo in the allowlist
    into full access, which is a worse bug than the one scoping exists to prevent. Same
    rule the deck allowlist learned (`allowed_users_configured`).
    """
    return allowed is None or tool_name in allowed


# ---------------------------------------------------------------------------
# Deployment configuration
# ---------------------------------------------------------------------------


def _mcp_trust_config() -> dict[str, Any]:
    try:
        from navig.config import get_config_manager

        section = get_config_manager().get("mcp.trust", {})
    except Exception as exc:  # noqa: BLE001 — config unreadable must not open the gate
        logger.debug("mcp.trust config unreadable ({}); defaulting to byo", exc)
        return {}
    return section if isinstance(section, dict) else {}


def _server_entry(server_id: str) -> Any:
    """The raw ``mcp.trust.servers.<id>`` value, if any.

    Accepts two spellings, because the short one is what an operator types and the long
    one is what they need once they want scoping as well::

        mcp.trust.servers.acme: vetted
        mcp.trust.servers.acme: {tier: vetted, tools: [list_issues, search]}
    """
    servers = _mcp_trust_config().get("servers")
    return servers.get(server_id) if isinstance(servers, dict) else None


def trust_for_server(server_id: str) -> ServerTrust:
    """The deployment's trust tier for *server_id*.

    Deliberately a **validated string, not a boolean** — that is how it sidesteps the
    ``bool("false")`` trap that :func:`navig.core.coerce.coerce_bool` exists for: by
    not being a boolean at all. An unrecognised value falls back to ``byo``, following
    the same rule ``coerce_bool`` applies to an unknown token — ambiguity never
    silently resolves to the more permissive reading.
    """
    raw = _server_entry(server_id)
    if isinstance(raw, dict):
        raw = raw.get("tier")
    if raw is None:
        raw = _mcp_trust_config().get("default")

    if isinstance(raw, str):
        try:
            return ServerTrust(raw.strip().lower())
        except ValueError:
            logger.warning(
                "mcp.trust: unrecognised trust tier {!r} for server {!r} — using 'byo'. "
                "Valid values: vetted, byo.",
                raw,
                server_id,
            )
    return ServerTrust.BYO


def allowed_tools_for_server(server_id: str) -> tuple[str, ...] | None:
    """The tools *server_id* may offer, or ``None`` when unrestricted.

    Reads ``mcp.trust.servers.<id>.tools``. Returning an **empty tuple** for a
    configured-but-unusable value is deliberate — see :func:`tool_is_in_scope`. A single
    string is accepted as a one-element list, because ``navig config set`` cannot
    produce a YAML list and an operator restricting a server to one tool is the common
    case.
    """
    return _tool_name_list(server_id, "tools")


def auto_approve_tools_for_server(server_id: str) -> tuple[str, ...]:
    """Tools of *server_id* the operator has pre-authorised to run without a prompt.

    This is the **second** of two independent gates, and it only ever narrows: a tool
    runs unprompted when the classifier says the action is auto-approvable
    (:attr:`ClassifiedTool.auto_approvable` — a *vetted* endpoint declaring the tool
    non-destructive AND idempotent) **and** the operator named it here.

    Two gates rather than one, because either alone is the wrong authority. The server
    deciding its own writes are safe is the server marking its own homework; the
    operator pre-approving a tool whose server never claimed it was idempotent is a
    blank cheque written against a description they cannot see change. Requiring both
    means an unprompted write needs the server to have made a specific claim *and* a
    human to have accepted that claim for that specific tool.

    Returns an empty tuple when nothing is pre-authorised — the safe default, and the
    reason this cannot be a boolean: there is no "auto-approve everything" spelling.
    """
    return _tool_name_list(server_id, "auto_approve") or ()


def _tool_name_list(server_id: str, key: str) -> tuple[str, ...] | None:
    """Read a list-of-tool-names setting from a server's entry.

    ``None`` when the key is absent; an **empty tuple** when it is present but names
    nothing usable — the caller decides what each means, and for a *restriction* those
    are very different things (see :func:`tool_is_in_scope`).
    """
    entry = _server_entry(server_id)
    if not isinstance(entry, dict) or key not in entry:
        return None

    raw = entry.get(key)
    if isinstance(raw, str):
        # `navig config set … tools "a,b"` — the only shape the CLI can write.
        return tuple(part.strip() for part in raw.split(",") if part.strip())
    if isinstance(raw, (list, tuple)):
        return tuple(str(item).strip() for item in raw if str(item).strip())

    # Unusable either way, and fail-closed either way — but for different reasons, so
    # the operator is told which one they are looking at.
    consequence = (
        "denying every tool from this server. Remove the key to allow all of them."
        if key == "tools"
        else "pre-authorising nothing. Every tool from this server will still ask."
    )
    logger.warning(
        "mcp.trust.servers.{}.{} is {!r}, which names no tools — {}",
        server_id,
        key,
        raw,
        consequence,
    )
    return ()


def honor_read_only_hint() -> bool:
    """Whether a server's ``readOnlyHint`` may classify a tool as a read.

    The one genuine boolean in this section, so it goes through ``coerce_bool`` —
    ``navig config set`` stores raw strings and a bare ``if value:`` would read
    ``"false"`` as True.
    """
    from navig.core.coerce import coerce_bool

    return coerce_bool(_mcp_trust_config().get("honor_read_only_hint"), default=True)
