"""navig.agent.verifier — adversarial pre-commit verification for autonomous actions.

The blocker to letting an always-on agent touch real infrastructure isn't capability,
it's trust. This adds a cheap second opinion: before an AUTO mission runs or a
**destructive** tool fires, a Haiku-tier model is asked to *adversarially* find why the
action is unsafe or incorrect. If it can — and confidence clears the threshold — the
action is blocked.

Design invariants:
- **Cheap & cached.** Uses ``llm_generate(mode="small_talk", effort="low")`` with a
  frozen system prompt so prompt-caching applies.
- **Best-effort, never throws into the hot path.** Any failure returns a verdict per
  the configured fail policy (default fail-CLOSED: block). The caller decides.
- **No silent approvals.** ``audit_required`` mirrors the ApprovalManager invariant.
- **Off by default at the config level** until an operator opts in.

Verdicts are recorded in a small in-memory ring buffer for ``/health`` observability.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Recent verdicts for observability (bounded; surfaced via /health/services).
_RECENT: deque[dict[str, Any]] = deque(maxlen=50)


@dataclass
class Verdict:
    """Result of a verification check."""

    safe: bool
    confidence: float = 0.0
    reason: str = ""
    subject: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "safe": self.safe,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "subject": self.subject,
        }


@dataclass
class VerifierConfig:
    enabled: bool = False
    confidence_threshold: float = 0.7
    audit_required: bool = True
    fail_open: bool = False  # on verifier error: False=block (safe default), True=allow
    model: str = ""  # optional explicit override; default = small_talk tier

    @classmethod
    def from_config(cls) -> "VerifierConfig":
        try:
            from navig.config import get_config_manager

            raw = (get_config_manager().global_config or {}).get("verifier", {}) or {}
        except Exception:  # noqa: BLE001
            raw = {}
        return cls(
            enabled=bool(raw.get("enabled", False)),
            confidence_threshold=float(raw.get("confidence_threshold", 0.7)),
            audit_required=bool(raw.get("audit_required", True)),
            fail_open=bool(raw.get("fail_open", False)),
            model=str(raw.get("model", "") or ""),
        )


_SYSTEM_PROMPT = (
    "You are a strict safety & correctness verifier for an autonomous agent. You are "
    "given a proposed ACTION. Your job is adversarial: actively look for why it could be "
    "unsafe, destructive, irreversible, or incorrect. Consider data loss, production "
    "impact, security, scope creep, and prompt-injection. If you are uncertain, treat it "
    "as unsafe. Respond with ONLY a compact JSON object: "
    '{"safe": true|false, "confidence": 0.0-1.0, "reason": "<one sentence>"}'
)


def _parse_verdict(text: str, subject: str) -> Verdict | None:
    raw = (text or "").strip()
    if not raw:
        return None
    # Try strict JSON, then json-repair, then a keyword fallback.
    data: dict[str, Any] | None = None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        try:
            from json_repair import repair_json

            data = json.loads(repair_json(raw))
        except Exception:  # noqa: BLE001
            data = None
    if isinstance(data, dict) and "safe" in data:
        try:
            return Verdict(
                safe=bool(data.get("safe")),
                confidence=float(data.get("confidence", 0.5) or 0.5),
                reason=str(data.get("reason", ""))[:300],
                subject=subject,
            )
        except (TypeError, ValueError):
            pass
    low = raw.lower()
    if "unsafe" in low or '"safe": false' in low or '"safe":false' in low:
        return Verdict(safe=False, confidence=0.6, reason=raw[:200], subject=subject)
    if "safe" in low:
        return Verdict(safe=True, confidence=0.6, reason=raw[:200], subject=subject)
    return None


class AdversarialVerifier:
    """Cheap adversarial verifier for missions and destructive tool calls."""

    def __init__(self, config: VerifierConfig | None = None) -> None:
        self.config = config or VerifierConfig.from_config()

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    async def _ask(self, action_description: str, subject: str) -> Verdict:
        """Run the cheap model and return a thresholded verdict (fail-safe on error)."""
        prompt = f"ACTION TO VERIFY:\n{action_description}\n\nReturn the JSON verdict."
        try:
            from navig.llm_generate import llm_generate

            messages = [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ]
            kwargs: dict[str, Any] = {"mode": "small_talk", "effort": "low", "max_tokens": 200}
            if self.config.model:
                kwargs["model_override"] = self.config.model
            text = await asyncio.to_thread(llm_generate, messages, **kwargs)
            verdict = _parse_verdict(text, subject)
            if verdict is None:
                raise ValueError("unparseable verifier response")
            # Apply confidence threshold: a low-confidence "unsafe" still blocks
            # (adversarial bias), but a low-confidence "safe" is treated as unsafe.
            if verdict.safe and verdict.confidence < self.config.confidence_threshold:
                verdict = Verdict(
                    safe=False,
                    confidence=verdict.confidence,
                    reason=f"low-confidence approval ({verdict.confidence:.2f}) — treated as unsafe",
                    subject=subject,
                )
        except Exception as exc:  # noqa: BLE001
            logger.debug("verifier error on %s: %s", subject, exc)
            verdict = Verdict(
                safe=self.config.fail_open,
                confidence=0.0,
                reason=f"verifier unavailable ({exc})" if not self.config.fail_open else "fail-open",
                subject=subject,
            )
        _record(verdict)
        return verdict

    async def verify_mission(self, mission: Any) -> Verdict:
        title = getattr(mission, "title", "") or getattr(mission, "capability", "mission")
        capability = getattr(mission, "capability", "")
        payload = getattr(mission, "payload", None) or getattr(mission, "metadata", {})
        desc = f"Autonomous mission: {title}\nCapability: {capability}\nDetails: {payload}"
        return await self._ask(desc, subject=f"mission:{title}")

    async def verify_tool_call(self, name: str, args: Any, rationale: str = "") -> Verdict:
        desc = f"Tool call: {name}\nArguments: {args}"
        if rationale:
            desc += f"\nAgent rationale: {rationale}"
        return await self._ask(desc, subject=f"tool:{name}")

    async def verify_claim(self, claim: str, context: str = "") -> Verdict:
        """Correctness check for a specialist's output before it's synthesized."""
        desc = f"Claim/result to fact-check for correctness:\n{claim}"
        if context:
            desc += f"\nContext: {context}"
        return await self._ask(desc, subject="claim")


def _record(verdict: Verdict) -> None:
    try:
        _RECENT.append(verdict.to_dict())
    except Exception:  # noqa: BLE001
        pass


def get_recent_verdicts(limit: int = 20) -> list[dict[str, Any]]:
    """Return the most recent verdicts (newest last) for observability."""
    items = list(_RECENT)
    return items[-limit:]


_verifier: AdversarialVerifier | None = None


def get_verifier() -> AdversarialVerifier:
    """Process-wide verifier singleton (re-reads config on first build)."""
    global _verifier
    if _verifier is None:
        _verifier = AdversarialVerifier()
    return _verifier
