"""The NAVIG Reflex service — plan (pure) / commit (effectful, idempotent) / ask.

One utterance, one tool. ``route`` classifies deterministically (no model), applies
the profile's approval table, and either mints a plan or plans-then-commits a single
reversible action. ``commit(token)`` is the only effectful entry point and is
idempotent on the token: the first claim runs the bound command(s), every replay
returns the stored Answer verbatim.

There is **no commit-time tool fallback**: one plan names one tool and runs it (a
batch is N idempotent commands under one token). The only fallback in the whole
router is classification-time — an unmatched utterance returns ``matched=False`` so
the caller can fall through to the existing ``ask_ai`` path, exactly once.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from navig.reflex.answer_bridge import (
    answer_as_dict,
    answer_from_dict,
    make_answer,
)
from navig.reflex.errors import CommandFailed, ReflexError
from navig.reflex.executor import (
    CommandRunner,
    ExecutionResult,
    SubprocessRunner,
    execute,
    logical_argv,
)
from navig.reflex.manifest import ToolManifest, ToolRegistry, default_registry
from navig.reflex.plans import CommitPlan, new_token
from navig.reflex.preclassify import Preclassification, classify
from navig.reflex.profiles import (
    DEFAULT_PROFILE,
    configured_security_level,
    describe,
    needs_explicit_commit,
    resolve_profile,
)
from navig.reflex.resolve import EndpointResolver, StoreResolver
from navig.reflex.store import CommitPlanStore

logger = logging.getLogger(__name__)

#: Human verbs per tool, used to phrase the acknowledgement honestly.
_VERBS: dict[str, str] = {
    "home.on": "on",
    "home.off": "off",
    "home.toggle": "toggled",
    "home.set": "set",
    "tv.power": "TV power toggled",
    "tv.play": "TV play/pause",
    "assistant.status": "assistant status",
    "campaigns.status": "campaigns",
    "coordination.inbox": "coordination inbox",
}


@dataclass
class RouteResult:
    """What the router decided for one utterance (``matched`` drives the fallback)."""

    text: str
    profile: str
    matched: bool = False
    committed: bool = False
    answer: Any = None
    plan: CommitPlan | None = None
    preclassification: Preclassification | None = None
    needs_slot: str = ""

    def as_dict(self) -> dict[str, Any]:
        payload = answer_as_dict(self.answer) if self.answer is not None else {}
        payload.update(
            {
                "text": self.text,
                "profile": self.profile,
                "matched": self.matched,
                "committed": self.committed,
                "needs_slot": self.needs_slot,
            }
        )
        return payload


class ReflexRouter:
    """Deterministic action router over the registered tool manifest."""

    def __init__(
        self,
        *,
        registry: ToolRegistry | None = None,
        resolver: EndpointResolver | None = None,
        store: CommitPlanStore | None = None,
        runner: CommandRunner | None = None,
        profile: str = DEFAULT_PROFILE,
        security_level: str | None = None,
    ) -> None:
        self._registry = registry or default_registry()
        self._resolver: EndpointResolver = resolver or StoreResolver()
        self._store = store or CommitPlanStore()
        self._runner: CommandRunner = runner or SubprocessRunner()
        self._profile = resolve_profile(profile)
        self._security_level = security_level

    # ── reads ───────────────────────────────────────────────────────────────

    @property
    def profile(self) -> str:
        return self._profile

    def security_level(self) -> str:
        if self._security_level is None:
            self._security_level = configured_security_level()
        return self._security_level

    def manifests(self) -> list[dict[str, Any]]:
        """The projected tool manifests (the one registry, serialized)."""
        return self._registry.manifests()

    def profiles(self) -> list[dict[str, Any]]:
        return [describe(name) for name in ("safe", "fast", "campaign")]

    def classify(self, text: str) -> Preclassification | None:
        """Pure classification only — no token minted, nothing persisted."""
        return classify(text, self._registry, self._resolver)

    # ── plan / route / commit ───────────────────────────────────────────────

    def plan(self, text: str, *, profile: str | None = None) -> RouteResult:
        """Mint a plan and return it. Mutates no device; persists only the token row."""
        return self._route(text, profile=profile, force_plan=True)

    def ask(self, text: str, *, profile: str | None = None) -> RouteResult:
        """Route per the profile: plan-only, or plan+commit one reversible action."""
        return self.route(text, profile=profile)

    def route(self, text: str, *, profile: str | None = None) -> RouteResult:
        return self._route(text, profile=profile, force_plan=False)

    def _route(self, text: str, *, profile: str | None, force_plan: bool) -> RouteResult:
        active = resolve_profile(profile or self._profile)
        finding = self.classify(text)
        if finding is None:
            return RouteResult(text=text, profile=active)
        if finding.needs_slot:
            return RouteResult(
                text=text,
                profile=active,
                matched=True,
                needs_slot=finding.needs_slot,
                answer=self._slot_answer(finding),
                preclassification=finding,
            )
        tool = finding.tool
        plan_only = force_plan or needs_explicit_commit(
            active,
            read_only=tool.read_only,
            risk=tool.risk,
            destructive=finding.destructive,
            tool_name=tool.name,
            security_level=self.security_level(),
        )
        plan = self._create_plan(finding, active)
        if plan_only:
            return RouteResult(
                text=text,
                profile=active,
                matched=True,
                plan=plan,
                answer=self._plan_answer(plan, finding),
                preclassification=finding,
            )
        answer = self.commit(plan.token)
        return RouteResult(
            text=text,
            profile=active,
            matched=True,
            committed=True,
            plan=plan,
            answer=answer,
            preclassification=finding,
        )

    def commit(self, token: str) -> Any:
        """Run the plan behind *token* exactly once; a replay returns the stored Answer."""
        plan, fresh = self._store.claim(token)
        if not fresh:
            return self._replay_answer(plan)
        tool = self._registry.get(plan.tool)
        if tool is None:
            answer = self._failure_answer(
                plan, f"the plan names unknown tool {plan.tool!r}", ok=False
            )
            self._store.finish(plan.token, answer_as_dict(answer), ok=False)
            return answer
        try:
            target_ids = [str(item.get("id") or "") for item in plan.targets]
            execution = execute(tool, plan.slots, target_ids, self._runner)
            answer = self._commit_answer(tool, plan, execution)
        except ReflexError as exc:
            answer = self._failure_answer(plan, exc.message, hint=exc.hint, ok=False)
        except Exception as exc:  # noqa: BLE001 — a crash must still close the token honestly
            logger.debug("commit of %s failed", plan.token, exc_info=True)
            answer = self._failure_answer(plan, str(exc), ok=False)
        payload = answer_as_dict(answer)
        self._store.finish(plan.token, payload, ok=bool(payload.get("ok", True)))
        return answer

    # ── plan construction ───────────────────────────────────────────────────

    def _create_plan(self, finding: Preclassification, profile: str) -> CommitPlan:
        tool = finding.tool
        first_target = finding.targets[0].id if finding.targets else ""
        plan = CommitPlan(
            token=new_token(),
            tool=tool.name,
            command=logical_argv(tool, finding.slots, first_target),
            profile=profile,
            risk=tool.risk,
            read_only=tool.read_only,
            destructive=finding.destructive,
            slots=dict(finding.slots),
            targets=[target.as_dict() for target in finding.targets],
            blast_radius=dict(finding.blast_radius),
        )
        return self._store.create(plan)

    # ── answers ─────────────────────────────────────────────────────────────

    def _plan_answer(self, plan: CommitPlan, finding: Preclassification) -> Any:
        targets = ", ".join(str(item.get("label") or item.get("id")) for item in plan.targets)
        if targets:
            where = targets
        elif plan.read_only:
            where = "every device"
        else:
            where = "no target"
        speech = f"Plan ready: {plan.tool} → {where}. Nothing was executed."
        return make_answer(
            speech=speech,
            display_text=speech,
            ok=True,
            intent=plan.tool,
            data={
                "matched": True,
                "executed": False,
                "plan": plan.as_dict(),
                "alternatives": [dict(item) for item in finding.alternatives],
            },
            plan=self._plan_payload(plan),
            suggestions=[f'navig ask --commit {plan.token}'],
        )

    def _slot_answer(self, finding: Preclassification) -> Any:
        names = [
            str(item.get("label") or item.get("id") or item.get("room") or "")
            for item in finding.candidates
        ]
        names = [name for name in names if name]
        slot = finding.needs_slot
        if slot == "capability":
            supported = ", ".join(
                str(item) for item in (finding.candidates[0].get("capabilities") or [])
            )
            speech = (
                f"{finding.candidates[0].get('label') or 'that device'} does not declare "
                f"that capability. Supported: {supported or 'nothing declared'}."
            )
        elif names:
            speech = f"Which one do you mean for {finding.tool.name}? {'; '.join(names[:5])}"
        else:
            speech = f"No target matched for {finding.tool.name}. Name a device first."
        return make_answer(
            speech=speech,
            display_text=speech,
            ok=True,
            intent=finding.tool.name,
            data={
                "matched": True,
                "executed": False,
                "needs_slot": slot,
                "candidates": [dict(item) for item in finding.candidates],
                "alternatives": [dict(item) for item in finding.alternatives],
            },
            suggestions=names[:5],
        )

    def _replay_answer(self, plan: CommitPlan) -> Any:
        if plan.result:
            return answer_from_dict(plan.result)
        speech = (
            "This plan was already committed but its result was not recorded — "
            "nothing was re-run."
        )
        return make_answer(
            speech=speech,
            display_text=speech,
            ok=False,
            intent=plan.tool,
            data={"plan": plan.as_dict(), "interrupted": True, "executed": False},
            plan=self._plan_payload(plan),
        )

    def _failure_answer(
        self, plan: CommitPlan, message: str, *, hint: str = "", ok: bool = False
    ) -> Any:
        speech = f"{plan.tool} failed: {message}"
        if hint:
            speech = f"{speech} — {hint}"
        return make_answer(
            speech=speech,
            display_text=speech,
            ok=ok,
            intent=plan.tool,
            data={"plan": plan.as_dict(), "executed": False, "error": message, "hint": hint},
            plan=self._plan_payload(plan),
        )

    def _commit_answer(
        self, tool: ToolManifest, plan: CommitPlan, execution: ExecutionResult
    ) -> Any:
        if not execution.ok:
            error = execution.error or "the command exited non-zero"
            return self._failure_answer(
                plan,
                error,
                hint=f"re-run `navig {' '.join(tool.command)}` manually to see the detail",
                ok=False,
            )
        payload = execution.payload if tool.read_only else None
        speech = self._speech(tool, plan, execution)
        table = self._table(tool, payload)
        suggestions = self._suggestions(tool, plan)
        return make_answer(
            speech=speech,
            display_text=execution.output or speech,
            ok=True,
            table=table,
            json=payload if payload is not None else execution.as_dict(),
            intent=tool.name,
            data={
                "matched": True,
                "executed": True,
                "tool": tool.name,
                "targets": [dict(item) for item in plan.targets],
                "commands": [item.as_dict() for item in execution.outcomes],
                "result": payload,
                "plan": plan.as_dict(),
            },
            plan=self._plan_payload(plan),
            suggestions=suggestions,
        )

    def _speech(self, tool: ToolManifest, plan: CommitPlan, execution: ExecutionResult) -> str:
        payload = execution.payload if tool.read_only else None
        if tool.name == "home.status" and isinstance(payload, dict):
            return _home_status_speech(payload)
        if tool.name == "assistant.status" and isinstance(payload, dict):
            healthy = bool(payload.get("ok", True))
            return "The assistant is healthy." if healthy else "The assistant reports a problem."
        if tool.name == "campaigns.status" and isinstance(payload, dict):
            campaigns = payload.get("campaigns")
            count = len(campaigns) if isinstance(campaigns, list) else payload.get("count", 0)
            return f"{count} campaign(s)."
        if tool.name == "coordination.inbox" and isinstance(payload, dict):
            messages = payload.get("messages")
            count = len(messages) if isinstance(messages, list) else payload.get("count", 0)
            recipient = str(payload.get("recipient") or "operator")
            if count:
                return f"{count} coordination message(s) waiting for {recipient}."
            return f"No coordination messages for {recipient}."
        labels = [str(item.get("label") or item.get("id")) for item in plan.targets]
        where = labels[0] if len(labels) == 1 else f"{len(labels)} targets"
        if tool.name == "home.set":
            return f"{where}: {plan.slots.get('capability')} = {plan.slots.get('value')}."
        if tool.name == "tv.volume":
            return f"TV volume {plan.slots.get('direction')}."
        verb = _VERBS.get(tool.name, tool.name)
        if tool.read_only:
            return f"{verb}."
        return f"{where} {verb}."

    def _table(self, tool: ToolManifest, payload: Any) -> dict[str, Any] | None:
        if not isinstance(payload, dict):
            return None
        if tool.name == "home.status":
            devices = payload.get("devices")
            if not isinstance(devices, list):
                return None
            rows = []
            for device in devices:
                if not isinstance(device, dict):
                    continue
                rows.append(
                    [
                        str(device.get("name") or device.get("device") or "—"),
                        "online" if device.get("ok") else "offline",
                        "on" if device.get("power") else "off",
                    ]
                )
            if not rows:
                return None
            return {"columns": ["Device", "Reach", "Power"], "rows": rows}
        if tool.name == "campaigns.status":
            campaigns = payload.get("campaigns")
            if not isinstance(campaigns, list):
                return None
            rows = [
                [str(item.get("slug") or item.get("id") or "—"), str(item.get("state") or "—")]
                for item in campaigns
                if isinstance(item, dict)
            ]
            return {"columns": ["Campaign", "State"], "rows": rows} if rows else None
        if tool.name == "coordination.inbox":
            messages = payload.get("messages")
            if not isinstance(messages, list) or not messages:
                return None
            rows = [
                [
                    str(item.get("sender") or "—"),
                    str(item.get("kind") or "—"),
                    str(item.get("delivery_state") or "—"),
                    _content_snippet(item.get("content")),
                ]
                for item in messages
                if isinstance(item, dict)
            ]
            return {"columns": ["From", "Kind", "State", "Content"], "rows": rows} if rows else None
        return None

    def _suggestions(self, tool: ToolManifest, plan: CommitPlan) -> list[str]:
        if tool.read_only:
            return []
        if tool.name.startswith("home."):
            targets = [str(item.get("id")) for item in plan.targets if item.get("id")]
            if targets:
                return [f"navig home status {targets[0]}"]
        return []

    @staticmethod
    def _plan_payload(plan: CommitPlan) -> dict[str, Any]:
        return {
            "token": plan.token,
            "commit_token": plan.token,
            "tool": plan.tool,
            "slots": dict(plan.slots),
            "targets": [dict(item) for item in plan.targets],
            "blast_radius": dict(plan.blast_radius),
            "profile": plan.profile,
            "risk": plan.risk,
            "destructive": plan.destructive,
            "read_only": plan.read_only,
            "command": list(plan.command),
            "expires_at": plan.expires_at,
            "next": plan.next_command,
        }


def _content_snippet(content: Any) -> str:
    """A bounded, one-line digest of a coordination message's content for a table cell."""
    if not content:
        return ""
    text = str(content)
    return text if len(text) <= 40 else text[:37] + "…"


def _home_status_speech(payload: dict[str, Any]) -> str:
    devices = payload.get("devices")
    if not isinstance(devices, list) or not devices:
        return "No devices are configured."
    if len(devices) == 1:
        device = devices[0] if isinstance(devices[0], dict) else {}
        name = str(device.get("name") or device.get("device") or "the device")
        if device.get("ok") is False:
            return f"{name} is unreachable."
        return f"{name} is {'on' if device.get('power') else 'off'}."
    reachable = sum(1 for item in devices if isinstance(item, dict) and item.get("ok"))
    return f"{reachable} of {len(devices)} devices reachable."


__all__ = ["ReflexRouter", "RouteResult", "CommandFailed"]
