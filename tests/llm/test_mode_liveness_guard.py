"""
Offline CI guard for LLM mode routing — no network, no credentials, runs in the
normal `python -m pytest tests/` step.

It can't detect a provider retiring a model live (that needs `navig mode doctor`),
but it DOES fail the build if a shipped DEFAULT mode ever points at a model we've
already seen retired — the regression that let `nvidia:qwen3-coder-480b` (HTTP 410)
become the coding default and break every coding run.
"""

from __future__ import annotations

from navig.llm.liveness import RETIRED_MODELS, is_retired
from navig.llm.router import CANONICAL_MODES, LLMModeRouter


def _default_modes():
    # Empty config → the SHIPPED code defaults (what a fresh install routes to).
    return LLMModeRouter(config={}).modes


def test_no_default_mode_uses_a_retired_model():
    modes = _default_modes()
    for name in CANONICAL_MODES:
        cfg = modes.get_mode(name)
        assert cfg is not None, f"missing default mode: {name}"
        assert cfg.provider, f"mode {name} has empty provider"
        assert cfg.model, f"mode {name} has empty model"
        # Unscoped on purpose: a SHIPPED DEFAULT should not point at a model
        # that is dead anywhere, even if this provider still serves it.
        assert not is_retired(cfg.model), (
            f"default mode '{name}' points at RETIRED model '{cfg.model}' — "
            f"repoint it in navig/llm/router.py:LLMModesConfig"
        )
        assert not is_retired(cfg.model, cfg.provider), (
            f"default mode '{name}' is retired on its own provider {cfg.provider}"
        )
        if cfg.fallback_model:
            assert not is_retired(cfg.fallback_model), (
                f"default mode '{name}' fallback '{cfg.fallback_model}' is RETIRED"
            )


def test_is_retired_matches_known_and_ignores_live():
    assert is_retired("qwen/qwen3-coder-480b-a35b-instruct")
    assert is_retired("deepseek-ai/deepseek-r1")
    assert is_retired("qwen/qwen2.5-coder-32b-instruct")
    # live models must not be flagged.
    # ⚠ This list used `meta/llama-3.1-70b-instruct` as its "live" example until
    # NVIDIA retired it (410, EOL 2026-08-26) and it was added to the denylist —
    # so pick ids that are probe-verified live, and expect to revisit them.
    assert not is_retired("nvidia/nemotron-3-super-120b-a12b")
    assert not is_retired("claude-opus-4-8")
    assert not is_retired("gpt-4o")
    # A namespaced retired id must NOT flag a different provider's similar id.
    assert not is_retired("meta-llama/llama-3.1-8b-instruct")  # live on OpenRouter
    assert not is_retired(None)
    assert not is_retired("")


def test_denylist_is_populated():
    # Guard against an accidental empty denylist silently disabling the check.
    assert len(RETIRED_MODELS) >= 3


def test_classify_probe_error():
    from navig.llm.liveness import classify_probe_error as c

    assert c(Exception("Client error '410 Gone' for url ..."))[0] == "dead"
    assert c(Exception("model reached its end of life"))[0] == "dead"
    assert c(Exception("Client error '404 Not Found'"))[0] == "dead"
    assert c(Exception("401 Unauthorized"))[0] == "auth"
    assert c(Exception("No credential for provider 'x'"))[0] == "nokey"
    # A READ timeout means the endpoint ANSWERED the connection and the model ran
    # long — `slow`, which is not a defect. A CONNECT failure is `unreachable`,
    # which is. ConnectTimeout carries both words and must stay unreachable.
    assert c(Exception("The read operation timed out"))[0] == "slow"
    assert c(Exception("ReadTimeout"))[0] == "slow"
    assert c(Exception("ConnectError: All connection attempts failed"))[0] == "unreachable"
    assert c(Exception("ConnectTimeout"))[0] == "unreachable"
    # A retirement served as 400 (groq). BOTH halves are required: 400 is also a
    # malformed request, and calling that dead would denylist a live model.
    assert c(Exception("The model `x` has been decommissioned"))[0] == "dead"
    assert c(Exception("Invalid value for max_tokens: must be >= 1"))[0] == "error"
    assert c(Exception("weird boom"))[0] == "error"


def test_dead_modes_filter():
    from navig.llm.liveness import dead_modes

    rows = [
        {"mode": "a", "status": "live"},
        {"mode": "b", "status": "dead"},
        {"mode": "c", "status": "auth"},
        {"mode": "d", "status": "unreachable"},
        {"mode": "e", "status": "nokey"},   # not-configured is not a break
    ]
    got = {r["mode"] for r in dead_modes(rows)}
    assert got == {"b", "c", "d"}


# ── The manifests themselves ─────────────────────────────────────────────
#
# The tests above guard the shipped default MODES. Nothing guarded the provider
# MANIFESTS, and that is where the damage came from: `model_router` substitutes a
# slot's model when it is absent from the manifest, using `default_model or
# models[0]`. So the manifest's first entry is an implicit routing default, and a
# dead one silently repoints every slot at a dead model.
#
# Measured 2026-09-08: the nvidia manifest listed 20 ids and a real 1-token call to
# each of the 20 FAILED — first entry included.


def _manifests_with_models():
    from navig.providers.registry import list_all_providers

    out = [(p.id, list(getattr(p, "models", None) or [])) for p in list_all_providers()]
    out = [(pid, ms) for pid, ms in out if ms]
    assert len(out) >= 10, "provider registry looks empty — this guard would be vacuous"
    return out


def test_no_provider_manifest_lists_a_retired_model():
    """Scoped to the manifest's OWN provider — a model retired elsewhere is fine here."""
    offenders = [
        f"{pid}: {m}"
        for pid, ms in _manifests_with_models()
        for m in ms
        if is_retired(m, pid)
    ]
    assert not offenders, (
        "provider manifest(s) list a model recorded as RETIRED: "
        + ", ".join(offenders)
        + " — remove it; the first entry is also the substitution default in model_router"
    )


def test_every_providers_substitution_default_is_not_retired():
    """`models[0]` is what a mistyped/withdrawn slot model gets replaced WITH.

    A retired entry here is the worst case: the operator's explicit choice is
    discarded in favour of something that cannot answer.
    """
    offenders = [
        f"{pid}: {ms[0]}" for pid, ms in _manifests_with_models() if is_retired(ms[0], pid)
    ]
    assert not offenders, f"substitution default is a retired model: {offenders}"


# ── The ProviderConfig table ─────────────────────────────────────────────
#
# A THIRD list names models: `BUILTIN_PROVIDERS` in navig/providers/types.py. It
# is the one every credential probe read `models[0]` from — `navig ai providers
# --test`, `navig connect` validation — and the guard above never looked at it.
# Measured 2026-09-19: its first row was retired for xai, openai, nvidia AND
# openrouter (three of them already on this denylist), so four working keys
# reported as broken. A guard protects the surface it scans, not the class.


def _table_with_models():
    from navig.providers.types import BUILTIN_PROVIDERS

    out = [(pid, [m.id for m in cfg.models]) for pid, cfg in BUILTIN_PROVIDERS.items()]
    out = [(pid, ms) for pid, ms in out if ms]
    assert len(out) >= 8, "BUILTIN_PROVIDERS looks empty — this guard would be vacuous"
    return out


def test_no_builtin_provider_table_lists_a_retired_model():
    offenders = [
        f"{pid}: {m}" for pid, ms in _table_with_models() for m in ms if is_retired(m, pid)
    ]
    assert not offenders, (
        "BUILTIN_PROVIDERS lists a model recorded as RETIRED: " + ", ".join(offenders)
        + " — remove it; probes and `navig ai models` read this table"
    )


def test_every_builtin_tables_first_row_is_not_retired():
    """`models[0]` was the credential probe until 2026-09-19 and is still what
    `navig ai models` shows first."""
    offenders = [f"{pid}: {ms[0]}" for pid, ms in _table_with_models() if is_retired(ms[0], pid)]
    assert not offenders, f"table's first row is a retired model: {offenders}"


def test_no_manifest_model_is_flagged_by_another_providers_retirement():
    """The collisions this used to grandfather are now structurally impossible.

    Retirement is PROVIDER-SPECIFIC, and the denylist is now keyed that way. It
    previously matched a bare id against the last path segment of a namespaced
    one, so `deepseek-ai/deepseek-r1` (dead on NVIDIA) also flagged a bare
    `deepseek-r1` on GitHub Models, where it was never tested — two live manifest
    entries had to be pinned as knowingly accepted.

    `probe_model` fast-fails a match to "dead" WITHOUT spending a call, so such a
    match is unfalsifiable: it reports a model dead having never asked. That is
    why this is scoped rather than documented.
    """
    offenders = [
        f"{pid}: {m}"
        for pid, ms in _manifests_with_models()
        for m in ms
        if is_retired(m, pid)
    ]

    assert not offenders, f"manifest models retired on their OWN provider: {offenders}"


def test_every_denylist_entry_names_a_provider():
    """A bare entry would silently mean "any provider" again."""
    bare = [e for e in RETIRED_MODELS if ":" not in e]

    assert not bare, f"denylist entries must be 'provider:model': {bare}"


def test_a_model_retired_on_one_provider_is_fine_on_another():
    """The concrete case: NVIDIA retired it, OpenRouter still serves it."""
    assert is_retired("meta/llama-3.1-8b-instruct", "nvidia")
    assert not is_retired("meta/llama-3.1-8b-instruct", "openrouter")


def test_the_openai_substitution_default_stays_cheap():
    """`models[0]` is what an unknown-model slot is silently replaced WITH.

    gpt-5 and gpt-5-mini were added once they became callable (they reject
    `max_tokens` and any explicit `temperature`, so every call failed until
    `_sanitize_openai_body` learned that family). Promoting one to first would
    quietly repoint every substituted slot at a pricier model — a cost change
    disguised as a catalogue update.
    """
    from navig.providers.registry import get_provider

    models = list(get_provider("openai").models)

    assert models[0] == "gpt-4.1", "the substitution default changed"
    assert "gpt-5" in models, "gpt-5 is callable and should be offerable"
