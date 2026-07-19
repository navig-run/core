"""
LLM mode liveness — probe each routing mode's model to catch DEAD/EOL models
(the provider retired it: 410 Gone / 404 Not Found) before they break a real run.

Shared by:
  * ``navig mode doctor`` (CLI, on-demand)                    — commands/mode.py
  * the heartbeat runner (scheduled, throttled daily)         — heartbeat/runner.py
  * an offline CI guard (no live calls)                       — tests/llm/test_mode_liveness_guard.py

Two layers:
  * ``RETIRED_MODELS`` — a static denylist of model ids we've SEEN retired, so the
    offline CI guard fails fast if a shipped default ever points at one again.
  * ``probe_model`` / ``probe_modes`` — a real 1-token call through the normal
    (connection-aware) dispatch, classifying the outcome. This is the only way to
    catch a provider retiring a model out from under a working config.
"""

from __future__ import annotations

from typing import Any

# Model ids confirmed retired by their provider (kept as substrings so a
# provider-prefixed id still matches). Add to this as EOLs are discovered — the
# offline CI guard asserts no shipped default mode references any of these.
RETIRED_MODELS: frozenset[str] = frozenset({
    "qwen/qwen3-coder-480b-a35b-instruct",   # NVIDIA — EOL 2026-06-11 (HTTP 410)
    "qwen/qwen2.5-coder-32b-instruct",       # NVIDIA — HTTP 410
    "deepseek-ai/deepseek-r1",               # NVIDIA — HTTP 404 (id gone)
})

# Statuses a probe can return, worst → best for sorting/among a set.
STATUS_ORDER = ("dead", "unreachable", "auth", "error", "nokey", "live")


def is_retired(model: str | None) -> bool:
    """True if *model* matches a known-retired id (exact or provider-prefixed)."""
    if not model:
        return False
    m = model.strip()
    return any(r == m or m.endswith("/" + r) or m == r.split("/")[-1] for r in RETIRED_MODELS)


def classify_probe_error(exc: Exception) -> tuple[str, str]:
    """Map a probe exception to ``(status, human_detail)``.

    status ∈ dead | auth | nokey | unreachable | error.
    """
    msg = str(exc).lower()
    if any(s in msg for s in ("410", "end of life", "no longer available", "reached its end")):
        return "dead", "model retired (410 EOL)"
    # A 404 from a known chat/completions endpoint means the model id is gone.
    if "404" in msg:
        return "dead", "model not found (404)"
    if "no credential" in msg or "no api key" in msg:
        return "nokey", "no credential configured"
    if any(s in msg for s in ("401", "unauthorized", "authorization", "invalid api key")):
        return "auth", "auth failed (401 — key/token invalid or expired)"
    if "403" in msg:
        return "auth", "forbidden (403)"
    if any(s in msg for s in ("timed out", "timeout", "read operation", "connect",
                              "econnrefused", "unreachable")):
        return "unreachable", "endpoint unreachable / timed out"
    return "error", str(exc)[:90]


def probe_model(provider: str, model: str, *, timeout: float = 25.0) -> tuple[str, str]:
    """Run a 1-token completion through the real (connection-aware) dispatch.
    Returns ``(status, detail)`` where status ∈ live|dead|auth|nokey|unreachable|error.

    Fast-fails to ``dead`` for a known-retired id without spending a call.
    """
    if is_retired(model):
        return "dead", "known-retired model (denylisted)"
    from navig.llm.generate import llm_generate

    try:
        llm_generate(
            [{"role": "user", "content": "ping"}],
            model_override=f"{provider}:{model}",
            max_tokens=1,
            temperature=0.0,
            timeout=timeout,
        )
        return "live", "ok"
    except Exception as exc:  # noqa: BLE001 — classify every failure
        return classify_probe_error(exc)


def probe_modes(modes: list[str] | None = None, *, timeout: float = 25.0) -> list[dict[str, Any]]:
    """Probe each mode's ACTUALLY-ROUTED model (via ``resolve_llm`` — so the
    small_talk fast-chat override, default_provider, and fallbacks are reflected).

    Returns a list of ``{mode, provider, model, status, detail}`` dicts.
    """
    from navig.llm.router import CANONICAL_MODES, resolve_llm

    targets = modes or sorted(CANONICAL_MODES)
    out: list[dict[str, Any]] = []
    for m in targets:
        try:
            cfg = resolve_llm(mode=m)
            provider, model = cfg.provider, cfg.model
        except Exception as exc:  # noqa: BLE001 — a broken route is itself a finding
            out.append({"mode": m, "provider": "?", "model": "?",
                        "status": "error", "detail": f"resolve failed: {str(exc)[:70]}"})
            continue
        status, detail = probe_model(provider, model, timeout=timeout)
        out.append({"mode": m, "provider": provider, "model": model,
                    "status": status, "detail": detail})
    return out


def dead_modes(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter probe results to the ones that would break a run (dead/unreachable/auth)."""
    return [r for r in results if r["status"] in ("dead", "unreachable", "auth")]
