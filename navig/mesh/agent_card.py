"""
A2A Agent Card — expose a NAVIG node as a standard discoverable agent.

This is the minimal, spec-shaped bridge between NAVIG's LAN Flux mesh and the
open **A2A (Agent2Agent)** convention: an *Agent Card* is a JSON descriptor a
client fetches to learn "who is this agent and what can it do". A2A serves it at
``/.well-known/agent-card.json`` (legacy drafts used ``/.well-known/agent.json``).

Design notes / self-asked questions:
  Q: Why not invent our own descriptor?
  A: A2A already won this slot (Google → Linux Foundation). Riding the existing
     well-known convention is the whole adoption trick — clients that speak A2A
     discover a NAVIG node for free, no NAVIG-specific code on their side.

  Q: What maps to an A2A "skill"?
  A: A NAVIG node's mesh *capabilities* (``llm``/``shell``/``docker``/``ssh``/``gpu``)
     — the same list already gossiped over UDP multicast. One capability → one skill.

  Q: Does this add a new transport?
  A: No. The card advertises the node's existing gateway URL. Messaging still
     rides the existing ``/mesh/route`` + chat endpoints. Card == discovery only.

This module is pure (no I/O): it turns a :class:`~navig.mesh.registry.NodeRecord`
into a dict. The gateway route layer serves it over HTTP.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from navig.mesh.registry import NodeRecord

# A2A spec version this card conforms to. Kept as a constant so a single edit
# re-stamps every served card when we track a newer A2A revision.
A2A_PROTOCOL_VERSION = "0.2.5"

# One NAVIG mesh capability → one A2A skill descriptor.
# id/name/description/tags follow the A2A AgentSkill shape.
_CAPABILITY_SKILLS: dict[str, dict[str, object]] = {
    "llm": {
        "id": "chat",
        "name": "Conversational agent",
        "description": "Hold a natural-language conversation and reason over a task.",
        "tags": ["llm", "chat", "reasoning"],
        "examples": ["Summarise the last 24h of server logs."],
    },
    "shell": {
        "id": "shell",
        "name": "Shell execution",
        "description": "Run shell commands on this host and return their output.",
        "tags": ["shell", "exec", "ops"],
        "examples": ["Show disk usage for the root filesystem."],
    },
    "docker": {
        "id": "docker",
        "name": "Container management",
        "description": "Inspect and manage Docker containers on this host.",
        "tags": ["docker", "containers", "ops"],
        "examples": ["List running containers and their health."],
    },
    "ssh": {
        "id": "ssh",
        "name": "Remote host operations",
        "description": "Operate remote servers over SSH from this node.",
        "tags": ["ssh", "remote", "ops"],
        "examples": ["Restart nginx on the production web host."],
    },
    "gpu": {
        "id": "gpu",
        "name": "GPU-accelerated inference",
        "description": "Run GPU-accelerated model inference on this node.",
        "tags": ["gpu", "inference", "compute"],
        "examples": ["Run a local model for a large batch job."],
    },
}


def _skills_for(capabilities: list[str]) -> list[dict[str, object]]:
    """Map a node's mesh capabilities to A2A AgentSkill descriptors.

    Unknown capabilities degrade to a generic skill so a card is never empty
    and forward-compatible with capabilities added later.
    """
    skills: list[dict[str, object]] = []
    for cap in capabilities:
        known = _CAPABILITY_SKILLS.get(cap)
        if known is not None:
            skills.append(dict(known))  # copy so callers can't mutate the table
        else:
            skills.append(
                {
                    "id": cap,
                    "name": cap.replace("_", " ").title(),
                    "description": f"NAVIG capability: {cap}.",
                    "tags": [cap],
                    "examples": [],
                }
            )
    return skills


def build_agent_card(record: NodeRecord, *, base_url: str | None = None) -> dict:
    """Build an A2A-compatible Agent Card from a mesh :class:`NodeRecord`.

    Args:
        record: The node to describe (usually the registry's ``self_record``,
            but any peer record works — that is what powers the mesh→A2A bridge).
        base_url: Override for the advertised service base. Defaults to the
            record's ``gateway_url``. The A2A JSON-RPC path (``/a2a``) is always
            appended. Pass the externally reachable base when the node is fronted
            by an edge/proxy so remote clients get a working endpoint instead of
            a LAN IP.

    Returns:
        A JSON-serialisable dict conforming to the A2A AgentCard shape.
    """
    url = (base_url or record.gateway_url).rstrip("/")
    return {
        "protocolVersion": A2A_PROTOCOL_VERSION,
        "name": f"NAVIG · {record.hostname}",
        "description": (
            "A NAVIG operator node — an agent that runs real infrastructure "
            "(shell, containers, SSH, deployments) on this host."
        ),
        "url": f"{url}/a2a",
        "preferredTransport": "JSONRPC",
        "version": record.version,
        "provider": {"organization": "NAVIG", "url": "https://navig.run"},
        "capabilities": {
            "streaming": True,
            "pushNotifications": False,
            "stateTransitionHistory": False,
        },
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": _skills_for(record.capabilities),
        # NAVIG-namespaced extension: lets A2A clients that also speak mesh
        # correlate a card back to its mesh node without polluting spec fields.
        "x-navig": {
            "nodeId": record.node_id,
            "os": record.os,
            "formation": record.formation or None,
            "role": record.role,
        },
    }
