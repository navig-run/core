"""
Runtime error-driven fallback (#1): a mid-call failure (429/503/dead model)
must VISIBLY hop to the next configured model instead of ending the turn.

Covers:
  * categorize_error — status codes, ProviderError-like objects, string reasons
  * cooldown map — mark / is_cooling / remaining / reset, and "never shorten"
  * _assemble_fallback_chain — mode fallback + global chain, dedupe, drop primary
  * run_llm dispatch — hop on primary error, pre-emptive skip of a cooling
    primary, visible fallback metadata, and all-fail surfacing
"""

from __future__ import annotations

import pytest

from navig.llm import fallback_policy as fp
from navig.llm import generate as g
from navig.llm.types import LLMResult, ModelSelection

# ── categorize_error ────────────────────────────────────────────────


@pytest.mark.parametrize(
    "reason,expected",
    [
        ("error:ProviderError: [anthropic] rate limit (status=429)", fp.RATE_LIMITED),
        ("error:Overloaded: (status=529)", fp.OVERLOADED),
        ("error:ProviderError: service unavailable (status=503)", fp.OVERLOADED),
        ("error:AuthError: invalid api key (status=401)", fp.AUTH),
        ("error:ProviderError: forbidden (status=403)", fp.AUTH),
        ("error:ProviderError: insufficient credit (status=402)", fp.PAYMENT),
        ("error:ProviderError: model decommissioned (status=410)", fp.DEAD_MODEL),
        ("error:ProviderError: model not found (status=404)", fp.DEAD_MODEL),
        ("error:ProviderError: internal error (status=500)", fp.SERVER_ERROR),
        ("error:TimeoutError: request timed out", fp.TIMEOUT),
        ("error:ValueError: something odd", fp.UNKNOWN),
    ],
)
def test_categorize_error_strings(reason, expected):
    assert fp.categorize_error(reason) == expected


def test_categorize_error_keyword_without_status():
    assert fp.categorize_error("This request would exceed your account's rate limit") == fp.RATE_LIMITED
    assert fp.categorize_error("The model qwen3-coder is deprecated") == fp.DEAD_MODEL


def test_categorize_error_exception_object():
    class _PErr(Exception):
        status_code = 429
        error_type = "rate_limit"

    assert fp.categorize_error(_PErr("nope")) == fp.RATE_LIMITED


def test_categorize_error_error_type_wins_over_missing_status():
    class _PErr(Exception):
        status_code = None
        error_type = "billing"

    assert fp.categorize_error(_PErr("card declined")) == fp.PAYMENT


# ── describe_category (shared humanizer for rotation notices) ────────


def test_describe_category_known_phrases():
    assert fp.describe_category(fp.RATE_LIMITED) == "rate-limited"
    assert fp.describe_category(fp.AUTH) == "failing to authenticate"
    assert fp.describe_category(fp.PAYMENT) == "having a billing issue"
    assert fp.describe_category("cooldown") == "cooling down from a recent failure"
    # reads correctly in the sentence both surfaces build:
    assert f"was {fp.describe_category(fp.OVERLOADED)}" == "was overloaded"


def test_describe_category_blank_and_unknown():
    assert fp.describe_category(None) == "unavailable"
    assert fp.describe_category("") == "unavailable"
    assert fp.describe_category(fp.UNKNOWN) == "unavailable"
    # a novel category degrades to a readable de-underscored form, not a raw token
    assert fp.describe_category("some_new_thing") == "some new thing"


def test_describe_category_covers_all_cooldown_categories():
    # Every category that gets a cooldown (i.e. can drive a rotation) has a phrase.
    missing = {c for c in fp.COOLDOWN_SECONDS if c not in fp._CATEGORY_PHRASE}
    assert not missing, f"unmapped cooldown categories: {missing}"


# ── cooldown map ────────────────────────────────────────────────────


def test_cooldown_mark_and_expiry(monkeypatch):
    fp.reset_cooldowns()
    now = [1000.0]
    monkeypatch.setattr(fp.time, "time", lambda: now[0])

    fp.mark_cooldown("anthropic:claude-opus-4-8", fp.RATE_LIMITED)  # 60s
    assert fp.is_cooling("anthropic:claude-opus-4-8") is True
    assert 59.0 <= fp.cooldown_remaining("anthropic:claude-opus-4-8") <= 60.0

    now[0] = 1061.0  # past the 60s window
    assert fp.is_cooling("anthropic:claude-opus-4-8") is False
    assert fp.cooldown_remaining("anthropic:claude-opus-4-8") == 0.0


def test_cooldown_never_shortens(monkeypatch):
    fp.reset_cooldowns()
    now = [1000.0]
    monkeypatch.setattr(fp.time, "time", lambda: now[0])
    fp.mark_cooldown("x:y", fp.PAYMENT)       # 600s
    fp.mark_cooldown("x:y", fp.SERVER_ERROR)  # 10s — must NOT overwrite the longer one
    assert fp.cooldown_remaining("x:y") > 500.0


def test_unknown_category_does_not_cool():
    fp.reset_cooldowns()
    fp.mark_cooldown("a:b", fp.UNKNOWN)
    assert fp.is_cooling("a:b") is False


def test_account_wide_cooldown_spreads_across_models():
    # A Claude Max rate limit is account-wide: capping opus on account A must
    # also skip account A for sonnet (so rotation doesn't re-probe it).
    fp.reset_cooldowns()
    fp.mark_cooldown("anthropic:claude-opus-4-8@conn:A", fp.RATE_LIMITED)
    assert fp.is_cooling("anthropic:claude-opus-4-8@conn:A") is True
    assert fp.is_cooling("anthropic:claude-sonnet-4-6@conn:A") is True   # same account
    assert fp.is_cooling("anthropic:claude-sonnet-4-6@conn:B") is False  # other account
    assert fp.cooldown_remaining("anthropic:claude-sonnet-4-6@conn:A") > 0.0
    fp.reset_cooldowns()


def test_model_specific_error_does_not_cool_whole_account():
    # A dead/retired model is model-specific — it must NOT sideline other models
    # on the same account.
    fp.reset_cooldowns()
    fp.mark_cooldown("anthropic:claude-opus-4-8@conn:A", fp.DEAD_MODEL)
    assert fp.is_cooling("anthropic:claude-opus-4-8@conn:A") is True
    assert fp.is_cooling("anthropic:claude-sonnet-4-6@conn:A") is False
    fp.reset_cooldowns()


def test_account_cool_key_format():
    # The one shared key format used by all three dispatch paths.
    assert fp.account_cool_key("anthropic", "claude-opus-4-8", "A") == "anthropic:claude-opus-4-8@conn:A"
    assert fp.account_cool_key("anthropic", "claude-opus-4-8", None) == "anthropic:claude-opus-4-8"
    # Round-trips with the account-wide cooldown it feeds.
    fp.reset_cooldowns()
    fp.mark_cooldown(fp.account_cool_key("anthropic", "opus", "A"), fp.RATE_LIMITED)
    assert fp.is_cooling(fp.account_cool_key("anthropic", "sonnet", "A")) is True   # account-wide
    fp.reset_cooldowns()


def test_bare_spec_has_no_account_wide_effect():
    # The run_llm default account uses a bare provider:model key (no @conn:) —
    # it only cools itself, never a whole account.
    fp.reset_cooldowns()
    fp.mark_cooldown("anthropic:claude-opus-4-8", fp.RATE_LIMITED)
    assert fp.is_cooling("anthropic:claude-opus-4-8") is True
    assert fp.is_cooling("anthropic:claude-sonnet-4-6") is False
    fp.reset_cooldowns()


# ── _assemble_fallback_chain ────────────────────────────────────────


def _sel(provider="anthropic", model="claude-opus-4-8", mode="big_tasks"):
    return ModelSelection(
        provider_name=provider, model_name=model, metadata={"mode": mode}
    )


@pytest.fixture
def _no_siblings(monkeypatch):
    """No sibling accounts — isolates the chain from this machine's real store."""
    monkeypatch.setattr("navig.providers.inference.list_provider_connections", lambda p: [])


def test_assemble_chain_dedupes_and_drops_primary(monkeypatch, _no_siblings):
    # Mode fallback resolves to ollama; global chain repeats the primary + a dupe.
    monkeypatch.setattr(
        "navig.llm.router.get_mode_fallback_spec", lambda mode: "ollama:qwen2.5:7b-instruct"
    )
    chain = g._assemble_fallback_chain(
        _sel(),
        ["anthropic:claude-opus-4-8", "openai:gpt-4o", "openai:gpt-4o"],
        model_override=None,
    )
    assert chain == ["ollama:qwen2.5:7b-instruct", "openai:gpt-4o"]  # primary + dupe removed


def test_assemble_chain_skips_mode_fallback_on_model_override(monkeypatch, _no_siblings):
    monkeypatch.setattr(
        "navig.llm.router.get_mode_fallback_spec", lambda mode: "ollama:qwen2.5:7b-instruct"
    )
    chain = g._assemble_fallback_chain(
        _sel(), ["openai:gpt-4o"], model_override="anthropic:claude-opus-4-8"
    )
    assert chain == ["openai:gpt-4o"]  # mode fallback NOT consulted under --model


def test_sibling_accounts_prepended_even_under_model_override(monkeypatch):
    """A second/third Claude Max account is offered (same model) BEFORE lesser
    models — and even when the user pinned the model with --model."""
    monkeypatch.setattr(
        "navig.providers.inference.list_provider_connections",
        lambda p: [
            {"connection_id": "acctA", "is_default": True},   # [0] = primary's account
            {"connection_id": "acctB"},
            {"connection_id": "acctC"},
        ],
    )
    monkeypatch.setattr("navig.llm.router.get_mode_fallback_spec", lambda mode: None)
    chain = g._assemble_fallback_chain(
        _sel(), ["openai:gpt-4o"], model_override="anthropic:claude-opus-4-8"
    )
    assert chain == [
        "anthropic:claude-opus-4-8@conn:acctB",   # sibling accounts first…
        "anthropic:claude-opus-4-8@conn:acctC",
        "openai:gpt-4o",                            # …then the lesser cross-provider model
    ]


def test_split_conn():
    assert g._split_conn("anthropic:claude-opus-4-8@conn:abc") == ("anthropic:claude-opus-4-8", "abc")
    assert g._split_conn("openai:gpt-4o") == ("openai:gpt-4o", None)


# ── run_llm dispatch integration ────────────────────────────────────


@pytest.fixture
def _no_context(monkeypatch):
    """Neutralize context assembly + routing so we control the selection."""
    monkeypatch.setattr(g, "_build_pipeline_context", lambda **kw: {})
    monkeypatch.setattr(g, "_enrich_messages_with_context", lambda m, c: m)
    monkeypatch.setattr(g, "_load_fallback_chain", lambda: [])
    # Deterministic: no sibling accounts unless a test opts in.
    monkeypatch.setattr("navig.providers.inference.list_provider_connections", lambda p: [])


def test_primary_error_hops_to_fallback_with_visible_metadata(monkeypatch, _no_context):
    fp.reset_cooldowns()
    calls: list[str] = []

    def wrap(**kw):
        sel = kw["selection"]
        spec = f"{sel.provider_name}:{sel.model_name}"
        calls.append(spec)
        if spec == "anthropic:claude-opus-4-8":
            return LLMResult(content="", model=sel.model_name, provider=sel.provider_name,
                             finish_reason="error:ProviderError: rate limit (status=429)")
        return LLMResult(content="from-fallback", model=sel.model_name,
                         provider=sel.provider_name, finish_reason="stop")

    monkeypatch.setattr(g, "_call_and_wrap", wrap)

    res = g.run_llm(
        [{"role": "user", "content": "hi"}],
        model_override="anthropic:claude-opus-4-8",
        fallback_models=["openai:gpt-4o"],
    )
    assert res.content == "from-fallback"
    assert res.is_fallback is True
    assert res.raw["fallback"]["from"] == "anthropic:claude-opus-4-8"
    assert res.raw["fallback"]["to"] == "openai:gpt-4o"
    assert res.raw["fallback"]["reason"] == fp.RATE_LIMITED
    # primary was marked cooling by the dispatcher
    assert fp.is_cooling("anthropic:claude-opus-4-8") is True
    assert calls == ["anthropic:claude-opus-4-8", "openai:gpt-4o"]


def test_cooling_primary_is_skipped_preemptively(monkeypatch, _no_context):
    fp.reset_cooldowns()
    fp.mark_cooldown("anthropic:claude-opus-4-8", fp.RATE_LIMITED)  # already capped
    calls: list[str] = []

    def wrap(**kw):
        sel = kw["selection"]
        spec = f"{sel.provider_name}:{sel.model_name}"
        calls.append(spec)
        return LLMResult(content="ok", model=sel.model_name, provider=sel.provider_name,
                         finish_reason="stop")

    monkeypatch.setattr(g, "_call_and_wrap", wrap)
    # Route by MODE (no model_override) so the pre-emptive skip applies.
    # run_llm does `from navig.llm.router import resolve_llm` at call time, so
    # patch it on the router module, not on `g`.
    from navig.llm.router import ResolvedLLMConfig

    monkeypatch.setattr(
        "navig.llm.router.resolve_llm",
        lambda **kw: ResolvedLLMConfig(provider="anthropic", model="claude-opus-4-8", mode="big_tasks"),
    )
    monkeypatch.setattr("navig.llm.router.get_mode_fallback_spec", lambda mode: None)

    res = g.run_llm([{"role": "user", "content": "hi"}], fallback_models=["openai:gpt-4o"])
    assert res.content == "ok"
    assert res.is_fallback is True
    # The cooling primary was NEVER dialed — first call went straight to fallback.
    assert calls == ["openai:gpt-4o"]


def test_all_fallbacks_fail_surfaces_error(monkeypatch, _no_context):
    fp.reset_cooldowns()

    def wrap(**kw):
        sel = kw["selection"]
        return LLMResult(content="", model=sel.model_name, provider=sel.provider_name,
                         finish_reason="error:ProviderError: boom (status=500)")

    monkeypatch.setattr(g, "_call_and_wrap", wrap)

    res = g.run_llm(
        [{"role": "user", "content": "hi"}],
        model_override="anthropic:claude-opus-4-8",
        fallback_models=["openai:gpt-4o"],
    )
    assert res.content == ""
    assert res.finish_reason.startswith("all_fallbacks_failed:")
    assert fp.is_cooling("openai:gpt-4o") is True  # failed candidate cooled too


def test_run_llm_primary_error_marks_account_wide_cooldown(monkeypatch, _no_context):
    # A mode-routed run_llm cap on the default account must record an
    # ACCOUNT-WIDE cooldown (@conn:<cid>), so the connection-first paths skip
    # that account for *every* model — consistency across the 3 dispatch paths.
    fp.reset_cooldowns()
    monkeypatch.setattr("navig.providers.inference.default_connection_id", lambda p: "DEF")
    monkeypatch.setattr(
        "navig.llm.router.resolve_llm",
        lambda **kw: __import__(
            "navig.llm.router", fromlist=["ResolvedLLMConfig"]
        ).ResolvedLLMConfig(provider="anthropic", model="claude-opus-4-8", mode="big_tasks"),
    )
    monkeypatch.setattr("navig.llm.router.get_mode_fallback_spec", lambda mode: None)

    def wrap(**kw):
        sel = kw["selection"]
        return LLMResult(content="", model=sel.model_name, provider=sel.provider_name,
                         finish_reason="error:ProviderError: rate limit (status=429)")

    monkeypatch.setattr(g, "_call_and_wrap", wrap)
    g.run_llm([{"role": "user", "content": "hi"}])  # mode-routed (no --model)

    # The default account is now cooling account-wide → a DIFFERENT model on it
    # is skipped too (this is what fixes "opus cap re-probes account for sonnet").
    assert fp.is_cooling("anthropic:claude-sonnet-4-6@conn:DEF") is True
    fp.reset_cooldowns()


def test_capped_account_rotates_to_sibling_and_cools_independently(monkeypatch, _no_context):
    """The headline: account A caps → dispatch hops to account B (same model),
    and cooling A does NOT cool B (per-account cooldown keys)."""
    fp.reset_cooldowns()
    monkeypatch.setattr(
        "navig.providers.inference.list_provider_connections",
        lambda p: [{"connection_id": "A", "is_default": True}, {"connection_id": "B"}],
    )
    seen = []

    def wrap(**kw):
        sel = kw["selection"]
        cid = (sel.metadata or {}).get("connection_id")
        seen.append((f"{sel.provider_name}:{sel.model_name}", cid))
        if cid in (None, "A"):  # default account A is capped
            return LLMResult(content="", model=sel.model_name, provider=sel.provider_name,
                             finish_reason="error:ProviderError: rate limit (status=429)")
        return LLMResult(content="from-B", model=sel.model_name, provider=sel.provider_name,
                         finish_reason="stop")

    monkeypatch.setattr(g, "_call_and_wrap", wrap)

    res = g.run_llm(
        [{"role": "user", "content": "hi"}],
        model_override="anthropic:claude-opus-4-8",
    )
    assert res.content == "from-B"
    assert res.is_fallback is True
    assert res.raw["fallback"]["connection_id"] == "B"
    # Default account A is cooling under its account-aware key (no bare-key
    # special case anymore); B is NOT — accounts cool independently.
    assert fp.is_cooling("anthropic:claude-opus-4-8@conn:A") is True
    assert fp.is_cooling("anthropic:claude-opus-4-8@conn:B") is False
    # dispatch order: primary (default A) → sibling B
    assert seen == [("anthropic:claude-opus-4-8", None), ("anthropic:claude-opus-4-8", "B")]


# ── get_mode_fallback_spec reachability gating ──────────────────────


def test_mode_fallback_gated_when_ollama_down(monkeypatch):
    """A default ollama mode-fallback is dropped when Ollama isn't running, so a
    cloud user gets the clean rate-limit error, not a doomed hop → all_failed."""
    import navig.llm.router as r

    # A mode whose fallback is a local ollama model...
    monkeypatch.setattr(
        r, "get_llm_router",
        lambda force_new=False: type("R", (), {
            "modes": type("M", (), {"get_mode": lambda self, m: type("C", (), {
                "fallback_model": "qwen2.5:7b-instruct", "fallback_provider": "ollama",
                "provider": "anthropic",
            })()})(),
            "resolve_mode": lambda self, m: m,
        })(),
    )
    # ...and Ollama is down / has no models installed.
    monkeypatch.setattr(r, "_check_ollama_models", lambda *a, **k: {})
    assert r.get_mode_fallback_spec("big_tasks") is None
    # But with the model installed, it IS offered.
    monkeypatch.setattr(r, "_check_ollama_models", lambda *a, **k: {"qwen2.5:7b-instruct": True})
    assert r.get_mode_fallback_spec("big_tasks") == "ollama:qwen2.5:7b-instruct"
    # require_reachable=False bypasses the probe entirely.
    monkeypatch.setattr(r, "_check_ollama_models", lambda *a, **k: {})
    assert r.get_mode_fallback_spec("big_tasks", require_reachable=False) == "ollama:qwen2.5:7b-instruct"
