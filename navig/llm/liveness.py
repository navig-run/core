"""
LLM mode liveness — probe each routing mode's model to catch DEAD/EOL models
(the provider retired it: 410 Gone / 404 Not Found) before they break a real run.

Shared by:
  * ``navig mode doctor`` (CLI, on-demand)                    — commands/mode.py
  * ``navig ai models --check`` (CLI, the CATALOG sweep)      — commands/ai.py
  * the heartbeat runner (scheduled, throttled daily)         — heartbeat/runner.py
  * an offline CI guard (no live calls)                       — tests/llm/test_mode_liveness_guard.py

Two SCOPES, and the difference matters: ``probe_routes`` probes what an install
ROUTES to (modes, fallbacks, tiers), ``probe_catalog`` probes what it can
SUBSTITUTE IN — the manifest and ``BUILTIN_PROVIDERS`` ids behind every
credential probe and every ``models[0]`` default. A retirement shows up in the
catalog long before it reaches a route, and nothing looked there until a sweep
found the table's first row dead for all four keyed providers.

Two layers:
  * ``RETIRED_MODELS`` — a static denylist of model ids we've SEEN retired, so the
    offline CI guard fails fast if a shipped default ever points at one again.
  * ``probe_model`` / ``probe_modes`` — a real 1-token call through the normal
    (connection-aware) dispatch, classifying the outcome. This is the only way to
    catch a provider retiring a model out from under a working config.
"""

from __future__ import annotations

from typing import Any

# Models confirmed retired, keyed ``provider:model`` — RETIREMENT IS
# PROVIDER-SPECIFIC. `deepseek-ai/deepseek-r1` being 404 on NVIDIA says nothing
# about `deepseek-r1` on GitHub Models, and this list is consulted by
# `probe_model`, which FAST-FAILS a match to "dead" without spending a call. An
# over-broad entry therefore marks a live model dead on a provider nobody tested,
# and "dead" is the verdict that raises a [HIGH] health issue and an approval
# prompt.
#
# The previous shape was a bare model id matched three ways, including "equals
# the last path segment of a retired id". That produced two real collisions
# against shipped manifests — github_models `deepseek-r1` and qwen
# `qwen2.5-coder-32b-instruct` — which had to be pinned as knowingly accepted.
# Keying on the provider removes the category instead of documenting it.
#
# Split on the FIRST colon only: ollama ids legitimately contain one
# (`ollama:qwen2.5:3b-instruct`).
# Add to this as EOLs are discovered — the offline CI guard asserts no shipped
# default mode, and no provider manifest, references any of these.
RETIRED_MODELS: frozenset[str] = frozenset({
    "nvidia:qwen/qwen3-coder-480b-a35b-instruct",   # NVIDIA — EOL 2026-06-11 (HTTP 410)
    "nvidia:qwen/qwen2.5-coder-32b-instruct",       # NVIDIA — HTTP 410
    "nvidia:deepseek-ai/deepseek-r1",               # NVIDIA — HTTP 404 (id gone)
    # Audited 2026-09-08 by calling each id. NVIDIA retired four Llama ids on a
    # single day, 2026-08-26, which is what broke this install's routing.
    "nvidia:meta/llama-3.1-8b-instruct",            # NVIDIA — 410, EOL 2026-08-26
    "nvidia:meta/llama-3.1-70b-instruct",           # NVIDIA — 410, EOL 2026-08-26
    "nvidia:meta/llama-3.1-405b-instruct",          # NVIDIA — 410
    "nvidia:meta/llama-3.3-70b-instruct",           # NVIDIA — 410 (was the substitution default)
    "nvidia:nvidia/llama-3.3-nemotron-super-49b-v1",  # NVIDIA — gone from catalog + 404
    "openrouter:anthropic/claude-3-7-sonnet",           # OpenRouter — 404 (was its first entry)
    "openrouter:anthropic/claude-3-5-sonnet",           # OpenRouter — withdrawn
    "openrouter:anthropic/claude-3-5-haiku",            # OpenRouter — withdrawn
    "openrouter:x-ai/grok-3-beta",                      # OpenRouter — withdrawn
    "openrouter:x-ai/grok-3-mini-beta",                 # OpenRouter — withdrawn
    "openrouter:qwen/qwq-32b",                          # OpenRouter — 404
    "xai:grok-2-1212",                           # xAI — HTTP 400, model removed
    "xai:grok-2-vision-1212",                    # xAI — HTTP 400, model removed
    "openai:o1-mini",                               # OpenAI — 404 (control: gpt-4.1 live)
    # 2026-09-19: these two led BUILTIN_PROVIDERS (the table every credential
    # probe read `models[0]` from) while the guard below covered only modes and
    # manifests. Confirmed twice each with a live control on the same key.
    "openai:gpt-4-turbo-preview",                   # OpenAI — 404 (control: gpt-4o-mini live)
    "openrouter:anthropic/claude-3.5-sonnet",       # OpenRouter — 404 "No endpoints found" (control: openai/gpt-4o live)
    # Same audit, the rest of the table: every remaining row of the four keyed
    # providers was called; these three answered 404 four times each.
    "openrouter:google/gemini-pro-1.5",             # OpenRouter — 404 (control: openai/gpt-4o live)
    "nvidia:mistralai/mistral-7b-instruct-v0.3",    # NVIDIA — 404 (control: nemotron-3-super live)
    "nvidia:nvidia/llama-3.1-nemotron-70b-instruct",  # NVIDIA — 404 (control: nemotron-3-super live)
    # Retired 2026-09-09, ~24h after it was selected as a live replacement —
    # confirmed twice with an explicit end-of-life message against a control
    # returning 200. The clearest demonstration yet of why this list rots.
    "nvidia:minimaxai/minimax-m3",                  # NVIDIA — 410, EOL 2026-09-09
    # ⚠ groq, swept 2026-09-26: ALL ELEVEN shipped manifest ids were gone in one
    # audit — the clearest case yet that a hand-maintained catalog rots wholesale,
    # not one id at a time. 4 answered 404; the other 7 answered **400 "has been
    # decommissioned"**, a shape `classify_probe_error` could not read until this
    # sweep, so they were filed as unclassified `error` and the heartbeat raised
    # nothing. Controls on the same key: openai/gpt-oss-120b and qwen/qwen3.8-27b live.
    "groq:llama-3.3-70b-versatile",                 # groq — 404 (was models[0]: probe + substitution default)
    "groq:llama-3.1-8b-instant",                    # groq — 404
    "groq:qwen2.5-72b-instruct",                    # groq — 404
    "groq:compound-beta",                           # groq — 404
    "groq:llama-3.3-70b-specdec",                   # groq — 400 decommissioned
    "groq:llama3-70b-8192",                         # groq — 400 decommissioned
    "groq:mixtral-8x7b-32768",                      # groq — 400 decommissioned (was the table's 2nd row)
    "groq:deepseek-r1-distill-llama-70b",           # groq — 400 decommissioned
    "groq:deepseek-r1-distill-qwen-32b",            # groq — 400 decommissioned
    "groq:qwen-qwq-32b",                            # groq — 400 decommissioned
    "groq:gemma2-9b-it",                            # groq — 400 decommissioned
    # 2026-09-26, the second sweep — now through the DISPATCH credential, which
    # reaches a Claude SUBSCRIPTION (an OAuth connection with no API key) that the
    # first sweep skipped. ALL FOUR anthropic manifest ids were gone, plus the ids
    # the router's fallback tables named. Controls on the same credential:
    # claude-opus-4-8, claude-sonnet-4-6, claude-haiku-4-5 live.
    "anthropic:claude-3-7-sonnet-20250219",         # anthropic — 404 (was models[0])
    "anthropic:claude-3-5-sonnet-20241022",         # anthropic — 410 EOL
    "anthropic:claude-3-5-haiku-20241022",          # anthropic — 410 EOL (was the router's _default)
    "anthropic:claude-3-opus-20240229",             # anthropic — 404
    "anthropic:claude-sonnet-4-20250514",           # anthropic — 404 (router big_tasks/coding/research)
    "anthropic:claude-haiku-3-20250422",            # anthropic — 404 (router small_talk/summarize)
    "groq:llama-3.1-70b-versatile",                 # groq — 400 decommissioned (router _default)
    "xai:grok-2-latest",                            # xAI — 404 (router _default; control grok-3 live)
})

# Provider-side failures that say nothing about the model or the credential —
# the provider simply could not serve THIS call. Checked BEFORE the unreachable
# branch, because "504 Gateway Timeout" contains "timeout" and would otherwise
# be classified `unreachable` — which `dead_modes()` reports as a HIGH health
# issue, and the gateway turns a HIGH health issue into an approval prompt. A
# blip upstream must not manufacture a "your model is dead" alarm.
# A provider can retire a model behind HTTP **400**, not 404/410 — measured
# 2026-09-26 on groq, which answers `400 The model \`X\` has been decommissioned
# and is no longer supported` for SEVEN of the eleven ids its manifest shipped.
# None of the EOL markers above matches ("no longer SUPPORTED" is not "no longer
# available"), and 400 is not in the 404/410 branch, so all seven landed in the
# catch-all `error` — which `dead_modes()` does not report, so the heartbeat
# raised nothing for a provider whose entire catalog was gone.
#
# ⚠ 400 alone must NEVER mean dead: it is also the code for a malformed request,
# a bad parameter, an oversized prompt. The verdict requires the message to name
# the MODEL *and* carry a retirement phrase, and it is confirmed by a second call
# like the 404 — a destructive verdict is earned twice.
_RETIRED_PHRASES = (
    "decommissioned",
    "no longer supported",
    "has been retired",
    "no longer accessible",
)
_TRANSIENT_MARKERS = (
    "429", "500", "502", "503", "504",
    "too many requests", "rate limit", "ratelimit",
    "internal server error", "service unavailable", "bad gateway", "gateway timeout",
    "overloaded",  # anthropic: overloaded_error
)
# ⚠ "500" was missing here while present in the connect-path taxonomy
# (drivers/native.py) — the two had drifted. Measured 2026-09-15 on
# mistralai/mistral-nemotron: 8 probes → 5x 200, 1x 500, 1x 502, 1x timeout.
# A 500 says nothing about the model or the credential, exactly like a 502.
# `tests/llm/test_liveness_transient.py` now pins that both taxonomies treat the
# same 5xx codes as non-fatal, so they cannot drift apart again.

# One retry is what turns a false red into a true green on a busy free tier:
# NVIDIA NIM's own manifest describes a "40 RPM free tier", and `probe_modes`
# fires one call per mode back-to-back on top of whatever the agent is doing.
_TRANSIENT_RETRIES = 1
_TRANSIENT_RETRY_DELAY_S = 2.0


def retired_model_ids(provider: str | None = None) -> set[str]:
    """The retired model ids, for *provider* when named, else across all of them."""
    want = (provider or "").strip().lower()
    out: set[str] = set()
    for entry in RETIRED_MODELS:
        prov, _, model = entry.partition(":")  # first colon only — ollama ids have one
        if not want or prov == want:
            out.add(model)
    return out


def is_retired(model: str | None, provider: str | None = None) -> bool:
    """True if *model* is a known-retired id — on *provider* when one is named.

    Matching is EXACT. The old loose forms ("ends with /<id>", "equals the last
    path segment") were what let a model retired on one provider be reported dead
    on another that still serves it.

    Omitting *provider* asks "retired on ANY provider", which is the right
    question for a shipped DEFAULT — we do not want to ship a default that is
    dead anywhere — but it is deliberately not what `probe_model` asks.
    """
    if not model:
        return False
    return model.strip() in retired_model_ids(provider)


# Reaching the output cap is PROOF OF LIFE: the model accepted the request and
# generated, it just could not finish inside a 1-token budget. OpenAI's reasoning
# models (o-series, gpt-5) spend tokens thinking before emitting anything, so they
# ALWAYS hit this on a liveness probe — measured: `probe_model("openai", "o3")`
# returned `error` for a model that answers normally with a larger budget.
# Raising the probe budget would fix today's models and rot for tomorrow's; reading
# the provider's own "you hit the cap" as success cannot.
_OUTPUT_LIMIT_MARKERS = (
    "output limit was reached",
    "max_tokens or model output limit",
    "please try again with higher max_tokens",
)


def hit_output_limit(exc: Exception) -> bool:
    """True when the call failed ONLY because the token budget was too small."""
    msg = str(exc).lower()
    return any(s in msg for s in _OUTPUT_LIMIT_MARKERS)


def _worth_retrying(status: str, detail: str) -> bool:
    """Should this verdict be confirmed by a second call before we believe it?

    ``dead`` is the DESTRUCTIVE verdict — it is what `dead_modes()` turns into a
    ``[HIGH]`` heartbeat issue and the gateway turns into an approval prompt, and
    it is the verdict that justifies denylisting a model. A bare **404 is not
    reliable enough to earn it on one call**: measured 2026-09-08, NVIDIA
    answered 404 once for `nvidia/nemotron-3-super-120b-a12b` and then 200 five
    times in a row for the same id.

    A genuine 404 survives the retry — the four withdrawn ids checked the same
    way answered 404 (or 410) 5/5. **410 is left definitive**: it carries an
    explicit end-of-life message and was likewise stable 5/5.

    ⚠ ``unreachable`` is here too, and it is NOT only about connectivity: a plain
    socket TIMEOUT lands in it, and `dead_modes()` reports `unreachable` as a
    ``[HIGH]`` issue — so one slow call became an approval prompt. Measured
    2026-09-09: 12 consecutive probes of a HEALTHY NVIDIA model returned **11x
    200 and 1x TimeoutError**. A genuinely unreachable endpoint is deterministic
    and survives the retry — ollama that is not running refuses the connection
    both times, which is how dead fallbacks are still detected.

    ⚠ ``slow`` is retried for the same reason as ``unreachable`` — a cold model
    warms up (measured: 107 s then 43 s for the same NVIDIA id) — but unlike
    ``unreachable`` it is NOT a defect either way, so the retry only sharpens the
    detail.
    """
    return (
        status in ("transient", "unreachable", "slow")
        # A retirement served as 400 is earned twice like the 404, for the same
        # reason: 400 is a crowded code and a transient one would be destructive.
        or (status == "dead" and ("404" in detail or "decommissioned" in detail))
    )


def classify_probe_error(exc: Exception) -> tuple[str, str]:
    """Map a probe exception to ``(status, human_detail)``.

    status ∈ dead | auth | nokey | transient | unreachable | error.
    """
    msg = str(exc).lower()
    if any(s in msg for s in ("410", "end of life", "no longer available", "reached its end")):
        return "dead", "model retired (410 EOL)"
    # A 404 from a known chat/completions endpoint means the model id is gone.
    if "404" in msg:
        return "dead", "model not found (404)"
    # A retirement served as 400 (groq). Both halves are required — see _RETIRED_PHRASES.
    if "model" in msg and any(s_ in msg for s_ in _RETIRED_PHRASES):
        return "dead", "model retired (provider says decommissioned)"
    if "no credential" in msg or "no api key" in msg:
        return "nokey", "no credential configured"
    if any(s in msg for s in ("401", "unauthorized", "authorization", "invalid api key")):
        return "auth", "auth failed (401 — key/token invalid or expired)"
    if "403" in msg:
        return "auth", "forbidden (403)"
    # Before `unreachable` on purpose — see _TRANSIENT_MARKERS.
    if any(s in msg for s in _TRANSIENT_MARKERS):
        return "transient", "provider busy / rate-limited (retried) — not a config problem"
    # A READ timeout is not an unreachable endpoint — the connection SUCCEEDED
    # and the request was accepted; the model is merely slow to answer. Measured
    # 2026-09-26: two NVIDIA ids reported `unreachable` twice each at a 40 s cap
    # and then answered `live` in 107 s / 75 s cold, 43 s / 104 s warm. Because
    # `dead_modes()` reports `unreachable` as a [HIGH] issue and the gateway
    # turns a HIGH issue into an approval prompt, a cold model paged the
    # operator. The distinction is only legible because #1503 kept the exception
    # TYPE in the message (`ReadTimeout` vs `ConnectError`) — both used to
    # stringify to nothing.
    #
    # ⚠ Order matters: ConnectTimeout contains BOTH "connect" and "timeout" and
    # is genuinely unreachable, so this branch matches the READ shapes only.
    if any(s in msg for s in ("readtimeout", "read timeout", "read operation timed out",
                              "readerror")):
        return "slow", "model answered the connection but not in time (slow/cold, not gone)"
    if any(s in msg for s in ("timed out", "timeout", "read operation", "connect",
                              "econnrefused", "unreachable")):
        return "unreachable", "endpoint unreachable / timed out"
    return "error", str(exc)[:90]


def probe_model(provider: str, model: str, *, timeout: float = 25.0) -> tuple[str, str]:
    """Run a 1-token completion through the real (connection-aware) dispatch.
    Returns ``(status, detail)`` where status ∈
    live|dead|auth|nokey|transient|slow|unreachable|error.

    Fast-fails to ``dead`` for a known-retired id without spending a call, and
    retries once before believing a verdict that ``_worth_retrying`` flags — a
    transient provider failure, or a bare 404 — so neither a blip on a
    rate-limited free tier nor a one-off 404 is reported as a broken model.
    """
    # Scoped to THIS provider: a model retired on one is routinely alive on
    # another, and this branch skips the call entirely, so a false match is
    # unfalsifiable — it reports "dead" having never asked.
    if is_retired(model, provider):
        return "dead", f"known-retired model on {provider} (denylisted)"
    import time  # noqa: PLC0415 — keep this module import-cheap

    from navig.llm.generate import llm_generate  # noqa: PLC0415

    outcome: tuple[str, str] = ("error", "probe did not run")
    for attempt in range(_TRANSIENT_RETRIES + 1):
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
            if hit_output_limit(exc):
                return "live", "ok (answered, hit the 1-token probe budget)"
            outcome = classify_probe_error(exc)
            if not _worth_retrying(*outcome) or attempt >= _TRANSIENT_RETRIES:
                return outcome
            time.sleep(_TRANSIENT_RETRY_DELAY_S)
    return outcome


def _configured_fallbacks() -> list[tuple[str, str, str]]:
    """``(mode, provider, model)`` for each mode that declares a fallback.

    A fallback is exercised ONLY at the moment its primary fails — i.e. exactly
    when you cannot afford it to be dead too. Nothing probed them: the operator's
    ``big_tasks`` fell back to ``ollama:qwen2.5:7b-instruct`` with no ollama
    running, and that was invisible until the primary's credential lapsed.
    """
    from navig.llm.router import CANONICAL_MODES, get_llm_router  # noqa: PLC0415

    out: list[tuple[str, str, str]] = []
    try:
        router = get_llm_router()
    except Exception:  # noqa: BLE001 — a missing router is not this function's problem
        return out
    for name in sorted(CANONICAL_MODES):
        try:
            cfg = router.modes.get_mode(name)
        except Exception:  # noqa: BLE001
            continue
        if cfg is None:
            continue
        model = (getattr(cfg, "fallback_model", "") or "").strip()
        if not model:
            continue
        # An empty fallback_provider means "same provider as the primary".
        provider = (getattr(cfg, "fallback_provider", "") or getattr(cfg, "provider", "")).strip()
        if provider:
            out.append((name, provider, model))
    return out


def _configured_tiers() -> list[tuple[str, str, str]]:
    """``(tier, provider, model)`` for the hybrid routing slots.

    These were covered by NOTHING. All three of the operator's pointed at models
    that answered 410 GONE, `navig mode doctor` never looked at them, and
    `navig mode route set` reported success while a stale registry substituted a
    dead default underneath (#1322/#1342).
    """
    from navig.agent.model_router import RoutingConfig  # noqa: PLC0415
    from navig.config import get_config_manager  # noqa: PLC0415

    try:
        # get_global_config(), never _load_global_config(): the latter returns the
        # PYDANTIC view and silently drops every key the schema does not declare.
        global_cfg = get_config_manager().get_global_config() or {}
    except Exception:  # noqa: BLE001
        return []
    ai_cfg = global_cfg.get("ai", {}) or {}
    merged = dict(ai_cfg.get("routing", {}) or global_cfg.get("ai_routing", {}) or {})
    if "models" in ai_cfg:
        merged["models"] = ai_cfg["models"]
    if not merged:
        return []
    try:
        cfg = RoutingConfig.from_dict(merged, global_cfg=global_cfg)
    except Exception:  # noqa: BLE001
        return []
    out: list[tuple[str, str, str]] = []
    for tier in ("small", "big", "coder_big"):
        slot = getattr(cfg, tier, None)
        provider = (getattr(slot, "provider", "") or "").strip()
        model = (getattr(slot, "model", "") or "").strip()
        if provider and model:
            out.append((tier, provider, model))
    return out


def probe_routes(
    *,
    include_fallbacks: bool = True,
    include_tiers: bool = True,
    timeout: float = 25.0,
    probed_cache: dict[tuple[str, str], tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Every configured LLM route, not just the primary one.

    Rows are ``{kind, label, provider, model, status, detail}`` where kind is
    ``mode`` | ``fallback`` | ``tier``. One shared probe cache spans all three,
    so a model reached by several routes costs a single call and can never be
    reported with two different statuses in one run.
    """
    # An external cache lets a caller pay ONE call for a model reached both as a
    # route and as a catalog head — the heartbeat probes both in the same beat.
    probed: dict[tuple[str, str], tuple[str, str]] = (
        probed_cache if probed_cache is not None else {}
    )
    rows: list[dict[str, Any]] = [
        {"kind": "mode", "label": r["mode"], **{k: r[k] for k in ("provider", "model",
                                                                  "status", "detail")}}
        for r in probe_modes(timeout=timeout, probed_cache=probed)
    ]
    extra: list[tuple[str, list[tuple[str, str, str]]]] = []
    if include_fallbacks:
        extra.append(("fallback", _configured_fallbacks()))
    if include_tiers:
        extra.append(("tier", _configured_tiers()))
    for kind, entries in extra:
        for label, provider, model in entries:
            key = (provider, model)
            if key not in probed:
                probed[key] = probe_model(provider, model, timeout=timeout)
            status, detail = probed[key]
            rows.append({"kind": kind, "label": label, "provider": provider,
                         "model": model, "status": status, "detail": detail})
    return rows


def probe_modes(
    modes: list[str] | None = None,
    *,
    timeout: float = 25.0,
    probed_cache: dict[tuple[str, str], tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Probe each mode's ACTUALLY-ROUTED model (via ``resolve_llm`` — so the
    small_talk fast-chat override, default_provider, and fallbacks are reflected).

    Returns a list of ``{mode, provider, model, status, detail}`` dicts.
    """
    from navig.llm.router import CANONICAL_MODES, resolve_llm

    targets = modes or sorted(CANONICAL_MODES)
    out: list[dict[str, Any]] = []
    # Several modes commonly share one provider:model, and probing it once per
    # mode both wastes a call and rate-limits US: two back-to-back calls to the
    # same NVIDIA model made the second return 503, so one run reported the
    # identical model as BOTH "live" and "busy" — a report contradicting itself.
    probed = probed_cache if probed_cache is not None else {}
    for m in targets:
        try:
            cfg = resolve_llm(mode=m)
            provider, model = cfg.provider, cfg.model
        except Exception as exc:  # noqa: BLE001 — a broken route is itself a finding
            out.append({"mode": m, "provider": "?", "model": "?",
                        "status": "error", "detail": f"resolve failed: {str(exc)[:70]}"})
            continue
        key = (provider, model)
        if key not in probed:
            probed[key] = probe_model(provider, model, timeout=timeout)
        status, detail = probed[key]
        out.append({"mode": m, "provider": provider, "model": model,
                    "status": status, "detail": detail})
    return out


def _has_dispatch_credential(provider_id: str) -> bool:
    """Would a real call to *provider_id* carry a credential?

    Asks the SAME resolver `probe_model` dispatches through
    (`resolve_provider_credential`: a routable connection first — a Claude
    subscription's OAuth token has no API key — then the shared key store). The
    first version asked `AuthProfileManager.resolve_auth`, the key store alone,
    so a subscription-backed provider was reported "not judged" while every real
    call to it succeeded: measured 2026-09-26, the sweep and the daily head check
    both skipped `anthropic` on a machine whose Claude subscription answered —
    and all four ids in anthropic's manifest turned out to be retired.
    """
    try:
        from navig.providers.inference import resolve_provider_credential

        api_key, oauth_token = resolve_provider_credential(provider_id)
        return bool(api_key or oauth_token)
    except Exception:  # noqa: BLE001 — an unreadable credential store is "no credential"
        return False


def _router_fallback_ids() -> dict[str, list[str]]:
    """``{provider: [model, …]}`` from the live router's fallback tables, in
    table order, deduplicated. Empty on any import failure — the sweep must
    never break because a routing module moved."""
    try:
        from navig.llm.routing.capabilities import MODE_MODEL_PREFERENCE
        from navig.llm.routing.router import _PROVIDER_DEFAULT_MODELS
    except Exception:  # noqa: BLE001
        return {}
    pairs = [(prov, mid) for prefs in MODE_MODEL_PREFERENCE.values() for prov, mid in prefs.items()]
    pairs += [(prov, mid) for prov, prefs in _PROVIDER_DEFAULT_MODELS.items() for mid in prefs.values()]
    out: dict[str, list[str]] = {}
    for prov, mid in pairs:
        if mid and mid not in out.setdefault(prov, []):
            out[prov].append(mid)
    return out


def catalog_entries(provider_id: str | None = None) -> list[dict[str, str]]:
    """Every ``(provider, model)`` the CATALOG names, with where it is named.

    The catalog is the two hand-maintained lists a routing default or a
    credential probe picks FROM — ``registry.ALL_PROVIDERS[*].models`` (the
    audited list) and ``types.BUILTIN_PROVIDERS[*].models`` (the metadata
    table). ``probe_routes`` covers the models an install ROUTES to; nothing
    covered the ones it can SUBSTITUTE IN, and on 2026-09-19 the table's first
    row was retired for all four providers this machine held a key for.

    It also covers the live router's two FALLBACK tables (``where="router"``),
    which name models used only after a primary provider has failed: measured
    2026-09-26, nine of their ids were dead and nothing looked there.

    Rows are ``{provider, model, where}`` with *where* ∈ ``manifest`` |
    ``table`` | ``both`` | ``router``, ordered provider-then-catalog-order,
    deduplicated. Router-only ids come LAST for each provider, so the first row
    is still ``models[0]`` — the head the daily check probes.
    """
    from navig.providers.registry import get_provider, list_all_providers
    from navig.providers.types import BUILTIN_PROVIDERS

    router_ids = _router_fallback_ids()
    manifests = [get_provider(provider_id)] if provider_id else list_all_providers()
    out: list[dict[str, str]] = []
    for man in manifests:
        pid = man.id if man is not None else (provider_id or "")
        if not pid:
            continue
        seen: dict[str, dict[str, str]] = {}
        cfg = BUILTIN_PROVIDERS.get(pid.lower())
        # Router ids only for CLOUD providers: a local model (ollama's llama3.2)
        # is a download, not a retirement, and "add a key" would be false advice.
        routed = router_ids.get(pid, []) if getattr(man, "tier", "") == "cloud" else []
        for mid, where in (
            [(m, "manifest") for m in (getattr(man, "models", None) or [])]
            + [(m.id, "table") for m in (cfg.models if cfg else [])]
            + [(m, "router") for m in routed]
        ):
            if not mid:
                continue
            if mid in seen:
                # Named by BOTH catalog lists: one probe, one row — a model
                # reported twice with two statuses is how `probe_modes` learned
                # to cache. A router mention of an id the catalog already lists
                # adds nothing to probe, so it keeps the catalog's label.
                if where != "router" and seen[mid]["where"] != where:
                    seen[mid]["where"] = "both"
                continue
            row = {"provider": pid, "model": mid, "where": where}
            seen[mid] = row
            out.append(row)
    return out


def probe_catalog(
    provider_id: str | None = None,
    *,
    heads_only: bool = False,
    timeout: float = 25.0,
    probed_cache: dict[tuple[str, str], tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Probe every catalog id with a 1-token call, skipping what we cannot judge.

    Rows are ``{provider, model, where, status, detail}``. Two statuses are
    produced without spending a call, and both are honest about that:

    * ``denylisted`` — already in :data:`RETIRED_MODELS` for this provider.
      ``probe_model`` would fast-fail it to ``dead`` anyway, and reporting it as
      a fresh finding would re-raise a debt already paid.
    * ``nokey`` — no credential resolves for the provider, so the id was NOT
      judged. Never rendered as a tick: a green light over an unknown is worse
      than a red one (the ``navig doctor`` rule).

    One shared probe cache, so an id named by both lists (and a provider probed
    by several callers) costs one call and cannot be reported two ways.

    *heads_only* probes just each provider's FIRST catalog id — see
    :func:`probe_catalog_heads`, which is what a scheduled check uses.
    """
    probed = probed_cache if probed_cache is not None else {}
    keyed: dict[str, bool] = {}
    out: list[dict[str, Any]] = []
    entries = catalog_entries(provider_id)
    if heads_only:
        firsts: dict[str, dict[str, str]] = {}
        for e in entries:
            firsts.setdefault(e["provider"], e)
        entries = list(firsts.values())
    for entry in entries:
        pid, mid = entry["provider"], entry["model"]
        if pid not in keyed:
            keyed[pid] = _has_dispatch_credential(pid)
        if not keyed[pid]:
            out.append({**entry, "status": "nokey",
                        "detail": f"add a key: navig ai providers --add {pid}"})
            continue
        if is_retired(mid, pid):
            out.append({**entry, "status": "denylisted",
                        "detail": "already in RETIRED_MODELS"})
            continue
        key = (pid, mid)
        if key not in probed:
            probed[key] = probe_model(pid, mid, timeout=timeout)
        status, detail = probed[key]
        out.append({**entry, "status": status, "detail": detail})
    return out


def probe_catalog_heads(
    *,
    timeout: float = 25.0,
    probed_cache: dict[tuple[str, str], tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Probe only each provider's FIRST catalog id — the cheap daily check.

    ``models[0]`` carries blast radius nothing else in the catalog does: it is
    the credential probe (`navig ai providers --test`, `navig connect`
    validation) *and* what `model_router` substitutes in for a slot whose model
    the manifest does not list. When it dies, "test my provider" and every
    substituted route break together while every *configured* route stays green
    — which is exactly why :func:`probe_routes` cannot see it.

    A full :func:`probe_catalog` is 58 calls on a machine with five keys, which
    is the right shape for an on-demand audit and the wrong shape for a daily
    beat; this is one call per provider that HAS a credential (five). Rows carry
    ``kind="catalog"`` and ``label=<provider>`` so a reporting surface can label
    them without parsing a model id.
    """
    return [
        {**r, "kind": "catalog", "label": r["provider"]}
        for r in probe_catalog(heads_only=True, timeout=timeout, probed_cache=probed_cache)
        if r["status"] != "nokey"
    ]


def retired_catalog_entries(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The catalog rows a provider has RETIRED — the ones to denylist.

    ``auth`` and ``unreachable`` are deliberately absent, unlike
    :func:`dead_modes`: this answers "which id is gone", and a bad key or a down
    endpoint says nothing about the id. ``dead_modes`` answers "would a run
    break", where both of those do.
    """
    return [r for r in results if r["status"] == "dead"]


def denylist_lines(results: list[dict[str, Any]]) -> list[str]:
    """Paste-ready ``RETIRED_MODELS`` entries for the retired catalog rows.

    The sweep needs live keys and cannot gate a build; the DENYLIST is offline
    and ``test_mode_liveness_guard`` already fails on it. Emitting the exact
    line is what connects the two, so the finding lands in the repo instead of
    in a terminal someone closed.
    """
    import datetime as _dt

    day = _dt.date.today().isoformat()
    return [
        f'    "{r["provider"]}:{r["model"]}",'
        f'  # {r["detail"]} — swept {day}'
        for r in retired_catalog_entries(results)
    ]


def dead_modes(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Filter probe results to the ones that would break a run (dead/unreachable/auth).

    ``transient`` is deliberately absent: the heartbeat turns everything this
    returns into a ``[HIGH]`` issue, and the gateway turns a HIGH issue into an
    operator approval prompt. A provider being briefly busy is not something the
    operator can approve their way out of.
    """
    return [r for r in results if r["status"] in ("dead", "unreachable", "auth")]
