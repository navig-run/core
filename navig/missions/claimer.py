"""MissionClaimer — the node-side half of a distributed mission.

The contract layer already models a :class:`~navig.contracts.mission.Mission` and its state
machine, and the gateway already exposes list / advance / complete. What was missing is the
piece that makes ``node_id`` mean anything: something on a *node* that asks the brain for the
queued missions addressed to it, claims one, runs it, and finishes it with a signed receipt.

Three properties decide whether that is safe, and each is load-bearing:

**The claim is the fence.** A worker never executes a mission it did not win. Claiming is a
compare-and-set on the mission's own state machine — ``queued`` → ``running`` — so the loser of
a race is told, not guessed at. The store enforces the transition; this module only reads the
answer.

**A lost claim is not an error.** Two nodes may poll at the same moment; exactly one gets the
mission and the other moves on to the next one. Treating ``409`` as a failure would make every
busy node log false errors, so the outcome records ``claimed=False`` and no execution happens.

**Every terminal state leaves a receipt.** Completion goes through the store's
:meth:`~navig.contracts.store.RuntimeStore.complete_mission`, which signs an Ed25519 receipt.
A mission that raised still finishes — as a failure with its error text — because a mission
left in ``running`` for ever is invisible work, and invisible work is what this whole plane
exists to end.

The transport is injected so the same claimer drives the brain in-process (``StoreTransport``)
or a remote brain over its HTTP API (``HttpTransport``); the protocol is identical either way,
which is why the tests need no server.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

from navig.contracts.mission import Mission, MissionStatus

#: Capability a node can actually execute today. Anything else is refused loudly rather than
#: silently "succeeding" with nothing done — a mission that reports success without work is
#: worse than one that reports it cannot run.
CAPABILITY_READINESS_PROBE = "readiness.probe"


class UnknownCapability(RuntimeError):
    """The mission asks for a capability this node does not implement."""

    def __init__(self, capability: str, known: list[str]) -> None:
        self.capability = capability
        self.known = known
        super().__init__(
            f"this node cannot execute capability {capability!r}; "
            f"it knows: {', '.join(known) or 'none'}"
        )


class Transport(Protocol):
    """What a claimer needs from whoever owns the mission store."""

    async def list_queued(self, node_id: str) -> list[Mission]:
        """Queued missions addressed to ``node_id``, oldest first."""

    async def claim(self, mission_id: str) -> bool:
        """``True`` only when this caller won the ``queued`` → ``running`` transition."""

    async def complete(
        self,
        mission_id: str,
        *,
        succeeded: bool,
        result: Any = None,
        error: str | None = None,
    ) -> str:
        """Finish the mission and return the id of the receipt it signed."""


@dataclass
class ClaimOutcome:
    """What one poll did. Empty is a normal answer: nothing was queued for this node."""

    node_id: str
    claimed: bool = False
    mission_id: str = ""
    capability: str = ""
    executed: bool = False
    succeeded: bool = False
    receipt_id: str = ""
    error: str = ""
    result: Any = field(default=None, repr=False)

    @property
    def idle(self) -> bool:
        """There was nothing to do — distinct from "something went wrong"."""
        return not self.claimed

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": self.succeeded or self.idle,
            "node_id": self.node_id,
            "claimed": self.claimed,
            "mission_id": self.mission_id,
            "capability": self.capability,
            "executed": self.executed,
            "succeeded": self.succeeded,
            "receipt_id": self.receipt_id,
        }
        if self.error:
            payload["error"] = self.error
        if self.result is not None:
            payload["result"] = self.result
        return payload


class StoreTransport:
    """Drive the mission store this process already holds — the brain's own case.

    Mutations are flushed immediately. The gateway flushes at the end of each request, but this
    transport is not inside a request: without the flush, a claim and its signed receipt live
    only in this process, so the next ``RuntimeStore()`` loads the mission still ``queued`` and
    the receipt is lost — work that happened and left no trace, which is the exact failure this
    plane exists to prevent.
    """

    def __init__(self, store) -> None:  # noqa: ANN001 - RuntimeStore, imported lazily by callers
        self._store = store

    def _flush(self) -> None:
        flush = getattr(self._store, "flush", None)
        if callable(flush):
            flush()

    async def list_queued(self, node_id: str) -> list[Mission]:
        missions = self._store.list_missions(node_id=node_id, status=MissionStatus.QUEUED)
        # The store lists newest first; a queue is drained oldest first, so the mission that
        # has waited longest is the one that runs next.
        return sorted(missions, key=lambda mission: mission.created_at)

    async def claim(self, mission_id: str) -> bool:
        try:
            self._store.advance_mission(mission_id, "start")
        except (KeyError, ValueError):
            # Gone, or no longer queued: somebody else owns it. Not an error for us.
            return False
        self._flush()
        return True

    async def complete(
        self,
        mission_id: str,
        *,
        succeeded: bool,
        result: Any = None,
        error: str | None = None,
    ) -> str:
        receipt = self._store.complete_mission(
            mission_id, succeeded=succeeded, result=result, error=error
        )
        self._flush()
        return receipt.receipt_id


class HttpTransport:
    """Drive a remote brain over the runtime API it already exposes.

    The three calls map one-to-one onto routes that exist
    (:mod:`navig.gateway.routes.runtime`): list, advance, complete. ``claim`` reads the
    *status code* rather than the body, because the state machine — not the client — decides
    who wins a race.
    """

    def __init__(
        self,
        base_url: str,
        token: str = "",
        *,
        timeout: float = 30.0,
        session_factory: Callable[..., Any] | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout
        self._session_factory = session_factory

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def _request(self, method: str, path: str, payload: dict | None = None):
        import aiohttp

        url = f"{self._base}{path}"
        timeout = aiohttp.ClientTimeout(total=self._timeout)
        factory = self._session_factory or aiohttp.ClientSession
        async with factory(timeout=timeout) as session:
            async with session.request(
                method, url, json=payload, headers=self._headers()
            ) as response:
                body = await response.text(encoding="utf-8", errors="replace")
                return response.status, body

    async def list_queued(self, node_id: str) -> list[Mission]:
        status, body = await self._request(
            "GET", f"/runtime/missions?node_id={node_id}&status=queued"
        )
        if status != 200:
            raise RuntimeError(f"listing missions failed: HTTP {status}: {body[:200]}")
        payload = json.loads(body or "{}")
        rows = payload.get("data") or payload
        missions = rows.get("missions", rows) if isinstance(rows, dict) else rows
        queued = [Mission.from_dict(row) for row in missions or []]
        return sorted(queued, key=lambda mission: mission.created_at)

    async def claim(self, mission_id: str) -> bool:
        status, _ = await self._request(
            "POST", f"/runtime/missions/{mission_id}/advance", {"action": "start"}
        )
        # 200 = we won it. 409/404 = somebody else did, or it is gone; both mean "not ours".
        return status == 200

    async def complete(
        self,
        mission_id: str,
        *,
        succeeded: bool,
        result: Any = None,
        error: str | None = None,
    ) -> str:
        # The route's documented body is {outcome, output, error} — NOT the store's
        # `succeeded`/`result` spelling. Sending the store's spelling is insidious: `outcome`
        # defaults to "success", so the mission reports success while `output` is dropped and
        # the receipt's digest covers nothing. Measured against the live gateway 2026-10-28.
        payload: dict[str, Any] = {"outcome": "success" if succeeded else "failure"}
        if result is not None:
            payload["output"] = result
        if error:
            payload["error"] = error
        status, body = await self._request(
            "POST", f"/runtime/missions/{mission_id}/complete", payload
        )
        if status not in (200, 201):
            raise RuntimeError(f"completing {mission_id} failed: HTTP {status}: {body[:200]}")
        envelope = json.loads(body or "{}")
        data = envelope.get("data") or envelope
        receipt = data.get("receipt") or data
        return str(receipt.get("receipt_id", ""))


# ── what a node can actually do ───────────────────────────────────────────────


def _probe_readiness(mission: Mission) -> dict[str, Any]:
    """Answer §12.3's question about ONE machine: can it be worked through while the PC is off?

    The mission's ``metadata`` may carry ``editor_url`` and ``repo_root``; the defaults are what
    the probe uses on any host, so a mission with no payload still produces a real answer.
    """
    from pathlib import Path

    from navig.readiness.remote_dev import assess

    metadata = mission.metadata or {}
    repo_root = Path(metadata.get("repo_root") or Path.cwd())
    capabilities = assess(
        repo_root=repo_root,
        editor_url=str(metadata.get("editor_url", "")),
        check_remote_build=bool(metadata.get("check_remote_build", True)),
    )
    return {
        "capabilities": {
            capability.key: {
                "readiness": capability.readiness.value,
                "headline": capability.headline(),
                "blockers": [item.name for item in capability.blockers],
                "unknowns": [item.name for item in capability.unknowns],
            }
            for capability in capabilities
        },
    }


_EXECUTORS: dict[str, Callable[[Mission], dict[str, Any]]] = {
    CAPABILITY_READINESS_PROBE: _probe_readiness,
}


def known_capabilities() -> list[str]:
    return sorted(_EXECUTORS)


def execute_mission(mission: Mission) -> dict[str, Any]:
    """Run one mission's work and return its result payload.

    Raises :class:`UnknownCapability` for anything this node does not implement — the claimer
    turns that into a *failed* mission with the reason attached, never a silent success.
    """
    runner = _EXECUTORS.get(mission.capability or "")
    if runner is None:
        raise UnknownCapability(mission.capability or "", known_capabilities())
    return runner(mission)


# ── the claimer ───────────────────────────────────────────────────────────────


class MissionClaimer:
    """Claim queued missions for one node, run them, and finish them with a receipt."""

    def __init__(
        self,
        transport: Transport,
        *,
        node_id: str,
        execute: Callable[[Mission], Awaitable[Any] | Any] | None = None,
    ) -> None:
        self._transport = transport
        self._node_id = node_id
        self._execute = execute or execute_mission

    async def run_once(self) -> ClaimOutcome:
        """Claim and run at most one mission. An empty queue is a normal outcome."""
        queued = await self._transport.list_queued(self._node_id)
        # A queue is drained oldest first, and the policy lives HERE rather than in each
        # transport: a transport that hands back newest-first (the store lists that way)
        # must not change which mission runs next.
        for mission in sorted(queued, key=lambda item: item.created_at):
            if not await self._transport.claim(mission.mission_id):
                continue  # another node won it; try the next one rather than failing
            return await self._run_claimed(mission)
        return ClaimOutcome(node_id=self._node_id)

    async def _run_claimed(self, mission: Mission) -> ClaimOutcome:
        outcome = ClaimOutcome(
            node_id=self._node_id,
            claimed=True,
            mission_id=mission.mission_id,
            capability=mission.capability,
        )
        try:
            result = self._execute(mission)
            if asyncio.iscoroutine(result):
                result = await result
        except UnknownCapability as exc:
            outcome.error = str(exc)
            outcome.receipt_id = await self._transport.complete(
                mission.mission_id, succeeded=False, error=str(exc)
            )
            return outcome
        except Exception as exc:  # noqa: BLE001 - a mission failing must still close
            outcome.error = f"{type(exc).__name__}: {exc}"
            outcome.receipt_id = await self._transport.complete(
                mission.mission_id, succeeded=False, error=outcome.error
            )
            return outcome
        outcome.executed = True
        outcome.succeeded = True
        outcome.result = result
        outcome.receipt_id = await self._transport.complete(
            mission.mission_id, succeeded=True, result=result
        )
        return outcome
