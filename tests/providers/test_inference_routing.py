"""
Tests for the connection→inference bridge (navig.providers.inference): route
selection, honest external-not-routable errors, and the no-connection path.

Pure routing logic — no network. `resolve_route` imports `list_connections`
from `navig.providers.connect` at call time, so we patch it there.
"""

from __future__ import annotations

import pytest

from navig.providers import inference as inf


def _patch_conns(monkeypatch, conns):
    monkeypatch.setattr(
        "navig.providers.connect.list_connections", lambda *a, **k: conns
    )


def _conn(cid, name, template, *, default=False, routable=True, driver="native",
          model="m-default", models=None, provider=None):
    meta = {"provider_id": provider} if provider else {}
    return {
        "connection_id": cid, "name": name, "template_id": template, "driver": driver,
        "is_default": default, "is_routable": routable, "default_model": model,
        "models": models if models is not None else [model], "metadata": meta,
    }


def test_parse_spec():
    assert inf._parse_spec("anthropic:claude-opus-4-8") == ("anthropic", "claude-opus-4-8")
    assert inf._parse_spec("openrouter:deepseek/deepseek-chat") == (
        "openrouter", "deepseek/deepseek-chat")
    # bare models now INFER a provider (else forced onto the default connection)
    assert inf._parse_spec("gpt-4o") == ("openai", "gpt-4o")
    assert inf._parse_spec("claude-opus-4-8") == ("anthropic", "claude-opus-4-8")
    assert inf._parse_spec("vendor/model") == ("openrouter", "vendor/model")
    assert inf._parse_spec("some-unknown-model") == (None, "some-unknown-model")
    assert inf._parse_spec(None) == (None, None)


def test_bare_model_declines_when_no_matching_connection(monkeypatch):
    # `--model gpt-4o` with ONLY a claude-max default must NOT force gpt-4o onto
    # anthropic (404); resolve_route declines so the caller falls back honestly.
    _patch_conns(monkeypatch, [_conn("a", "Claude", "claude-max", default=True)])
    assert inf.resolve_route("gpt-4o") is None
    assert inf.has_routable_connection("gpt-4o") is False


def test_resolve_default_and_by_provider(monkeypatch):
    conns = [
        _conn("a", "Claude", "claude-max", default=True, model="claude-opus-4-8"),
        _conn("b", "OpenAI", "openai-api", model="gpt-4o-mini"),
    ]
    _patch_conns(monkeypatch, conns)

    # no spec → default connection, its default model
    r = inf.resolve_route(None)
    assert r.connection["connection_id"] == "a"
    assert r.model == "claude-opus-4-8"

    # provider:model → the matching connection, model overridden
    r2 = inf.resolve_route("openai:gpt-4o")
    assert r2.connection["connection_id"] == "b"
    assert r2.model == "gpt-4o"

    # anthropic maps to claude-max (template provider_id)
    r3 = inf.resolve_route("anthropic:claude-haiku-4-5")
    assert r3.connection["connection_id"] == "a"
    assert r3.model == "claude-haiku-4-5"


def test_resolve_prefers_routable_then_default(monkeypatch):
    conns = [
        _conn("a", "OpenAI needs reauth", "openai-api", routable=False),
        _conn("b", "OpenAI good", "openai-api", routable=True),
    ]
    _patch_conns(monkeypatch, conns)
    r = inf.resolve_route("openai:gpt-4o")
    assert r.connection["connection_id"] == "b"  # the routable one wins


def test_virtual_connection_provider_from_metadata(monkeypatch):
    # configured-elsewhere virtual connection carries provider_id in metadata
    conns = [_conn("configured:xai", "xAI", "xai", default=True, provider="xai",
                   model="grok-3-mini")]
    _patch_conns(monkeypatch, conns)
    r = inf.resolve_route("xai:grok-3")
    assert r.connection["connection_id"] == "configured:xai"
    assert r.model == "grok-3"


def test_external_is_not_routable_but_surfaces_error(monkeypatch):
    conns = [_conn("x", "Codex", "codex", default=True, routable=False,
                   driver="external", model=None, models=[])]
    _patch_conns(monkeypatch, conns)
    # has_routable_connection returns True so the caller raises a clear error
    assert inf.has_routable_connection(None) is True
    with pytest.raises(inf.ExternalNotRoutable):
        inf.complete_via_connection(system_prompt="s", user_content="u")


def test_no_connection(monkeypatch):
    _patch_conns(monkeypatch, [])
    assert inf.has_routable_connection(None) is False
    with pytest.raises(inf.NoRoutableConnection):
        inf.complete_via_connection(system_prompt="s", user_content="u")


def test_non_routable_non_external_falls_through(monkeypatch):
    # a needs_reauth native connection is not usable → caller uses legacy path
    conns = [_conn("a", "Claude", "claude-max", default=True, routable=False)]
    _patch_conns(monkeypatch, conns)
    assert inf.has_routable_connection(None) is False


# ── credential resolution (the dispatch seam) ────────────────────────────────


def test_has_connection_for_provider(monkeypatch):
    conns = [
        _conn("a", "Claude", "claude-max", default=True, provider=None),   # anthropic via template
        _conn("b", "OpenAI needs reauth", "openai-api", routable=False),
    ]
    _patch_conns(monkeypatch, conns)
    assert inf.has_connection_for_provider("anthropic") is True   # claude-max routable
    assert inf.has_connection_for_provider("openai") is False     # not routable
    assert inf.has_connection_for_provider("xai") is False        # absent
    assert inf.has_connection_for_provider(None) is False


def test_credential_for_provider_oauth(monkeypatch):
    async def _fake_bearer(ref):
        return "tok-abc"

    _patch_conns(monkeypatch, [_conn("a", "Claude", "claude-max", default=True)])
    monkeypatch.setattr(inf, "_oauth_bearer", _fake_bearer)
    cred = inf.credential_for_provider("anthropic")
    assert cred == (None, "tok-abc")  # (api_key, oauth_token)


def test_credential_for_provider_api_key(monkeypatch):
    _patch_conns(monkeypatch, [_conn("b", "OpenAI", "openai-api", default=True)])
    monkeypatch.setattr(inf, "_resolve_api_key", lambda conn, pid: "sk-test")
    assert inf.credential_for_provider("openai") == ("sk-test", None)


def test_credential_for_provider_none(monkeypatch):
    _patch_conns(monkeypatch, [])
    assert inf.credential_for_provider("anthropic") is None


# ── connection-aware fallback (multiple accounts per provider) ──────


def test_list_provider_connections_default_first(monkeypatch):
    conns = [
        _conn("B", "Claude B", "claude-max"),
        _conn("A", "Claude A", "claude-max", default=True),
        _conn("C", "Claude C", "claude-max"),
        _conn("o", "OpenAI", "openai-api", default=True),  # other provider — excluded
    ]
    _patch_conns(monkeypatch, conns)
    got = [c["connection_id"] for c in inf.list_provider_connections("anthropic")]
    assert got[0] == "A"                 # default account first
    assert set(got) == {"A", "B", "C"}   # only anthropic connections
    assert inf.list_provider_connections(None) == []


def test_resolve_credential_by_connection_id(monkeypatch):
    async def _fake_bearer(ref):
        return f"tok-{ref}"

    a = _conn("A", "Claude A", "claude-max", default=True)
    b = _conn("B", "Claude B", "claude-max")
    a["secret_ref"], b["secret_ref"] = "ref-A", "ref-B"
    _patch_conns(monkeypatch, [a, b])
    monkeypatch.setattr(inf, "_oauth_bearer", _fake_bearer)

    # Pinning connection B returns B's token — NOT the default account A's.
    assert inf.resolve_provider_credential("anthropic", connection_id="B") == (None, "tok-ref-B")
    # No connection_id → default account A.
    assert inf.resolve_provider_credential("anthropic") == (None, "tok-ref-A")


def test_resolve_credential_unknown_connection_id_returns_none(monkeypatch):
    _patch_conns(monkeypatch, [_conn("A", "Claude A", "claude-max", default=True)])
    # A specific-but-missing account must NOT silently fall back to another key.
    assert inf.resolve_provider_credential("anthropic", connection_id="ZZZ") == (None, None)


# ── resolve_rotating_credential: cooldown-aware account picking ─────


def test_rotating_credential_skips_cooling_default(monkeypatch):
    async def _fake_bearer(ref):
        return f"tok-{ref}"

    a = _conn("A", "Claude A", "claude-max", default=True)
    b = _conn("B", "Claude B", "claude-max")
    a["secret_ref"], b["secret_ref"] = "ref-A", "ref-B"
    _patch_conns(monkeypatch, [a, b])
    monkeypatch.setattr(inf, "_oauth_bearer", _fake_bearer)

    from navig.llm import fallback_policy as fp

    fp.reset_cooldowns()
    # Cap the DEFAULT account A under the bare key (run_llm's convention).
    fp.mark_cooldown("anthropic:claude-opus-4-8", fp.RATE_LIMITED)
    key, oauth, cid = inf.resolve_rotating_credential("anthropic", "claude-opus-4-8")
    assert cid == "B" and oauth == "tok-ref-B"  # skipped capped A → sibling B
    fp.reset_cooldowns()


def test_rotating_credential_all_cooling_falls_to_default(monkeypatch):
    async def _fake_bearer(ref):
        return f"tok-{ref}"

    a = _conn("A", "Claude A", "claude-max", default=True)
    b = _conn("B", "Claude B", "claude-max")
    a["secret_ref"], b["secret_ref"] = "ref-A", "ref-B"
    _patch_conns(monkeypatch, [a, b])
    monkeypatch.setattr(inf, "_oauth_bearer", _fake_bearer)

    from navig.llm import fallback_policy as fp

    fp.reset_cooldowns()
    fp.mark_cooldown("anthropic:claude-opus-4-8", fp.RATE_LIMITED)  # A (bare)
    fp.mark_cooldown("anthropic:claude-opus-4-8@conn:B", fp.RATE_LIMITED)  # B
    _, oauth, cid = inf.resolve_rotating_credential("anthropic", "claude-opus-4-8")
    assert cid == "A" and oauth == "tok-ref-A"  # everything cooling → default A
    fp.reset_cooldowns()


def test_rotating_credential_no_connections_uses_key_store(monkeypatch):
    _patch_conns(monkeypatch, [])
    monkeypatch.setattr(inf, "resolve_provider_credential", lambda p, c=None: ("sk-x", None))
    assert inf.resolve_rotating_credential("openai", "gpt-4o") == ("sk-x", None, None)


# ── accounts_to_try: the shared account-selection primitive ─────────


def _ids(conns):
    return [c["connection_id"] for c in conns]


def test_accounts_to_try_default_first_skips_cooling():
    from navig.llm import fallback_policy as fp

    fp.reset_cooldowns()
    conns = [{"connection_id": "A"}, {"connection_id": "B"}, {"connection_id": "C"}]
    fp.mark_cooldown("anthropic:opus@conn:B", fp.RATE_LIMITED)  # B is cooling
    assert _ids(inf.accounts_to_try(conns, "anthropic", "opus")) == ["A", "C"]  # B skipped, order kept
    fp.reset_cooldowns()


def test_accounts_to_try_all_cooling_reprobes_first_or_none():
    from navig.llm import fallback_policy as fp

    fp.reset_cooldowns()
    conns = [{"connection_id": "A"}, {"connection_id": "B"}]
    fp.mark_cooldown("anthropic:opus@conn:A", fp.RATE_LIMITED)
    fp.mark_cooldown("anthropic:opus@conn:B", fp.RATE_LIMITED)
    assert _ids(inf.accounts_to_try(conns, "anthropic", "opus")) == ["A"]  # re-probe the first
    assert inf.accounts_to_try(conns, "anthropic", "opus", reprobe_when_all_cooling=False) == []
    fp.reset_cooldowns()


def test_accounts_to_try_excludes_and_drops_idless():
    conns = [{"connection_id": "A"}, {"connection_id": "B"}, {"name": "no-id"}]
    assert _ids(inf.accounts_to_try(conns, "anthropic", "opus", exclude_connection_id="A")) == ["B"]
    assert inf.accounts_to_try([], "anthropic", "opus") == []


# ── complete_via_connection: account rotation (navig ask path) ──────


class _RateLimited(Exception):
    status_code = 429
    error_type = "rate_limit"


class _DeadModel(Exception):
    status_code = 404


def _two_claude_accounts(monkeypatch):
    a = _conn("A", "Claude A", "claude-max", default=True, model="claude-opus-4-8")
    b = _conn("B", "Claude B", "claude-max", model="claude-opus-4-8")
    _patch_conns(monkeypatch, [a, b])
    from navig.llm import fallback_policy as fp

    fp.reset_cooldowns()


def test_complete_via_connection_rotates_on_rate_limit(monkeypatch):
    _two_claude_accounts(monkeypatch)
    calls: list[str] = []

    def fake_one(conn, provider_id, model, *a, **k):
        calls.append(conn["connection_id"])
        if conn["connection_id"] == "A":
            raise _RateLimited("account rate limit")
        return "ok-from-B"

    monkeypatch.setattr(inf, "_complete_one_account", fake_one)
    events: list[dict] = []
    out = inf.complete_via_connection(
        system_prompt="s", user_content="u", on_fallback=events.append
    )
    assert out == "ok-from-B"
    assert calls == ["A", "B"]                       # tried default A, rotated to B
    assert events[0]["to"] == "Claude B"
    assert events[0]["reason"] == "rate_limited"


def test_pinned_connection_does_not_rotate(monkeypatch):
    _two_claude_accounts(monkeypatch)

    def fake_one(conn, provider_id, model, *a, **k):
        raise _RateLimited("rate limit")

    monkeypatch.setattr(inf, "_complete_one_account", fake_one)
    # Pinning an explicit connection must respect the choice — no silent rotation.
    with pytest.raises(_RateLimited):
        inf.complete_via_connection(
            system_prompt="s", user_content="u", connection_id="A"
        )


def test_non_rotatable_error_does_not_rotate(monkeypatch):
    _two_claude_accounts(monkeypatch)
    calls: list[str] = []

    def fake_one(conn, provider_id, model, *a, **k):
        calls.append(conn["connection_id"])
        raise _DeadModel("model not found")

    monkeypatch.setattr(inf, "_complete_one_account", fake_one)
    # A dead model fails identically on every account → don't waste rotations.
    with pytest.raises(_DeadModel):
        inf.complete_via_connection(system_prompt="s", user_content="u")
    assert calls == ["A"]  # stopped after the first account


def test_single_account_rate_limited_surfaces_without_false_rotation(monkeypatch):
    # The operator's ONLY account is capped: there is no sibling to rotate to, so
    # NAVIG must surface the rate-limit cleanly (and NOT emit a rotation event or
    # claim it "answered with" another account). Mirrors the real report of
    # `navig ask --model anthropic:claude-sonnet-4-6` on a single capped account.
    a = _conn("A", "Claude (Pro/Max subscription)", "claude-max",
              default=True, model="claude-sonnet-4-6")
    _patch_conns(monkeypatch, [a])
    from navig.llm import fallback_policy as fp

    fp.reset_cooldowns()
    calls: list[str] = []

    def fake_one(conn, provider_id, model, *a_, **k_):
        calls.append(conn["connection_id"])
        raise _RateLimited("account rate limit")

    monkeypatch.setattr(inf, "_complete_one_account", fake_one)
    events: list[dict] = []
    with pytest.raises(_RateLimited):
        inf.complete_via_connection(
            system_prompt="s", user_content="u", on_fallback=events.append
        )
    assert calls == ["A"]   # only the one account tried
    assert events == []     # no rotation happened → no "answered with B" event
    fp.reset_cooldowns()


def test_cooling_default_preemptively_skipped_to_sibling(monkeypatch):
    # The default account is already cooling from an earlier cap and a fresh
    # sibling exists → go STRAIGHT to the sibling instead of wasting a round-trip
    # re-dialing the capped default on every call.
    a = _conn("A", "Claude A", "claude-max", default=True, model="claude-sonnet-4-6")
    b = _conn("B", "Claude B", "claude-max", model="claude-sonnet-4-6")
    _patch_conns(monkeypatch, [a, b])
    from navig.llm import fallback_policy as fp

    fp.reset_cooldowns()
    fp.mark_cooldown("anthropic:claude-sonnet-4-6@conn:A", fp.RATE_LIMITED)  # default A capped
    calls: list[str] = []

    def fake_one(conn, provider_id, model, *a_, **k_):
        calls.append(conn["connection_id"])
        return f"ok-{conn['connection_id']}"

    monkeypatch.setattr(inf, "_complete_one_account", fake_one)
    events: list[dict] = []
    out = inf.complete_via_connection(
        system_prompt="s", user_content="u", on_fallback=events.append
    )
    assert out == "ok-B"
    assert calls == ["B"]                     # capped default A never dialed
    assert events[0]["to"] == "Claude B"
    assert events[0]["reason"] == "cooldown"  # skipped for cooldown, not a live error
    fp.reset_cooldowns()


# ── credential resolution under parallel fan-out (the council race) ─────────
#
# Council runs dispatch N agent calls at once from a ThreadPoolExecutor; each
# used to re-run the FULL credential resolution concurrently over a
# non-thread-safe path (shared vault sqlite connection + OAuth refresh), and
# swallowed failures surfaced as the intermittent "No credential configured
# for provider 'anthropic'" while sibling calls succeeded in the same second.
# resolve_provider_credential now serializes per (provider, connection_id) and
# shares one SUCCESSFUL resolution for a short TTL.


@pytest.fixture(autouse=True)
def _fresh_credential_cache():
    """Each test starts and ends with an empty dispatch-credential cache."""
    inf.invalidate_credential_cache()
    yield
    inf.invalidate_credential_cache()


def test_parallel_resolution_is_single_flight_with_no_false_negatives(monkeypatch):
    """8 threads resolving the same provider at once must produce 8 successes
    and enter the (racy) resolution window exactly ONCE.

    The fake bearer refuses concurrent entry — the same shape as the real
    pre-fix failure (parallel entries into the vault/OAuth path blew up and
    were swallowed into (None, None)). Pre-fix this test fails both asserts:
    several threads get (None, None) and the window is entered ~8 times."""
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor

    conn = _conn("a", "Claude", "claude-max", default=True)
    conn["secret_ref"] = "ref-a"
    _patch_conns(monkeypatch, [conn])

    entries = {"n": 0}
    gate = threading.Lock()

    async def _racy_bearer(ref):
        if not gate.acquire(blocking=False):
            raise RuntimeError("concurrent entry into the resolution window")
        try:
            entries["n"] += 1
            await asyncio.sleep(0.05)  # dilate the racy window
            return "tok-abc"
        finally:
            gate.release()

    monkeypatch.setattr(inf, "_oauth_bearer", _racy_bearer)

    barrier = threading.Barrier(8)

    def resolve():
        barrier.wait(timeout=5)
        return inf.resolve_provider_credential("anthropic")

    with ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(resolve) for _ in range(8)]
        results = [f.result(timeout=30) for f in futures]

    assert results == [(None, "tok-abc")] * 8  # nobody got the false (None, None)
    assert entries["n"] == 1                   # fan-out collapsed to ONE resolution


def test_credential_cache_ttl_expiry(monkeypatch):
    """Within the TTL the cached success is reused; after it, resolution
    re-runs — so a revoked/rotated credential surfaces within seconds."""
    import time

    conn = _conn("a", "Claude", "claude-max", default=True)
    conn["secret_ref"] = "ref-a"
    _patch_conns(monkeypatch, [conn])

    calls = {"n": 0}

    async def _bearer(ref):
        calls["n"] += 1
        return f"tok-{calls['n']}"

    monkeypatch.setattr(inf, "_oauth_bearer", _bearer)
    monkeypatch.setattr(inf, "_CRED_TTL_SECONDS", 0.05)

    assert inf.resolve_provider_credential("anthropic") == (None, "tok-1")
    assert inf.resolve_provider_credential("anthropic") == (None, "tok-1")  # cached
    assert calls["n"] == 1
    time.sleep(0.06)  # TTL elapsed
    assert inf.resolve_provider_credential("anthropic") == (None, "tok-2")  # re-resolved
    assert calls["n"] == 2


def test_credential_cache_per_key_isolation(monkeypatch):
    """(provider, connection_id) keys are independent — pinning an account or
    asking for a different provider never reuses another key's credential."""
    a = _conn("A", "Claude A", "claude-max", default=True)
    b = _conn("B", "Claude B", "claude-max")
    a["secret_ref"], b["secret_ref"] = "ref-A", "ref-B"
    o = _conn("o", "OpenAI", "openai-api")
    _patch_conns(monkeypatch, [a, b, o])

    async def _bearer(ref):
        return f"tok-{ref}"

    monkeypatch.setattr(inf, "_oauth_bearer", _bearer)
    monkeypatch.setattr(inf, "_resolve_api_key", lambda conn, pid: "sk-openai")

    assert inf.resolve_provider_credential("anthropic") == (None, "tok-ref-A")
    assert inf.resolve_provider_credential("anthropic", connection_id="B") == (None, "tok-ref-B")
    assert inf.resolve_provider_credential("openai") == ("sk-openai", None)
    # cached entries stay isolated on the second pass too
    assert inf.resolve_provider_credential("anthropic", connection_id="B") == (None, "tok-ref-B")
    assert inf.resolve_provider_credential("anthropic") == (None, "tok-ref-A")


def test_credential_cache_never_caches_failure(monkeypatch):
    """A failed resolution must NOT be cached: the very next call re-probes and
    picks up a credential the operator just connected."""
    _patch_conns(monkeypatch, [])
    state = {"n": 0, "key": None}

    class _FakeAPM:
        def resolve_auth(self, pid):
            state["n"] += 1
            return state["key"], "test"

    monkeypatch.setattr("navig.providers.auth.AuthProfileManager", _FakeAPM)

    assert inf.resolve_provider_credential("anthropic") == (None, None)
    assert inf.resolve_provider_credential("anthropic") == (None, None)
    assert state["n"] == 2  # failure re-probed each time, never served from cache
    state["key"] = "sk-now-connected"
    assert inf.resolve_provider_credential("anthropic") == ("sk-now-connected", None)
    assert state["n"] == 3
    # …and the success IS cached from here on
    assert inf.resolve_provider_credential("anthropic") == ("sk-now-connected", None)
    assert state["n"] == 3


def test_invalidate_credential_cache_scopes_to_provider(monkeypatch):
    """invalidate_credential_cache(provider) drops only that provider's entries
    (disconnect/set-default call this so dispatch re-resolves immediately)."""
    a = _conn("A", "Claude A", "claude-max", default=True)
    a["secret_ref"] = "ref-A"
    o = _conn("o", "OpenAI", "openai-api")
    _patch_conns(monkeypatch, [a, o])

    bearer_calls = {"n": 0}

    async def _bearer(ref):
        bearer_calls["n"] += 1
        return f"tok-{bearer_calls['n']}"

    key_calls = {"n": 0}

    def _key(conn, pid):
        key_calls["n"] += 1
        return "sk-openai"

    monkeypatch.setattr(inf, "_oauth_bearer", _bearer)
    monkeypatch.setattr(inf, "_resolve_api_key", _key)

    assert inf.resolve_provider_credential("anthropic") == (None, "tok-1")
    assert inf.resolve_provider_credential("openai") == ("sk-openai", None)
    inf.invalidate_credential_cache("anthropic")
    assert inf.resolve_provider_credential("anthropic") == (None, "tok-2")  # re-resolved
    assert inf.resolve_provider_credential("openai") == ("sk-openai", None)
    assert key_calls["n"] == 1  # openai entry untouched — still cached
