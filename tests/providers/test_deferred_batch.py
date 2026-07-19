"""
Regression tests for the deferred-findings batch:
  * config#2  ai.default_provider must not emit an invalid provider:model pair.
  * conn#5    a bad shared-BYOK `connect add` (INVALID) must roll back to the
              previous key, not clobber a working `navig ai` key.
  * conn#6    OAuth exchange must reject a mismatched state on the paste path.
"""

from __future__ import annotations

import pytest

from navig.providers.connect import connect_provider
from navig.providers.connection_types import AuthState
from navig.providers.drivers.fake import FakeDriver

# ── config#2: default_provider override ──────────────────────────────────────


def test_default_provider_uses_provider_default_model():
    from navig.llm.router import LLMModeRouter

    r = LLMModeRouter(config={"ai": {"default_provider": "anthropic"}})
    cfg = r.get_config("big_tasks")  # default mode is openai:gpt-4o
    assert cfg.provider == "anthropic"
    assert cfg.model == "claude-sonnet-4-6"  # the provider default, NOT gpt-4o (would 404)


def test_default_provider_unknown_skips_override():
    from navig.llm.router import LLMModeRouter

    r = LLMModeRouter(config={"ai": {"default_provider": "no-such-provider"}})
    cfg = r.get_config("big_tasks")
    # Override skipped: keeps a valid mode pair rather than emitting an invalid
    # 'no-such-provider:<some other provider's model>' (the whole point of the fix).
    assert cfg.provider != "no-such-provider"
    assert cfg.model  # a real, non-empty model for whatever provider was kept


# ── conn#5: BYOK rollback on a bad key ───────────────────────────────────────


async def test_bad_byok_key_rolls_back_to_previous(tmp_path, isolate_shared_auth):
    from navig.providers.connections import ConnectionStore

    store = ConnectionStore(tmp_path / "c.db")
    shared = isolate_shared_auth
    shared["openai"] = "sk-good-existing"  # a working key already configured

    conn = await connect_provider(
        "openai-api", api_key="sk-bad", store=store, driver=FakeDriver(healthy=False),
    )
    assert conn.auth_state == AuthState.NEEDS_REAUTH
    assert shared["openai"] == "sk-good-existing"  # rolled back, not clobbered


async def test_bad_byok_key_with_no_previous_is_removed(tmp_path, isolate_shared_auth):
    from navig.providers.connections import ConnectionStore

    store = ConnectionStore(tmp_path / "c.db")
    shared = isolate_shared_auth  # starts empty

    await connect_provider(
        "openai-api", api_key="sk-bad", store=store, driver=FakeDriver(healthy=False),
    )
    assert "openai" not in shared  # bad key not left behind


# ── conn#6: OAuth state verification ─────────────────────────────────────────


async def test_claude_oauth_rejects_state_mismatch():
    from navig.providers.claude_oauth import ClaudeOAuthFlow, exchange_code

    flow = ClaudeOAuthFlow(state="EXPECTED", code_verifier="v")
    with pytest.raises(RuntimeError, match="state mismatch"):
        await exchange_code("thecode#WRONGSTATE", flow)


async def test_codex_oauth_rejects_state_mismatch():
    from navig.providers.codex_oauth import CodexOAuthFlow, exchange_code

    flow = CodexOAuthFlow(state="EXPECTED", code_verifier="v")
    with pytest.raises(RuntimeError, match="state mismatch"):
        await exchange_code(
            "http://localhost:1455/cb?code=x&state=WRONG", flow,
        )


def test_oauth_extract_state_helpers():
    from navig.providers import claude_oauth, codex_oauth

    assert claude_oauth._extract_state("code#abc") == "abc"
    assert claude_oauth._extract_state("code-no-state") is None
    assert codex_oauth._extract_state("https://x/cb?code=1&state=s9") == "s9"
    assert codex_oauth._extract_state("bare-code") is None
