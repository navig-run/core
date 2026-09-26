"""
MissionExecutor — the single execution path for autonomous + board AI work.

A Mission is dispatched here, run through the ConversationalAgent's agentic
ReAct loop (the canonical tool-using path), autonomy-gated (draft / approval /
auto), and finalized into an ExecutionReceipt with a `mission_state` SSE
emission so the Deck shows it live.

Concurrency: ONE shared asyncio.Semaphore bounds every execution. This is also
the structural fix for the board's previously-unbounded ``run_in_executor(None,
…)`` pool — board card runs funnel through ``run_bounded`` and share this gate.

Safety: system-initiated missions (heartbeat / proactive) are gated by the
master flag ``missions.autonomous_enabled`` (default False) at the call sites;
the executor itself enforces the per-mission autonomy mode, defaulting to
APPROVAL — it never silently AUTO-executes a system mission.
"""

from __future__ import annotations

import asyncio
import json
import logging
from enum import Enum
from typing import Any, Callable

from navig.contracts.mission import Mission
from navig.contracts.store import RuntimeStore, get_runtime_store

logger = logging.getLogger(__name__)


class MissionIncomplete(RuntimeError):
    """The agent loop ended without a real answer (turn cap / empty reply)."""


class Autonomy(str, Enum):
    """Resolved per-mission autonomy mode at the executor boundary."""

    DRAFT = "draft"        # propose only, no tools, no side effects
    APPROVAL = "approval"  # gate via ApprovalManager before executing
    AUTO = "auto"          # execute directly


# Capability → base toolset. run_agentic semantic-merges suggested tools on top,
# so a conservative base is enough; override per-mission via metadata["toolset"].
_CAPABILITY_TOOLSETS = {
    "remediate": "core",
    "proactive": "core",
    "agentic": "core",
    "board_card": "core",
}


class MissionExecutor:
    """Runs Missions to completion through the agentic loop, bounded + gated."""

    def __init__(
        self,
        gateway: Any,
        *,
        max_concurrency: int | None = None,
        store: RuntimeStore | None = None,
    ) -> None:
        self.gateway = gateway
        self.store = store or get_runtime_store()

        cfg: dict = {}
        try:
            cfg = (gateway.config_manager.global_config or {}).get("missions", {}) or {}
        except Exception:  # noqa: BLE001 — config is best-effort
            cfg = {}

        conc = max_concurrency if max_concurrency is not None else int(cfg.get("max_concurrency", 3))
        self._sem = asyncio.Semaphore(max(1, conc))
        self.default_timeout = float(cfg.get("default_timeout_secs", 600))
        self.max_iterations = int(cfg.get("max_iterations", 8))

        self._tasks: set[asyncio.Task] = set()
        self._active: set[str] = set()  # mission ids currently being handled

    # ── Public API ────────────────────────────────────────────────────

    @property
    def active(self) -> set[str]:
        """Mission ids the executor is currently handling (incl. awaiting approval)."""
        return self._active

    async def submit(self, mission: Mission) -> Mission:
        """Persist + enqueue a mission and start it in the background. Returns now."""
        if mission.timeout_secs is None:
            mission.timeout_secs = self.default_timeout
        self.store.create_mission(mission)
        self.store.flush()
        self._active.add(mission.mission_id)
        await self._emit(mission)  # queued
        self._spawn(mission)
        return mission

    async def run_to_completion(self, mission: Mission) -> Mission:
        """Persist + run a mission inline (awaitable). Used where the caller wants
        the terminal result, e.g. a synchronous board card path under Phase B."""
        if mission.timeout_secs is None:
            mission.timeout_secs = self.default_timeout
        self.store.create_mission(mission)
        self.store.flush()
        self._active.add(mission.mission_id)
        await self._emit(mission)
        return await self._execute(mission)

    async def run_bounded(self, fn: Callable, *args: Any, **kwargs: Any) -> Any:
        """Run a BLOCKING callable in a worker thread under the shared semaphore.

        This is the board's execution path: it keeps the existing structured
        ``_run_card_sync`` logic intact while bounding the thread pool (the fix
        for the previously-unbounded ``run_in_executor(None, …)``)."""
        loop = asyncio.get_event_loop()
        async with self._sem:
            return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    async def run_tracked(
        self,
        *,
        title: str,
        capability: str,
        fn: Callable,
        args: tuple = (),
        metadata: dict | None = None,
        ok: Callable[[Any], bool] | None = None,
        summary: Callable[[Any], str] | None = None,
    ) -> Any:
        """Run a BLOCKING callable as a TRACKED Mission and return its result.

        Wraps the existing board ``_run_card_sync`` (or any sync runner) so each
        run becomes a first-class Mission — queued → running → succeeded/failed,
        an ExecutionReceipt, and `mission_state` SSE — WITHOUT changing how the
        runner itself works. The board keeps its own structured result + lane
        moves; this just adds the audit + live-stream layer on top.

        ``ok(result)`` decides succeeded vs failed (default: any non-exception).
        ``summary(result)`` produces the short text stored on the mission.
        """
        from navig.contracts.mission import Mission

        mission = Mission(
            title=title,
            capability=capability,
            metadata=metadata or {},
            timeout_secs=self.default_timeout,
        )
        self.store.create_mission(mission)
        self.store.flush()
        self._active.add(mission.mission_id)
        await self._emit(mission)  # queued

        loop = asyncio.get_event_loop()
        try:
            async with self._sem:
                mission.start()
                self.store.flush()
                await self._emit(mission)  # running

                # Honour the timeout this mission already DECLARES (set above). Without the
                # bound, a runner that never returns held a slot of the shared `_sem`
                # forever — and since `_execute` shares it, a few hung board cards wedged
                # ALL mission execution for the life of the daemon. The worker thread can't
                # be force-killed (it's a blocking callable), but timing out releases the
                # slot and leaves an honest TIMED_OUT mission + receipt instead of a
                # mission that claims a timeout it never enforces.
                timeout = mission.timeout_secs or self.default_timeout
                try:
                    result = await asyncio.wait_for(
                        loop.run_in_executor(None, lambda: fn(*args)), timeout=timeout
                    )
                except asyncio.TimeoutError:
                    logger.warning(
                        "Tracked mission %s timed out after %ss", mission.mission_id[:8], timeout
                    )
                    mission.timeout()
                    self.store.record_receipt_from_mission(mission)
                    self.store.flush()
                    await self._emit(mission)
                    raise
                except Exception as exc:  # noqa: BLE001
                    self.store.complete_mission(mission.mission_id, succeeded=False, error=str(exc))
                    self.store.flush()
                    await self._emit(mission)
                    raise
                # NOTE: ok()/summary() are CALLER-supplied callbacks — the board passes
                # `lambda r: bool(r) and r.get("agent_status") != "failed"`, which raises on
                # any truthy non-dict result. A raise here lands in the outer handler below
                # rather than stranding the mission RUNNING.
                succeeded = ok(result) if ok else True
                if succeeded:
                    self.store.complete_mission(
                        mission.mission_id,
                        succeeded=True,
                        result=summary(result) if summary else None,
                    )
                else:
                    self.store.complete_mission(
                        mission.mission_id, succeeded=False, error="run reported failure"
                    )
                self.store.flush()
                await self._emit(mission)  # terminal
                return result
        except Exception as exc:  # noqa: BLE001 — never let a crash strand the mission
            # The crash may have happened AFTER mission.start() flipped it RUNNING but
            # before any terminal state was recorded — a store flush that raised, or a
            # caller-supplied ok()/summary() that raised on an unexpected result shape.
            # Without this the mission hangs RUNNING forever with no ExecutionReceipt (the
            # Deck shows it live indefinitely and the audit trail is missing). `_execute`
            # got this in #685; this sibling entry point was missed. Best-effort, and the
            # original exception is always re-raised — the caller's contract is unchanged.
            if not mission.is_terminal:
                try:
                    self.store.complete_mission(
                        mission.mission_id, succeeded=False, error=f"tracked run crashed: {exc}"
                    )
                    self.store.flush()
                    await self._emit(mission)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "failed to finalize crashed tracked mission %s", mission.mission_id[:8]
                    )
            raise
        finally:
            self._active.discard(mission.mission_id)

    async def aclose(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        self._tasks.clear()

    # ── Execution core ────────────────────────────────────────────────

    def _spawn(self, mission: Mission) -> None:
        task = asyncio.create_task(self._execute(mission))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _execute(self, mission: Mission) -> Mission:
        try:
            autonomy = self._resolve_autonomy(mission)

            # Approval gate happens while the mission is still QUEUED, so the
            # Deck shows it as queued during the wait.
            if autonomy == Autonomy.APPROVAL:
                approved = await self._request_approval(mission)
                if not approved:
                    mission.cancel("approval denied or timed out")
                    self.store.record_receipt_from_mission(mission)
                    self.store.flush()
                    await self._emit(mission)  # cancelled
                    return mission

            # Provable trust: AUTO missions have no human in the loop, so a cheap
            # adversarial verifier checks them before they run. APPROVAL/DRAFT already
            # have oversight and are skipped. Best-effort — never blocks on verifier error
            # unless it returns unsafe. Verdict is recorded on the mission for audit.
            if autonomy == Autonomy.AUTO:
                verdict = await self._verify_mission(mission)
                if verdict is not None and not verdict.safe:
                    mission.cancel(f"verification failed: {verdict.reason}")
                    mission.metadata["verification"] = verdict.to_dict()
                    self.store.record_receipt_from_mission(mission)
                    self.store.flush()
                    await self._emit(mission)  # cancelled
                    return mission
                if verdict is not None:
                    mission.metadata["verification"] = verdict.to_dict()

            async with self._sem:
                mission.start()
                self.store.flush()
                await self._emit(mission)  # running

                timeout = mission.timeout_secs or self.default_timeout
                try:
                    if autonomy == Autonomy.DRAFT:
                        runner = self._run_draft(mission)
                    else:
                        runner = self._run_agent(mission)
                    result = await asyncio.wait_for(runner, timeout=timeout)
                except asyncio.TimeoutError:
                    logger.warning("Mission %s timed out after %ss", mission.mission_id[:8], timeout)
                    mission.timeout()
                    self.store.record_receipt_from_mission(mission)
                except Exception as exc:  # noqa: BLE001 — any failure is a failed mission
                    logger.warning("Mission %s failed: %s", mission.mission_id[:8], exc)
                    self.store.complete_mission(mission.mission_id, succeeded=False, error=str(exc))
                else:
                    self.store.complete_mission(mission.mission_id, succeeded=True, result=result)

                self.store.flush()
                await self._emit(mission)  # terminal
                await self._notify_complete(mission)
        except Exception as exc:  # noqa: BLE001 — never let a task die unobserved
            logger.error("Mission executor crashed on %s: %s", mission.mission_id[:8], exc)
            # The crash may have happened AFTER the mission went RUNNING but before the
            # runner recorded a terminal state (e.g. a store flush / SSE emit raised in
            # the running-setup window) — leaving it stuck RUNNING forever with no
            # receipt (the Deck shows it live indefinitely, and the audit trail is
            # missing). Finalize a still-non-terminal mission as failed + record its
            # receipt so it can't hang and every run ends with an ExecutionReceipt.
            if not mission.is_terminal:
                try:
                    self.store.complete_mission(
                        mission.mission_id, succeeded=False, error=f"executor crashed: {exc}"
                    )
                    self.store.flush()
                    await self._emit(mission)
                except Exception:  # noqa: BLE001
                    logger.exception(
                        "failed to finalize crashed mission %s", mission.mission_id[:8]
                    )
        finally:
            self._active.discard(mission.mission_id)
        return mission

    async def _run_agent(self, mission: Mission) -> str:
        """Dispatch the mission through the canonical agentic ReAct loop — under
        a MissionGrant, so no tool call inside it pages the operator."""
        from navig.agent.conv import ConversationalAgent
        from navig.tools.approval import MissionGrant, mission_grant

        agent = ConversationalAgent()
        grant = MissionGrant(
            mission_id=mission.mission_id,
            title=mission.title or "",
            allowed_commands=tuple(self._allowed_commands()),
            enabled_by=f"missions.{(mission.capability or 'agentic').lower()}.autonomy",
        )
        with mission_grant(grant):
            result = await agent.run_agentic(
                message=self._build_prompt(mission),
                max_iterations=self.max_iterations,
                toolset=self._toolset_for(mission),
            )
        # A loop that hit its turn cap (or got an empty reply) returns a sentence
        # saying so; filing that sentence as the result reported SUCCEEDED for a
        # run that produced nothing — the operator's feed read "Remediate health
        # issues — succeeded: Agent reached the 8-turn limit". That is a failure.
        incomplete = getattr(agent, "last_run_incomplete", None)
        if incomplete:
            raise MissionIncomplete(f"{incomplete}: {result}")
        return result

    def _missions_cfg(self) -> dict:
        try:
            return (self.gateway.config_manager.global_config or {}).get("missions", {}) or {}
        except Exception:  # noqa: BLE001 — config is best-effort
            return {}

    def _allowed_commands(self) -> list[str]:
        """``missions.allowed_commands``: glob patterns a mission may run unasked
        beyond the read-only set (e.g. ``"navig mode set *"``). Empty by default."""
        raw = self._missions_cfg().get("allowed_commands") or []
        if isinstance(raw, str):
            raw = [raw]
        return [str(p) for p in raw if str(p).strip()]

    async def _run_draft(self, mission: Mission) -> str:
        """DRAFT mode: propose, never execute. A single no-tools planning call."""
        from navig.llm.generate import llm_generate

        sys = (
            "You are NAVIG. Produce a concise PROPOSAL of how you would accomplish "
            "the task below — numbered steps and the expected outcome. Do NOT execute "
            "anything; this is a plan for human review."
        )
        prompt = self._build_prompt(mission)
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: llm_generate(
                messages=[{"role": "system", "content": sys}, {"role": "user", "content": prompt}],
                mode="planning",
                temperature=0.3,
                max_tokens=800,
            ),
        )

    # ── Autonomy resolution ───────────────────────────────────────────

    #: Per-capability autonomy when the mission itself says nothing. `remediate`
    #: is AUTO: under the MissionGrant it can only run read-only diagnostics and
    #: report, so the mission-level prompt bought nothing but a 15-minute timeout
    #: (80 of the operator's 84 remediate missions ended exactly that way).
    #: Override per capability with ``missions.<capability>.autonomy``.
    _CAPABILITY_AUTONOMY: dict[str, Autonomy] = {"remediate": Autonomy.AUTO}

    def _resolve_autonomy(self, mission: Mission) -> Autonomy:
        raw = (mission.metadata or {}).get("autonomy")
        if isinstance(raw, str):
            r = raw.lower()
            if r in ("draft", "approval", "auto"):
                return Autonomy(r)
            # board's "inherit" (and anything else) falls through to the global level
        cap = (mission.capability or "").lower()
        cfg_raw = (self._missions_cfg().get(cap) or {}) if cap else {}
        cfg_level = str(cfg_raw.get("autonomy") or "").lower() if isinstance(cfg_raw, dict) else ""
        if cfg_level in ("draft", "approval", "auto"):
            return Autonomy(cfg_level)
        if cap in self._CAPABILITY_AUTONOMY:
            return self._CAPABILITY_AUTONOMY[cap]
        return self._global_autonomy()

    def _global_autonomy(self) -> Autonomy:
        """Map the operator's global autonomy_level → executor mode.

        cautious / balanced → APPROVAL, autonomous → AUTO. Defaults to APPROVAL
        on any failure — never silently AUTO."""
        level = "balanced"
        try:
            from navig.agent.proactive.user_state import get_user_state_tracker

            level = get_user_state_tracker().preferences.autonomy_level
        except Exception:  # noqa: BLE001
            level = "balanced"
        return Autonomy.AUTO if level == "autonomous" else Autonomy.APPROVAL

    async def _notify_complete(self, mission: Mission) -> None:
        """Push a SYSTEM mission's outcome through the notify router.

        `mission_complete` was a registered notification type that nothing ever
        dispatched — so the three remediate missions that did run on the
        operator's daemon rewrote the LLM routing and told nobody. A mission that
        acts unasked must at least say what it did. Best-effort, never raises.
        """
        cap = (mission.capability or "").lower()
        if cap not in ("remediate", "proactive"):
            return
        try:
            from navig.notify.router import dispatch

            status = mission.status.value if hasattr(mission.status, "value") else str(mission.status)
            result = ""
            try:
                fresh = self.store.get_mission(mission.mission_id)
                result = str(getattr(fresh, "result", None) or getattr(fresh, "error", None) or "")
            except Exception:  # noqa: BLE001
                result = ""
            body = result.strip()[:1500] or f"status: {status}"
            await dispatch(
                "mission_complete",
                f"{mission.title} — {status}",
                body,
                priority="normal",
                data={"source": "navig", "mission_id": mission.mission_id, "capability": cap},
            )
        except Exception:  # noqa: BLE001 — a report must never fail the mission
            logger.debug("mission_complete notify failed for %s", mission.mission_id[:8], exc_info=True)

    async def _emit(self, mission: Mission) -> None:
        """Broadcast a `mission_state` SSE event so the Deck shows it live."""
        q = getattr(self.gateway, "system_events", None)
        if q is None or not hasattr(q, "emit"):
            return
        try:
            await q.emit(
                "mission_state",
                {
                    "id": mission.mission_id,
                    "status": mission.status.value,
                    "title": mission.title,
                    "capability": mission.capability,
                    "error": mission.error,
                },
            )
        except Exception as exc:  # noqa: BLE001 — never fail a mission on telemetry
            logger.debug("mission_state emit failed: %s", exc)

    async def _request_approval(self, mission: Mission) -> bool:
        mgr = getattr(self.gateway, "approval_manager", None)
        if mgr is None:
            # No approval channel wired → fail safe: do NOT execute.
            logger.info(
                "Mission %s needs approval but no approval_manager is available; skipping",
                mission.mission_id[:8],
            )
            return False
        try:
            return await mgr.request_approval(
                command=mission.title,
                session_key=f"mission:{mission.mission_id}",
                channel="mission",
                user_id="system",
                description=self._build_prompt(mission)[:500],
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Approval request failed for %s: %s", mission.mission_id[:8], exc)
            return False

    async def _verify_mission(self, mission: Mission):
        """Run the adversarial verifier on an AUTO mission. Returns a Verdict, or
        ``None`` when the verifier is disabled (no-op). Records to the audit log."""
        try:
            from navig.agent.verifier import get_verifier

            verifier = get_verifier()
            if not verifier.enabled:
                return None
            verdict = await verifier.verify_mission(mission)
            # No silent approvals — record the verdict to the gateway audit log.
            # (The old call passed a non-existent `detail=` kwarg and no `actor`,
            # so it TypeError'd into the except on every run: verifier verdicts
            # were never audited.)
            audit = getattr(self.gateway, "audit_log", None)
            if audit is not None:
                try:
                    audit.record(
                        actor="mission:verifier",
                        action="mission.verify",
                        policy="allow",
                        status="success" if verdict.safe else "denied",
                        metadata={"mission": mission.mission_id, **verdict.to_dict()},
                    )
                except Exception:  # noqa: BLE001 — audit is best-effort here
                    logger.debug("mission verification audit failed", exc_info=True)
            if not verdict.safe:
                logger.warning(
                    "Mission %s blocked by verifier: %s",
                    mission.mission_id[:8],
                    verdict.reason,
                )
            return verdict
        except Exception as exc:  # noqa: BLE001
            logger.debug("mission verification skipped: %s", exc)
            return None

    # ── Prompt / toolset shaping ──────────────────────────────────────

    def _toolset_for(self, mission: Mission) -> str | list[str]:
        explicit = (mission.metadata or {}).get("toolset")
        if explicit:
            return explicit
        return _CAPABILITY_TOOLSETS.get((mission.capability or "").lower(), "core")

    def _build_prompt(self, mission: Mission) -> str:
        cap = (mission.capability or "").lower()
        p = mission.payload or {}

        if cap == "remediate":
            issues = p.get("issues") or []
            lines = []
            for it in issues:
                if isinstance(it, dict):
                    lines.append("- " + str(it.get("message") or it.get("title") or json.dumps(it)))
                else:
                    lines.append(f"- {it}")
            body = "\n".join(lines) or "(no issue details provided)"
            allowed = self._allowed_commands()
            extra = (
                "\n\nYou MAY also run these pre-authorised commands: "
                + ", ".join(f"`{p}`" for p in allowed)
                if allowed
                else ""
            )
            return (
                "The system health check reported the following issue(s). Diagnose the "
                "root cause using READ-ONLY diagnostics only (status, list, show, doctor, "
                "logs, ping, git status, …). You are running unattended: any command that "
                "changes state will be refused, so do NOT attempt one and do NOT retry a "
                "refused command. Finish with a short report — root cause, evidence, and "
                "the exact command(s) the operator should run to fix it, if any."
                + extra
                + "\n\nIssues:\n"
                + body
            )

        if cap == "proactive":
            sug = p.get("suggestion") or p.get("message") or ""
            ctx = p.get("context")
            extra = f"\n\nContext: {json.dumps(ctx, default=str)}" if ctx else ""
            return (
                "A proactive engagement opportunity was detected. Decide whether acting is "
                "worthwhile and, if so, carry it out and summarize; otherwise explain why "
                "you held back.\n\nSuggestion: " + str(sug) + extra
            )

        # board_card / agentic / default
        return p.get("message") or mission.title
