from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

_DECISION_POINT_RE = re.compile(
    r"(?:should\s+i\s+continue|want\s+me\s+to\s+continue|shall\s+i\s+continue|proceed\?|continue\?)",
    re.IGNORECASE,
)
_SOFT_CONTINUE_SIGNAL_RE = re.compile(r"\b(next\s+step|proceed|continue)\b", re.IGNORECASE)
_CHOICE_SIGNAL_RE = re.compile(
    r"(?:\bchoose\b|\bwhich\b|\boption\b|\ba\s+or\s+b\b|\byes\s+or\s+no\b)",
    re.IGNORECASE,
)
_WAIT_SIGNAL_RE = re.compile(
    r"(?:\bworking\s+on\b|\bin\s+progress\b|\bprocessing\b|\bhold\s+on\b|\bone\s+moment\b|\bcurrently\s+running\b)",
    re.IGNORECASE,
)
_BLOCKED_SIGNAL_RE = re.compile(
    r"(?:\bblocked\b|\bcannot\b|\bunable\b|\bneed\s+approval\b|\bpermission\s+denied\b|\bmissing\s+information\b)",
    re.IGNORECASE,
)

_PROFILE_DEFAULTS: dict[str, tuple[int, int]] = {
    "conservative": (20, 2),
    "balanced": (10, 3),
    "aggressive": (5, 5),
}

_BUSY_SUPPRESSION_SECONDS_BY_PROFILE: dict[str, dict[str, int]] = {
    "conservative": {"wait": 45, "blocked": 120},
    "balanced": {"wait": 30, "blocked": 90},
    "aggressive": {"wait": 15, "blocked": 60},
}


def normalize_profile_name(value: str | None) -> str:
    profile = (value or "").strip().lower()
    return profile if profile in _PROFILE_DEFAULTS else "conservative"


@dataclass(frozen=True)
class ContinuationPolicy:
    profile: str = "conservative"
    enabled: bool = False
    paused: bool = False
    skip_next: bool = False
    cooldown_seconds: int = 20
    max_turns: int = 2
    turns_used: int = 0
    last_continued_at: str = ""
    dry_run: bool = False


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _to_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def policy_from_context(context: dict[str, Any] | None) -> ContinuationPolicy:
    data = (context or {}).get("continuation", {})
    if not isinstance(data, dict):
        data = {}

    profile = normalize_profile_name(str(data.get("profile") or ""))
    default_cooldown, default_max_turns = _PROFILE_DEFAULTS[profile]

    return ContinuationPolicy(
        profile=profile,
        enabled=_to_bool(data.get("enabled"), False),
        paused=_to_bool(data.get("paused"), False),
        skip_next=_to_bool(data.get("skip_next"), False),
        cooldown_seconds=max(0, _to_int(data.get("cooldown_seconds"), default_cooldown)),
        max_turns=max(0, _to_int(data.get("max_turns"), default_max_turns)),
        turns_used=max(0, _to_int(data.get("turns_used"), 0)),
        last_continued_at=str(data.get("last_continued_at") or ""),
        dry_run=_to_bool(data.get("dry_run"), False),
    )


def policy_to_context(policy: ContinuationPolicy) -> dict[str, Any]:
    return {
        "profile": policy.profile,
        "enabled": policy.enabled,
        "paused": policy.paused,
        "skip_next": policy.skip_next,
        "cooldown_seconds": policy.cooldown_seconds,
        "max_turns": policy.max_turns,
        "turns_used": policy.turns_used,
        "last_continued_at": policy.last_continued_at,
        "dry_run": policy.dry_run,
    }


def merge_policy(
    context: dict[str, Any] | None,
    **updates: Any,
) -> dict[str, Any]:
    current = policy_from_context(context)
    current_data = (context or {}).get("continuation", {})
    has_existing = isinstance(current_data, dict) and bool(current_data)
    profile = normalize_profile_name(str(updates.get("profile", current.profile) or ""))
    default_cooldown, default_max_turns = _PROFILE_DEFAULTS[profile]

    if "cooldown_seconds" in updates:
        cooldown_raw = updates.get("cooldown_seconds")
    else:
        cooldown_raw = current.cooldown_seconds if has_existing else default_cooldown
    if cooldown_raw is None:
        cooldown_raw = default_cooldown

    if "max_turns" in updates:
        max_turns_raw = updates.get("max_turns")
    else:
        max_turns_raw = current.max_turns if has_existing else default_max_turns
    if max_turns_raw is None:
        max_turns_raw = default_max_turns

    next_policy = ContinuationPolicy(
        profile=profile,
        enabled=_to_bool(updates.get("enabled", current.enabled), current.enabled),
        paused=_to_bool(updates.get("paused", current.paused), current.paused),
        skip_next=_to_bool(updates.get("skip_next", current.skip_next), current.skip_next),
        cooldown_seconds=max(
            0,
            _to_int(cooldown_raw, default_cooldown),
        ),
        max_turns=max(0, _to_int(max_turns_raw, default_max_turns)),
        turns_used=max(0, _to_int(updates.get("turns_used", current.turns_used), current.turns_used)),
        last_continued_at=str(updates.get("last_continued_at", current.last_continued_at) or ""),
        dry_run=_to_bool(updates.get("dry_run", current.dry_run), current.dry_run),
    )
    merged = dict(context or {})
    merged["continuation"] = policy_to_context(next_policy)
    return merged


def classify_continuation_state(response_text: str) -> tuple[str, str]:
    """Classify continuation intent with lightweight weighted heuristics.

    Returns `(state, reason)` where state is one of:
    `continue`, `choice`, `wait`, `blocked`, `neutral`.
    """
    text = (response_text or "").strip()
    if not text:
        return "wait", "empty_response"

    continue_score = 0
    choice_score = 0
    wait_score = 0
    blocked_score = 0

    if _DECISION_POINT_RE.search(text):
        continue_score += 3
    if _SOFT_CONTINUE_SIGNAL_RE.search(text) and "?" in text:
        continue_score += 1

    if _CHOICE_SIGNAL_RE.search(text):
        choice_score += 2

    if _WAIT_SIGNAL_RE.search(text):
        wait_score += 2

    if _BLOCKED_SIGNAL_RE.search(text):
        blocked_score += 3

    if blocked_score >= 3 and blocked_score >= continue_score:
        return "blocked", "blocked_signal"
    if continue_score >= 2 and continue_score > max(choice_score, wait_score):
        return "continue", "continue_signal"
    if choice_score >= 2:
        return "choice", "choice_signal"
    if wait_score >= 2:
        return "wait", "wait_signal"
    return "neutral", "low_confidence"


def is_decision_point(response_text: str) -> bool:
    return is_decision_point_for_profile(response_text, "conservative")


def decision_sensitivity_for_profile(profile: str | None) -> str:
    normalized = normalize_profile_name(profile)
    if normalized == "aggressive":
        return "eager"
    if normalized == "balanced":
        return "standard"
    return "strict"


def is_decision_point_for_profile(response_text: str, profile: str | None) -> bool:
    state, _ = classify_continuation_state(response_text)
    if state == "continue":
        return True
    if state in {"choice", "wait", "blocked"}:
        return False

    normalized = normalize_profile_name(profile)
    if normalized == "aggressive":
        text = (response_text or "").strip()
        if "?" in text and _SOFT_CONTINUE_SIGNAL_RE.search(text):
            return True
    return False


def _seconds_since_iso8601(iso_utc: str) -> int:
    if not iso_utc:
        return 10**9
    try:
        dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    except ValueError:
        return 10**9
    now = datetime.now(timezone.utc)
    return int((now - dt.astimezone(timezone.utc)).total_seconds())


def suppression_windows_for_profile(profile: str | None) -> dict[str, int]:
    normalized = normalize_profile_name(profile)
    windows = _BUSY_SUPPRESSION_SECONDS_BY_PROFILE.get(normalized) or _BUSY_SUPPRESSION_SECONDS_BY_PROFILE["conservative"]
    return {
        "wait": int(windows.get("wait", 0)),
        "blocked": int(windows.get("blocked", 0)),
    }


def busy_window_seconds(profile: str | None, classifier_state: str) -> int:
    return suppression_windows_for_profile(profile).get(classifier_state, 0)


def apply_busy_suppression(
    context: dict[str, Any] | None,
    classifier_state: str,
    classifier_reason: str,
    profile: str | None = "conservative",
) -> dict[str, Any]:
    merged = dict(context or {})
    continuation = dict(merged.get("continuation") or {})
    window_seconds = busy_window_seconds(profile, classifier_state)
    if window_seconds <= 0:
        merged["continuation"] = continuation
        return merged

    busy_until = datetime.now(timezone.utc) + timedelta(seconds=window_seconds)
    continuation["busy_until"] = busy_until.isoformat()
    continuation["busy_reason"] = classifier_reason
    continuation["busy_state"] = classifier_state
    continuation["busy_window_seconds"] = window_seconds
    merged["continuation"] = continuation
    return merged


def get_busy_suppression(context: dict[str, Any] | None) -> tuple[bool, str, str]:
    continuation = (context or {}).get("continuation", {})
    if not isinstance(continuation, dict):
        return False, "", ""

    busy_until = str(continuation.get("busy_until") or "")
    if not busy_until:
        return False, "", ""

    if _seconds_since_iso8601(busy_until) < 0:
        return (
            True,
            str(continuation.get("busy_reason") or "busy"),
            busy_until,
        )

    return False, "", busy_until


def should_auto_continue(
    response_text: str,
    policy: ContinuationPolicy,
    context: dict[str, Any] | None = None,
) -> tuple[bool, str]:
    if not policy.enabled:
        return False, "disabled"
    if policy.paused:
        return False, "paused"
    if policy.skip_next:
        return False, "skip_next"
    busy_active, busy_reason, _ = get_busy_suppression(context)
    if busy_active:
        return False, f"busy_suppressed:{busy_reason}"
    if policy.max_turns > 0 and policy.turns_used >= policy.max_turns:
        return False, "max_turns"
    if _seconds_since_iso8601(policy.last_continued_at) < policy.cooldown_seconds:
        return False, "cooldown"
    if not is_decision_point_for_profile(response_text, policy.profile):
        return False, "no_decision_point"
    return True, "ok"


def consume_skip(context: dict[str, Any] | None) -> dict[str, Any]:
    policy = policy_from_context(context)
    if not policy.skip_next:
        return dict(context or {})
    return merge_policy(context, skip_next=False)


def mark_continued(context: dict[str, Any] | None) -> dict[str, Any]:
    policy = policy_from_context(context)
    now_iso = datetime.now(timezone.utc).isoformat()
    return merge_policy(
        context,
        turns_used=policy.turns_used + 1,
        last_continued_at=now_iso,
    )
