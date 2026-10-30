"""Model fallback POLICY + the record of every switch — "keep answering, and say so".

The dispatch layer already hops to another model when the primary fails
(:mod:`navig.llm.fallback_policy`, :func:`navig.llm.generate._call_with_fallback`,
the agent loop's rotation). Three things were missing, and the operator hit all
three at once when their Claude credential lapsed (``big_tasks`` and ``coding``
both answered 401):

1. **Nowhere to go.** The chain was the mode's configured fallback (default:
   a local Ollama model, dropped when Ollama is not running) plus
   ``agent.fallback_chain`` (unset on most installs) — so a 401 had *nothing*
   behind it while other keyed models were answering fine. This module adds the
   models the liveness cache (:mod:`navig.llm.model_health`) last saw answering,
   ranked same-family/same-tier first, and the operator's ``prefer`` list.
2. **Silent.** A switch was a log line. Each switch is now appended to
   ``<config_dir>/perf/model_fallbacks.jsonl`` and the degraded state is kept in
   ``<config_dir>/perf/model_fallback_state.json``; the first switch for a
   primary pushes a ``model_fallback`` notification (Telegram + deck), repeated
   at most every :data:`RENOTIFY_S` while it stays degraded.
3. **Unmanageable.** Policy lives in config under ``llm.fallback``:

   * ``auto`` (bool, default true) — use healthy models from the liveness cache
   * ``prefer`` (list of ``provider:model``) — tried before the automatic picks
   * ``never_from`` (list of mode names) — these modes never switch model; they
     fail loudly instead (a sibling ACCOUNT of the same model is still allowed —
     that is not a different model)
   * ``never_to`` (list of ``provider`` or ``provider:model``) — never used as a
     fallback

Recovery: when the primary answers again (a live call after its cooldown, the
heartbeat re-check, or a credential change) the degraded entry is cleared, its
cooldown lifted, and — if the operator was told it broke — told once it is back.

Every public function here is best-effort and never raises into the reply path.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger("navig.llm.model_fallback")

NOTIFY_TYPE = "model_fallback"
LEDGER_LOG = "model_fallbacks"  # → <config_dir>/perf/model_fallbacks.jsonl
STATE_FILE = "model_fallback_state.json"
CONFIG_PREFIX = "llm.fallback"

#: While a primary stays degraded, re-notify at most this often.
RENOTIFY_S = 6 * 3600.0
#: Minimum gap between two credential-triggered re-checks.
_RECHECK_MIN_GAP_S = 30.0

MANAGE_HINT = "Manage: Mini App → Settings → AI & Models → Fallbacks · or `navig ai fallback`"

_lock = threading.RLock()
_state_cache: dict[str, Any] = {"path": None, "mtime": None, "data": None}
_last_recheck = [0.0]

# Test seam: when set, notifications go here instead of navig.notify.
_sink: Callable[[str, str, str], None] | None = None
# The event loop of the process that owns notifications (captured the first time a
# switch is noted from inside a running loop — the gateway's). Lets a switch noted
# from a worker thread (run_llm under asyncio.to_thread) deliver on that loop.
_loop: asyncio.AbstractEventLoop | None = None


# ─────────────────────────────────────────────────────────────
# Policy
# ─────────────────────────────────────────────────────────────


@dataclass
class FallbackPolicy:
    auto: bool = True
    prefer: list[str] = field(default_factory=list)
    never_from: list[str] = field(default_factory=list)
    never_to: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [v for v in re.split(r"[,\s]+", value) if v]
    if not isinstance(value, (list, tuple, set)):
        return []
    out: list[str] = []
    for v in value:
        s = str(v).strip()
        if s and s not in out:
            out.append(s)
    return out


def load_policy() -> FallbackPolicy:
    """Read ``llm.fallback.*``. Defaults on any failure (auto on, nothing blocked)."""
    try:
        from navig.config import get_config_manager
        from navig.core.coerce import coerce_bool

        cm = get_config_manager()
        raw = cm.get(CONFIG_PREFIX, None) or {}
        if not isinstance(raw, dict):
            raw = {}
        return FallbackPolicy(
            auto=coerce_bool(raw.get("auto", True), default=True),
            prefer=_str_list(raw.get("prefer")),
            never_from=[m.lower() for m in _str_list(raw.get("never_from"))],
            never_to=[t.lower() for t in _str_list(raw.get("never_to"))],
        )
    except Exception as exc:  # noqa: BLE001
        logger.debug("fallback policy unreadable, using defaults: %s", exc)
        return FallbackPolicy()


def save_policy(
    *,
    auto: bool | None = None,
    prefer: list[str] | None = None,
    never_from: list[str] | None = None,
    never_to: list[str] | None = None,
) -> FallbackPolicy:
    """Write the given policy fields (one dotted ``set_global`` each — the safe
    write that never clobbers a sibling key) and return the policy read back.
    Raises on a failed write: the caller must not report success for it."""
    from navig.config import get_config_manager

    cm = get_config_manager()
    if auto is not None:
        cm.set_global(f"{CONFIG_PREFIX}.auto", bool(auto))
    if prefer is not None:
        cm.set_global(f"{CONFIG_PREFIX}.prefer", _str_list(prefer))
    if never_from is not None:
        cm.set_global(f"{CONFIG_PREFIX}.never_from", [m.lower() for m in _str_list(never_from)])
    if never_to is not None:
        cm.set_global(f"{CONFIG_PREFIX}.never_to", [t.lower() for t in _str_list(never_to)])
    return load_policy()


def valid_spec(spec: str) -> bool:
    """``provider:model`` with both halves present (ollama ids may contain ``:``)."""
    provider, sep, model = (spec or "").strip().partition(":")
    return bool(sep and re.fullmatch(r"[A-Za-z0-9_.\-]+", provider) and model.strip())


def norm_spec(spec: str) -> str:
    """``Provider:model@conn:x`` → ``provider:model`` (first colon splits)."""
    base = (spec or "").split("@conn:", 1)[0].strip()
    provider, _, model = base.partition(":")
    return f"{provider.strip().lower()}:{model.strip()}"


def fallback_allowed(mode: str | None, policy: FallbackPolicy | None = None) -> bool:
    """False when ``mode`` is in ``never_from`` — it must fail loudly instead."""
    policy = policy or load_policy()
    return not (mode and mode.lower() in policy.never_from)


def blocked_target(spec: str, policy: FallbackPolicy | None = None) -> bool:
    """True when ``never_to`` names this candidate's provider or exact model."""
    policy = policy or load_policy()
    if not policy.never_to:
        return False
    n = norm_spec(spec)
    provider = n.split(":", 1)[0]
    return any(t == provider or t == n.lower() for t in policy.never_to)


# ─────────────────────────────────────────────────────────────
# Candidate ranking (from the liveness cache)
# ─────────────────────────────────────────────────────────────

_FAMILIES = ("claude", "gpt", "gemini", "llama", "qwen", "deepseek", "nemotron",
             "mistral", "grok", "kimi", "glm", "minimax", "gemma", "phi", "command")
_SMALL = ("haiku", "mini", "nano", "flash", "lite", "small", "instant", "1b", "3b",
          "7b", "8b", "9b")
_BIG = ("opus", "ultra", "405b", "120b", "235b", "253b", "480b", "super", "large",
        "-pro", "gpt-5", "o3")


def _family(model: str) -> str:
    m = model.lower()
    return next((f for f in _FAMILIES if f in m), "")


def _tier(model: str) -> int:
    m = model.lower()
    if any(s in m for s in _BIG):
        return 3
    if any(s in m for s in _SMALL):
        return 1
    return 2


def rank_healthy(primary_provider: str, primary_model: str) -> list[str]:
    """Healthy models from the liveness cache, best stand-in first.

    The primary's own PROVIDER is excluded: the failures that leave a mode with
    nothing behind it — a rejected key, a billing cap, an account-wide rate
    limit — take every model on that provider down together. (A second account
    of the same provider is handled before this, as a sibling account.)
    Order: same model family (e.g. Claude via OpenRouter) → closest size tier →
    bigger → most recently seen answering. Cooling candidates are skipped.
    """
    from navig.llm import model_health
    from navig.llm.fallback_policy import is_cooling

    pp = (primary_provider or "").lower()
    fam = _family(primary_model)
    tier = _tier(primary_model)
    rows = []
    for r in model_health.healthy():
        if r["provider"] == pp or is_cooling(r["spec"]):
            continue
        same_family = 1 if fam and _family(r["model"]) == fam else 0
        rows.append(((same_family, -abs(_tier(r["model"]) - tier), _tier(r["model"]), r["ts"]),
                     r["spec"]))
    rows.sort(key=lambda x: x[0], reverse=True)
    return [spec for _, spec in rows]


def extra_candidates(
    mode: str | None, primary_provider: str, primary_model: str,
    policy: FallbackPolicy | None = None,
) -> list[str]:
    """What the policy adds after the configured chain: ``prefer``, then (when
    ``auto``) the ranked healthy models. Empty for a ``never_from`` mode."""
    policy = policy or load_policy()
    if not fallback_allowed(mode, policy):
        return []
    out = [s for s in policy.prefer if valid_spec(s)]
    if policy.auto:
        try:
            out += rank_healthy(primary_provider, primary_model)
        except Exception as exc:  # noqa: BLE001
            logger.debug("healthy-candidate ranking skipped: %s", exc)
    return out


def _global_chain() -> list[str]:
    try:
        from navig.llm.generate import _load_fallback_chain

        return _load_fallback_chain()
    except Exception:  # noqa: BLE001
        return []


def policy_chain(
    mode: str | None,
    provider: str,
    model: str,
    *,
    policy: FallbackPolicy | None = None,
    require_reachable: bool = True,
) -> list[str]:
    """The ordered model fallbacks for a mode-routed call, policy applied:
    the mode's configured fallback → ``agent.fallback_chain`` → ``prefer`` →
    healthy models (``auto``). Deduped, primary dropped, ``never_to`` filtered.
    ``[]`` for a ``never_from`` mode. (Sibling accounts are not here — they are
    the same model, and each dispatch path rotates them itself.)"""
    policy = policy or load_policy()
    if not fallback_allowed(mode, policy):
        return []
    raw: list[str] = []
    if mode:
        try:
            from navig.llm.router import get_mode_fallback_spec

            fb = (get_mode_fallback_spec(mode) if require_reachable
                  else get_mode_fallback_spec(mode, require_reachable=False))
            if fb:
                raw.append(fb)
        except Exception as exc:  # noqa: BLE001
            logger.debug("mode fallback lookup failed: %s", exc)
    raw += _global_chain()
    raw += extra_candidates(mode, provider, model, policy)
    primary = norm_spec(f"{provider}:{model}")
    out: list[str] = []
    for spec in raw:
        if not valid_spec(norm_spec(spec)):
            continue
        n = norm_spec(spec)
        if n == primary or n in out or blocked_target(n, policy):
            continue
        out.append(n)
    return out


def primary_unavailable(provider: str, model: str) -> bool:
    """True when every account that could serve ``provider:model`` is cooling
    down — i.e. a call to it now would only repeat a failure we just saw."""
    from navig.llm.fallback_policy import is_cooling

    try:
        from navig.providers.inference import accounts_to_try, list_provider_connections

        conns = list_provider_connections(provider)
        if conns:
            return not accounts_to_try(conns, provider, model, reprobe_when_all_cooling=False)
    except Exception as exc:  # noqa: BLE001
        logger.debug("account cooling lookup failed: %s", exc)
    return is_cooling(f"{provider}:{model}")


# Verdicts that do not heal on their own: a rejected or missing credential, a
# retired model. A busy/slow/unreachable verdict is NOT here — those clear by
# themselves, and the in-process cooldown map already covers a live failure.
_BROKEN_VERDICTS = ("auth", "nokey", "dead")
_BROKEN_VERDICT_MAX_AGE_S = 26 * 3600  # the heartbeat re-probes daily; allow slack


def broken_verdict(provider: str, model: str) -> str | None:
    """The persisted health verdict (``auth``/``nokey``/``dead``) when the last
    probe or real call saw ``provider:model`` broken in a way that will not heal
    by waiting, else ``None``. This is what lets the heartbeat's 401 reroute a
    chat BEFORE its first failed reply: the in-process cooldown map only knows
    about failures THIS process has seen. Cleared on a credential change
    (:func:`on_credentials_changed`) and overwritten by the next live answer."""
    try:
        from navig.llm import model_health

        h = model_health.status_of(f"{provider}:{model}") or {}
        status = h.get("status")
        if status in _BROKEN_VERDICTS and time.time() - float(h.get("ts") or 0) <= _BROKEN_VERDICT_MAX_AGE_S:
            return str(status)
    except Exception as exc:  # noqa: BLE001
        logger.debug("health verdict lookup failed: %s", exc)
    return None


def preemptive_target(mode: str | None, provider: str, model: str) -> str | None:
    """When the primary is known-unavailable (cooling on every account) and the
    policy allows it, the fallback to route to UP FRONT instead of paying for
    another doomed round-trip. ``None`` means "dial the primary"."""
    try:
        from navig.llm.fallback_policy import any_cooling, is_cooling

        known_broken = broken_verdict(provider, model) is not None
        if not known_broken and not any_cooling():  # the normal case: nothing failed
            return None
        policy = load_policy()
        if not fallback_allowed(mode, policy):
            return None
        if not known_broken and not primary_unavailable(provider, model):
            return None

        for spec in policy_chain(mode, provider, model, policy=policy):
            if not is_cooling(spec):
                return spec
    except Exception as exc:  # noqa: BLE001
        logger.debug("preemptive fallback lookup failed: %s", exc)
    return None


# ─────────────────────────────────────────────────────────────
# Degraded state + ledger
# ─────────────────────────────────────────────────────────────


def _state_path() -> Path:
    from navig.platform.paths import config_dir

    return config_dir() / "perf" / STATE_FILE


def _ledger_path() -> Path:
    from navig.platform.paths import config_dir

    return config_dir() / "perf" / f"{LEDGER_LOG}.jsonl"


def _load_state() -> dict[str, Any]:
    path = _state_path()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        return {"degraded": {}}
    with _lock:
        if (_state_cache["path"] == str(path) and _state_cache["mtime"] == mtime
                and _state_cache["data"] is not None):
            return _copy_state(_state_cache["data"])
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        logger.debug("fallback state unreadable: %s", exc)
        data = {}
    if not isinstance(data, dict) or not isinstance(data.get("degraded"), dict):
        data = {"degraded": {}}
    with _lock:
        _state_cache.update(path=str(path), mtime=mtime, data=_copy_state(data))
    return data


def _copy_state(data: dict[str, Any]) -> dict[str, Any]:
    return {"degraded": {k: dict(v) for k, v in (data.get("degraded") or {}).items()
                         if isinstance(v, dict)}}


def _save_state(data: dict[str, Any]) -> None:
    from navig.core.json_io import atomic_write_json

    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_json(data, path, sort_keys=True)
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = None
    with _lock:
        _state_cache.update(path=str(path), mtime=mtime, data=_copy_state(data))


def _ledger(event: str, data: dict[str, Any]) -> None:
    from navig.core.yaml_io import log_shadow_anomaly

    log_shadow_anomaly(LEDGER_LOG, event, data)


def degraded() -> dict[str, dict[str, Any]]:
    """``primary spec → {since, reason, modes, fallback, last_switch, last_notified}``."""
    try:
        return _load_state()["degraded"]
    except Exception:  # noqa: BLE001
        return {}


def history(limit: int = 20) -> list[dict[str, Any]]:
    """The newest ``limit`` ledger rows, newest first, flattened to
    ``{ts, event, mode, from, to, reason}``."""
    import json

    path = _ledger_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in reversed(lines):
        if len(out) >= max(0, limit):
            break
        try:
            row = json.loads(line)
        except Exception:  # noqa: BLE001 — one torn line must not hide the rest
            continue
        if not isinstance(row, dict):
            continue
        data = row.get("data") if isinstance(row.get("data"), dict) else {}
        out.append({"ts": row.get("ts"), "event": row.get("event"), **data})
    return out


# ─────────────────────────────────────────────────────────────
# Human text
# ─────────────────────────────────────────────────────────────

_REASON_TEXT = {
    "auth": "key rejected (auth error)",
    "payment": "out of credits or billing problem (402/403)",
    "rate_limited": "rate-limited (provider quota)",
    "overloaded": "provider overloaded",
    "server_error": "provider erroring (5xx)",
    "timeout": "timing out",
    "dead_model": "model retired by the provider",
    "unknown": "not answering",
    "cooldown": "still failing",
}


def reason_text(reason: str | None) -> str:
    return _REASON_TEXT.get(str(reason or "unknown"), str(reason or "not answering").replace("_", " "))


def friendly_model(spec_or_model: str) -> str:
    """``anthropic:claude-opus-4-8`` → ``Claude Opus 4.8``; ``nvidia:nvidia/
    nemotron-3-super-120b`` → ``Nemotron``; ``openai:gpt-4o`` → ``GPT-4o``."""
    base = (spec_or_model or "").split("@conn:", 1)[0]
    provider, sep, model = base.partition(":")
    if not sep or "/" in provider:
        model = base
    seg = model.rsplit("/", 1)[-1]
    toks = [t for t in re.split(r"[-_\s]+", seg) if t]
    if not toks:
        return model or "?"
    head = toks[0].lower()
    if head == "claude":
        tier = next((t for t in toks[1:] if t.isalpha()), "")
        nums = [t for t in toks[1:] if t.isdigit() and len(t) <= 2]
        return " ".join(x for x in ("Claude", tier.title(), ".".join(nums[:2])) if x)
    if head == "gpt":
        return "GPT-" + "-".join(toks[1:]) if len(toks) > 1 else "GPT"
    return toks[0][:1].upper() + toks[0][1:]


def _short_model(spec: str) -> str:
    """The model id as shown in a notification body (no provider, ≤ 40 chars)."""
    base = norm_spec(spec)
    model = base.split(":", 1)[1] if ":" in base else base
    return model if len(model) <= 40 else model[:39] + "…"


def _fallback_text(primary: str, to: str, modes: list[str], reason: str) -> tuple[str, str]:
    title = f"⚠️ {friendly_model(primary)} isn't answering — using {friendly_model(to)}"
    mode_txt = ", ".join(f"`{m}`" for m in modes) or "`?`"
    body = (
        f"Mode {mode_txt} · {reason_text(reason)}. Replies continue on "
        f"`{_short_model(to)}` until it works again.\n{MANAGE_HINT}"
    )
    if reason == "payment":
        # Out of credit looks exactly like a dead key to the operator, and the
        # only fix is money on the provider account — never a reconnect.
        provider = norm_spec(primary).split(":", 1)[0]
        body += f"\nTop up: `{provider}` account billing — the key itself is fine"
    elif reason == "auth":
        # A rejected credential never fixes itself — say exactly how to fix it.
        try:
            from navig.providers.inference import reconnect_hint

            hint = reconnect_hint(norm_spec(primary).split(":", 1)[0])
        except Exception:  # noqa: BLE001
            hint = None
        body += f"\nReconnect: `{hint or 'navig connect'}`"
    return title, body


def _recovery_text(primary: str, modes: list[str]) -> tuple[str, str]:
    mode_txt = ", ".join(f"`{m}`" for m in modes) or "it"
    verb = "use" if len(modes) > 1 else "uses"
    return f"✅ {friendly_model(primary)} is back", f"{mode_txt} {verb} it again."


# ─────────────────────────────────────────────────────────────
# Notification delivery (never blocks the reply path)
# ─────────────────────────────────────────────────────────────


async def _dispatch(title: str, body: str, priority: str, data: dict[str, Any]) -> None:
    try:
        from navig.notify import dispatch

        await dispatch(NOTIFY_TYPE, title, body, priority=priority, data=data)
    except Exception as exc:  # noqa: BLE001
        logger.warning("model fallback notification not delivered: %s", exc)


def _deliver(title: str, body: str, *, priority: str = "high",
             data: dict[str, Any] | None = None) -> None:
    """Fire-and-forget. Inside a running loop → a GC-safe background task; from a
    worker thread → onto the captured owner loop; with no loop at all (CLI) → a
    short-lived thread. Never raises, never awaits network on the caller."""
    global _loop
    data = data or {}
    try:
        if _sink is not None:
            _sink(NOTIFY_TYPE, title, body)
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is not None:
            _loop = running
            from navig.core.background import spawn

            spawn(_dispatch(title, body, priority, data), name="model-fallback-notify")
            return
        owner = _loop
        if owner is not None and owner.is_running() and not owner.is_closed():
            asyncio.run_coroutine_threadsafe(_dispatch(title, body, priority, data), owner)
            return
        threading.Thread(
            target=lambda: asyncio.run(_dispatch(title, body, priority, data)),
            name="model-fallback-notify",
            daemon=False,
        ).start()
    except Exception as exc:  # noqa: BLE001
        logger.warning("model fallback notification could not be scheduled: %s", exc)


# ─────────────────────────────────────────────────────────────
# Events from the dispatch paths
# ─────────────────────────────────────────────────────────────


def note_fallback(mode: str | None, from_spec: str, to_spec: str, reason: str | None) -> None:
    """A reply was produced by ``to_spec`` because ``from_spec`` could not.

    Writes a ledger row only when something CHANGED (a new degradation, a new
    target, a new mode affected) — a primary that stays down does not add a row
    per message. Notifies the first time, then at most every :data:`RENOTIFY_S`.
    Never raises.
    """
    try:
        primary = norm_spec(from_spec)
        target = norm_spec(to_spec)
        if not valid_spec(primary) or not valid_spec(target) or primary == target:
            return
        mode = (mode or "").strip() or "?"
        reason = str(reason or "unknown")
        if reason in ("cooldown", "unknown"):
            # A pre-emptive switch has no live error to classify — the persisted
            # verdict is the real reason, and "auth" is what earns the reconnect hint.
            v = broken_verdict(*primary.split(":", 1))
            if v:
                reason = "dead_model" if v == "dead" else "auth"
        now = time.time()
        with _lock:
            st = _load_state()
            deg = st["degraded"]
            entry = deg.get(primary)
            new_switch = entry is None
            if entry is None:
                entry = {"since": now, "reason": reason if reason != "cooldown" else "unknown",
                         "modes": [], "fallback": None, "last_switch": None,
                         "last_notified": None}
            if reason != "cooldown":
                entry["reason"] = reason
            if entry.get("fallback") != target:
                entry["fallback"] = target
                new_switch = True
            modes = list(entry.get("modes") or [])
            if mode not in modes:
                modes.append(mode)
                new_switch = True
            entry["modes"] = modes
            if new_switch:
                entry["last_switch"] = now
            last = entry.get("last_notified")
            notify = last is None or now - float(last) >= RENOTIFY_S
            if notify:
                entry["last_notified"] = now
            deg[primary] = entry
            if new_switch or notify:
                _save_state(st)
        if new_switch:
            _ledger("fallback", {"mode": mode, "from": primary, "to": target,
                                 "reason": entry["reason"]})
            logger.warning("model fallback: %s → %s (mode %s, %s)",
                           primary, target, mode, entry["reason"])
        try:
            from navig.llm import model_health

            _tp, _, _tm = target.partition(":")
            model_health.note_live(_tp, _tm)
        except Exception:  # noqa: BLE001
            pass
        if notify:
            title, body = _fallback_text(primary, target, entry["modes"], entry["reason"])
            _deliver(title, body, data={"from": primary, "to": target,
                                        "modes": entry["modes"], "reason": entry["reason"]})
    except Exception as exc:  # noqa: BLE001 — never break the reply path
        logger.debug("note_fallback failed: %s", exc)


_CATEGORY_HEALTH = {
    "auth": "auth", "payment": "auth", "dead_model": "dead",
    "rate_limited": "transient", "overloaded": "transient", "server_error": "transient",
    "timeout": "unreachable",
}


def note_primary_failed(provider: str, model: str, category: str | None) -> None:
    """Record what a real call just observed, so the health dot and the
    candidate ranking stop trusting a model that is failing. Never raises."""
    status_ = _CATEGORY_HEALTH.get(str(category or ""))
    if not status_:
        return
    if str(category) == "rate_limited":
        # A 429 on a REAL call counts toward the sustained rate-limit watch —
        # the watcher that turned the operator's week-long subscription cap
        # (2026-10: silent fast-retries, zero notifications) into a heartbeat
        # issue. A one-off success deliberately does NOT clear it: a capped
        # account answers occasional small requests, and clearing would reset
        # the storm forever. Entries age out by the storm window instead.
        try:
            from navig.llm.rate_limit_watch import note_rate_limited

            note_rate_limited(provider, model)
        except Exception as exc:  # noqa: BLE001
            logger.debug("rate-limit watch note failed: %s", exc)
    try:
        from navig.llm import model_health

        model_health.record(provider, model, status_, reason_text(category), source="observed")
    except Exception as exc:  # noqa: BLE001
        logger.debug("note_primary_failed skipped: %s", exc)


def is_degraded(spec: str) -> bool:
    try:
        return norm_spec(spec) in degraded()
    except Exception:  # noqa: BLE001
        return False


def note_primary_ok(provider: str, model: str) -> None:
    """The primary just answered a real request. Cheap when nothing is degraded
    (a stat of a small file); recovers the primary when it was."""
    try:
        from navig.llm import model_health

        model_health.note_live(provider, model)
        spec = norm_spec(f"{provider}:{model}")
        if spec in degraded():
            recover(spec, source="live call")
    except Exception as exc:  # noqa: BLE001
        logger.debug("note_primary_ok failed: %s", exc)


def recover(spec: str, *, source: str = "probe") -> bool:
    """Mark ``spec`` healthy again: drop it from the degraded state, lift its
    cooldowns, ledger a ``recovered`` row, and — if the operator was told it
    broke — tell them once that it is back. Returns True when it WAS degraded."""
    primary = norm_spec(spec)
    try:
        with _lock:
            st = _load_state()
            entry = st["degraded"].pop(primary, None)
            if entry is None:
                return False
            _save_state(st)
        try:
            from navig.llm.fallback_policy import clear_cooldown

            clear_cooldown(primary)
        except Exception:  # noqa: BLE001
            pass
        try:
            from navig.llm import model_health

            _pp, _, _pm = primary.partition(":")
            model_health.record(_pp, _pm, "live", f"recovered ({source})", source=source)
        except Exception:  # noqa: BLE001
            pass
        modes = list(entry.get("modes") or [])
        _ledger("recovered", {"mode": ",".join(modes), "from": primary,
                              "to": entry.get("fallback"), "reason": source})
        logger.warning("model fallback: %s recovered (%s)", primary, source)
        if entry.get("last_notified") is not None:
            title, body = _recovery_text(primary, modes)
            _deliver(title, body, priority="normal",
                     data={"recovered": primary, "modes": modes, "tidy": "status"})
        return True
    except Exception as exc:  # noqa: BLE001
        logger.debug("recover(%s) failed: %s", primary, exc)
        return False


def recheck_degraded(*, probe: Callable[..., tuple[str, str]] | None = None,
                     timeout: float = 20.0) -> list[str]:
    """Probe each degraded primary once (a 1-token call); recover the ones that
    answer. Returns the recovered specs. Blocking — run it off the event loop."""
    if probe is None:
        from navig.llm.liveness import probe_model as probe
    recovered: list[str] = []
    for spec in list(degraded()):
        provider, _, model = spec.partition(":")
        try:
            status, _detail = probe(provider, model, timeout=timeout)
        except Exception as exc:  # noqa: BLE001
            logger.debug("re-check of %s failed: %s", spec, exc)
            continue
        if status == "live" and recover(spec, source="re-check"):
            recovered.append(spec)
    return recovered


def on_credentials_changed(*, provider: str | None = None, routable: bool = False) -> None:
    """A provider credential was added or replaced. If anything is degraded, lift
    those cooldowns (so the next call tries the primary at once) and re-check in
    the background. A no-op — no thread, no I/O beyond a stat — when nothing is."""
    try:
        # A stored "key rejected" verdict describes the OLD credential: drop it,
        # or the chat pre-check keeps routing away from a login that now works —
        # but ONLY for the provider whose connection now WORKS. The store also
        # writes on a token refresh and on a revalidation that lands on
        # needs_reauth; forgetting on those erased the verdict that was
        # rerouting chats (seen live 2026-10-10: a planted 401 was gone before
        # the next message arrived).
        if provider and routable:
            try:
                from navig.llm import model_health

                model_health.forget(("auth", "nokey"), provider=provider)
            except Exception:  # noqa: BLE001
                pass
        bad = list(degraded())
        if not bad:
            return
        from navig.llm.fallback_policy import clear_cooldown

        for spec in bad:
            clear_cooldown(spec)
        now = time.time()
        if now - _last_recheck[0] < _RECHECK_MIN_GAP_S:
            return
        _last_recheck[0] = now
        threading.Thread(target=recheck_degraded, name="model-fallback-recheck",
                         daemon=True).start()
    except Exception as exc:  # noqa: BLE001
        logger.debug("on_credentials_changed failed: %s", exc)


# ─────────────────────────────────────────────────────────────
# Mode fallback writes + the status snapshot (CLI / deck / doctor)
# ─────────────────────────────────────────────────────────────


def set_mode_fallback(mode: str, spec: str | None) -> dict[str, Any]:
    """Point ``llm_modes.<mode>`` at ``spec`` (``None``/``""`` clears it) through
    the router — the same object ``navig mode set`` updates — and persist the
    whole modes block the way that command does. Raises ``ValueError`` on bad
    input and lets a failed save raise."""
    from navig.config import get_config_manager
    from navig.llm.router import CANONICAL_MODES, MODE_ALIASES, get_llm_router

    typed = (mode or "").strip().lower()
    if typed not in CANONICAL_MODES and typed not in MODE_ALIASES:
        raise ValueError(f"unknown mode: {mode!r} (modes: {', '.join(sorted(CANONICAL_MODES))})")
    router = get_llm_router()
    canonical = router.resolve_mode(typed)
    if spec:
        if not valid_spec(spec):
            raise ValueError(f"expected provider:model, got {spec!r}")
        provider, _, model = spec.strip().partition(":")
        ok = router.update_mode(canonical, fallback_provider=provider, fallback_model=model)
    else:
        ok = router.update_mode(canonical, fallback_provider="", fallback_model="")
    if not ok:
        raise ValueError(f"unknown mode: {mode!r}")
    get_config_manager().set_global("llm_router.llm_modes", router.get_all_modes())
    return router.get_all_modes().get(canonical, {})


def _configured_fallback(mode: str) -> str | None:
    try:
        from navig.llm.router import get_mode_fallback_spec

        return get_mode_fallback_spec(mode, require_reachable=False)
    except Exception:  # noqa: BLE001
        return None


def status(*, history_limit: int = 20, include_candidates: bool = True) -> dict[str, Any]:
    """Everything the CLI table, the deck page and doctor need, in one shape."""
    from navig.llm import model_health
    from navig.llm.router import CANONICAL_MODES, resolve_llm

    policy = load_policy()
    deg = degraded()
    hist = history(history_limit)
    last_switch_by_mode: dict[str, float] = {}
    for row in hist:
        if row.get("event") != "fallback":
            continue
        m = str(row.get("mode") or "")
        if m and m not in last_switch_by_mode and row.get("ts"):
            last_switch_by_mode[m] = float(row["ts"])

    modes: list[dict[str, Any]] = []
    for mode in sorted(CANONICAL_MODES):
        try:
            r = resolve_llm(mode=mode)
            primary = norm_spec(f"{r.provider}:{r.model}")
        except Exception as exc:  # noqa: BLE001
            modes.append({"mode": mode, "primary": None, "error": str(exc)[:120]})
            continue
        h = model_health.status_of(primary) or {}
        d = deg.get(primary)
        never = mode in policy.never_from
        effective = None
        if not never:
            if d and d.get("fallback"):
                effective = d["fallback"]
            else:
                chain = policy_chain(mode, r.provider, r.model, policy=policy)
                effective = chain[0] if chain else None
        if d:
            health = "degraded"
        else:
            health = h.get("status") or "unknown"
        modes.append({
            "mode": mode,
            "primary": primary,
            "primary_label": friendly_model(primary),
            "health": health,
            "health_detail": (reason_text(d.get("reason")) if d else h.get("detail", "")),
            "health_ts": h.get("ts"),
            "configured_fallback": _configured_fallback(mode),
            "effective_fallback": effective,
            "never": never,
            "degraded": d,
            "last_switch": (d or {}).get("last_switch") or last_switch_by_mode.get(mode),
        })
    out: dict[str, Any] = {
        "modes": modes,
        "policy": policy.to_dict(),
        "degraded": deg,
        "history": hist,
    }
    if include_candidates:
        out["candidates"] = [
            {"spec": r["spec"], "label": friendly_model(r["spec"]), "ts": r["ts"],
             "source": r["source"]}
            for r in model_health.healthy()
            if not blocked_target(r["spec"], policy)
        ]
    return out


def reset_for_tests() -> None:
    """Forget in-process caches and the test sink (files are left alone)."""
    global _sink, _loop
    with _lock:
        _state_cache.update(path=None, mtime=None, data=None)
    _last_recheck[0] = 0.0
    _sink = None
    _loop = None
