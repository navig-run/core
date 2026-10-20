"""One-tap repair for a mode whose model stopped answering.

The heartbeat reports ``Mode big_tasks → anthropic:… — key rejected`` and, until
now, only printed ``navig mode set big_tasks --provider <p> --model <m>`` with the
placeholders left for the operator to fill in. Runtime replies already fall back
(:mod:`navig.llm.model_fallback`), but the CONFIG kept pointing at the dead model,
so every beat re-raised the same alarm and every call paid for a failed attempt
first.

:func:`repair_modes` re-points each mode's PRIMARY at the best model that is
answering right now. The candidate order is the one the runtime fallback already
uses (:func:`model_fallback.rank_healthy`, with ``prefer`` first). Each candidate
is re-probed before it is written, because the liveness cache can be hours old,
and writing a second dead model would trade one alarm for another.

The policy is honoured: a ``never_from`` mode is not switched, and a ``never_to``
target is never picked. The old primary is returned so the caller can show how to
undo the switch.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from typing import Any, Callable

logger = logging.getLogger("navig.llm.mode_repair")

#: Candidates re-probed per mode before giving up. Each probe is one tiny call.
MAX_PROBES = 3


@dataclass
class RepairOutcome:
    mode: str
    ok: bool
    previous: str = ""
    current: str = ""
    reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _candidates(mode: str, provider: str, model: str) -> list[str]:
    from navig.llm import model_fallback as mf

    policy = mf.load_policy()
    raw = [s for s in policy.prefer if mf.valid_spec(s)]
    raw += mf.rank_healthy(provider, model)
    primary = mf.norm_spec(f"{provider}:{model}")
    out: list[str] = []
    for spec in raw:
        n = mf.norm_spec(spec)
        if n == primary or n in out or mf.blocked_target(n, policy):
            continue
        out.append(n)
    return out


def repair_mode(
    mode: str,
    *,
    probe: Callable[..., tuple[str, str]] | None = None,
    persist: bool = True,
) -> RepairOutcome:
    """Point *mode* at a model that answers now. Never raises."""
    try:
        from navig.llm import model_fallback as mf
        from navig.llm.router import CANONICAL_MODES, MODE_ALIASES, get_llm_router

        typed = (mode or "").strip().lower()
        if typed not in CANONICAL_MODES and typed not in MODE_ALIASES:
            return RepairOutcome(mode, False, reason="unknown mode")
        if not mf.fallback_allowed(typed):
            return RepairOutcome(mode, False, reason="llm.fallback.never_from keeps this mode fixed")

        router = get_llm_router()
        canonical = router.resolve_mode(typed)
        cfg = router.modes.get_mode(canonical)
        provider = (getattr(cfg, "provider", "") or "").strip()
        model = (getattr(cfg, "model", "") or "").strip()
        previous = f"{provider}:{model}" if provider and model else ""

        if probe is None:
            from navig.llm.liveness import probe_model as probe

        tried = 0
        for spec in _candidates(canonical, provider, model):
            if tried >= MAX_PROBES:
                break
            tried += 1
            c_provider, _, c_model = spec.partition(":")
            status, detail = probe(c_provider, c_model, timeout=20.0)
            if status != "live":
                logger.info("mode repair: %s skipped (%s: %s)", spec, status, detail)
                continue
            if not router.update_mode(canonical, provider=c_provider, model=c_model):
                return RepairOutcome(canonical, False, previous, reason="router refused the update")
            if persist:
                from navig.config import get_config_manager

                get_config_manager().set_global("llm_router.llm_modes", router.get_all_modes())
            logger.warning("mode repair: %s %s → %s", canonical, previous or "?", spec)
            return RepairOutcome(canonical, True, previous, spec)

        reason = (
            "no other model is answering right now" if tried else
            "no healthy model known yet — add a key for another provider"
        )
        return RepairOutcome(canonical, False, previous, reason=reason)
    except Exception as exc:  # noqa: BLE001 — a repair button must never crash the bot
        logger.warning("mode repair failed for %s: %s", mode, exc)
        return RepairOutcome(mode, False, reason=f"error: {exc}")


def repair_modes(modes: list[str], **kwargs: Any) -> list[RepairOutcome]:
    seen: list[str] = []
    for m in modes:
        m = (m or "").strip().lower()
        if m and m not in seen:
            seen.append(m)
    return [repair_mode(m, **kwargs) for m in seen]


def outcome_text(outcomes: list[RepairOutcome]) -> str:
    """Markdown summary for the chat, with the undo command for each switch."""
    lines: list[str] = []
    for o in outcomes:
        if o.ok:
            lines.append(f"✅ Mode `{o.mode}` → `{o.current}`")
            if o.previous:
                p, _, m = o.previous.partition(":")
                lines.append(f"   Undo: `navig mode set {o.mode} --provider {p} --model {m}`")
        else:
            lines.append(f"⚠️ Mode `{o.mode}` unchanged — {o.reason}")
    return "\n".join(lines) or "Nothing to repair."
