from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from navig.cli import app
from navig.integrations.firecrawl.client import FirecrawlError
from navig.tools.web import SearchResult, WebSearchResult, web_search

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _isolate_vault(monkeypatch):
    """Keep provider-selection tests independent of the machine's real vault.

    These tests assert selection logic given config/env; without isolation they
    would resolve whatever real search keys happen to live in the operator's
    vault. Individual tests override this with their own vault stub.
    ``vault_exists`` is forced True so the stub is actually consulted (a real
    existence check would read the machine and skip the stub entirely).
    Also clears the env provider override — it now genuinely beats config.
    """

    class _EmptyVault:
        def get_secret(self, label: str) -> str:
            return ""

    monkeypatch.setattr("navig.vault.core.get_vault", lambda: _EmptyVault())
    monkeypatch.setattr("navig.vault.vault_exists", lambda: True)
    monkeypatch.delenv("NAVIG_WEB_SEARCH_PROVIDER", raising=False)


class _VaultWithBraveKey:
    def get_secret(self, label: str) -> str:
        if label == "web/brave_api_key":
            return "vault-brave-key"
        return ""


def _ok_brave(
    query: str, api_key: str, count: int = 5, timeout_seconds: int = 30
) -> WebSearchResult:
    return WebSearchResult(
        success=True,
        query=query,
        provider="brave",
        results=[SearchResult(title="t", url="u", snippet=f"k={api_key}")],
    )


def _ok_ddg(query: str, count: int = 5, timeout_seconds: int = 30) -> WebSearchResult:
    return WebSearchResult(
        success=True,
        query=query,
        provider="duckduckgo",
        results=[SearchResult(title="t", url="u", snippet="ddg")],
    )


def test_web_search_explicit_provider_brave_uses_brave(monkeypatch):
    monkeypatch.setattr("navig.tools.web.REQUESTS_AVAILABLE", True)
    monkeypatch.setattr("navig.tools.web._search_brave", _ok_brave)
    monkeypatch.setattr("navig.tools.web._search_keyless", _ok_ddg)
    monkeypatch.setattr(
        "navig.tools.web.get_web_config",
        lambda config_manager=None: {
            "search": {
                "provider": "duckduckgo",
                "api_key": "cfg-brave-key",
                "api_keys": {},
            }
        },
    )

    result = web_search("python", provider="brave", use_cache=False)
    assert result.success is True
    assert result.provider == "brave"
    assert result.results and "cfg-brave-key" in result.results[0].snippet


def test_web_search_auto_uses_vault_key_before_config(monkeypatch):
    monkeypatch.setattr("navig.tools.web.REQUESTS_AVAILABLE", True)
    monkeypatch.setattr("navig.tools.web._search_brave", _ok_brave)
    monkeypatch.setattr("navig.tools.web._search_keyless", _ok_ddg)
    monkeypatch.setattr("navig.vault.core.get_vault", lambda: _VaultWithBraveKey())
    monkeypatch.setattr(
        "navig.tools.web.get_web_config",
        lambda config_manager=None: {
            "search": {
                "provider": "brave",
                "api_key": "cfg-brave-key",
                "api_keys": {},
            }
        },
    )

    result = web_search("python", provider="auto", use_cache=False)
    assert result.success is True
    assert result.provider == "brave"
    assert result.results and "vault-brave-key" in result.results[0].snippet


def test_web_search_unsupported_provider_falls_back_to_duckduckgo(monkeypatch):
    monkeypatch.setattr("navig.tools.web.REQUESTS_AVAILABLE", True)
    monkeypatch.setattr("navig.tools.web._search_brave", _ok_brave)
    monkeypatch.setattr("navig.tools.web._search_keyless", _ok_ddg)
    monkeypatch.setattr(
        "navig.tools.web.get_web_config",
        lambda config_manager=None: {
            "search": {
                "provider": "perplexity",
                "api_key": "",
                "api_keys": {},
            }
        },
    )

    result = web_search("python", provider="auto", use_cache=False)
    assert result.success is True
    assert result.provider == "duckduckgo"


def test_cli_search_forwards_provider_option(monkeypatch):
    runner = CliRunner()
    seen: dict[str, Any] = {}

    def _fake_web_search(*, query: str, count: int, provider: str, **kwargs):
        seen["query"] = query
        seen["count"] = count
        seen["provider"] = provider
        return WebSearchResult(success=True, query=query, provider=provider, results=[])

    monkeypatch.setattr("navig.tools.web.web_search", _fake_web_search)

    result = runner.invoke(app, ["search", "hello world", "--provider", "brave", "--limit", "3"])
    assert result.exit_code == 0, result.output
    assert seen == {"query": "hello world", "count": 3, "provider": "brave"}


def _ok_tavily(
    query: str, api_key: str, count: int = 5, timeout_seconds: int = 30
) -> WebSearchResult:
    return WebSearchResult(
        success=True,
        query=query,
        provider="tavily",
        results=[SearchResult(title="t", url="u", snippet=f"k={api_key}")],
    )


def test_web_search_explicit_provider_tavily_uses_tavily(monkeypatch):
    """Tavily is a first-class runtime provider — selecting it should call _search_tavily,
    not fall back to DuckDuckGo (regression guard for #42 follow-up)."""
    monkeypatch.setattr("navig.tools.web.REQUESTS_AVAILABLE", True)
    monkeypatch.setattr("navig.tools.web._search_brave", _ok_brave)
    monkeypatch.setattr("navig.tools.web._search_keyless", _ok_ddg)
    monkeypatch.setattr("navig.tools.web._search_tavily", _ok_tavily)
    monkeypatch.setattr(
        "navig.tools.web.get_web_config",
        lambda config_manager=None: {
            "search": {
                "provider": "tavily",
                "api_key": "cfg-tavily-key",
                "api_keys": {"tavily": "cfg-tavily-key"},
            }
        },
    )

    result = web_search("python", provider="tavily", use_cache=False)
    assert result.success is True
    assert result.provider == "tavily"
    assert result.results and "cfg-tavily-key" in result.results[0].snippet


def test_web_search_explicit_firecrawl_without_key_returns_error(monkeypatch):
    monkeypatch.setattr("navig.tools.web.REQUESTS_AVAILABLE", True)
    monkeypatch.setattr(
        "navig.integrations.firecrawl.get_firecrawl_client",
        lambda: (_ for _ in ()).throw(
            FirecrawlError(
                "FIRECRAWL_API_KEY is required. Set it via env or `navig vault add firecrawl --key ...`.",
                status_code=401,
            )
        ),
    )
    monkeypatch.setattr("navig.tools.web._search_keyless", _ok_ddg)
    monkeypatch.setattr(
        "navig.tools.web.get_web_config",
        lambda config_manager=None: {
            "search": {
                "provider": "firecrawl",
                "api_key": "",
                "api_keys": {},
            }
        },
    )

    result = web_search("python", provider="firecrawl", use_cache=False)

    assert result.success is False
    assert result.provider == "firecrawl"
    assert result.error and "FIRECRAWL_API_KEY is required" in result.error


class _SecretStrVault:
    """Vault that returns keys as SecretStr under the BARE provider label — exactly
    how the real vault stores `navig vault add <provider>` keys."""

    def __init__(self, mapping: dict[str, str]) -> None:
        self._mapping = mapping

    def get_secret(self, label: str):
        from navig.vault.secret_str import SecretStr

        if label in self._mapping:
            return SecretStr(self._mapping[label])
        raise KeyError(label)


def test_web_search_resolves_bare_vault_label_and_unwraps_secretstr(monkeypatch):
    """Regression: keys live under the bare provider label ('tavily') and come back
    as SecretStr. The old resolver checked only compound labels AND called .strip()
    on the SecretStr (which threw and was swallowed), so real keys were never used
    and every search silently degraded to the keyless path."""
    monkeypatch.setattr("navig.tools.web.REQUESTS_AVAILABLE", True)
    monkeypatch.setattr(
        "navig.vault.core.get_vault",
        lambda: _SecretStrVault({"tavily": "sk-tavily-real"}),
    )
    monkeypatch.setattr(
        "navig.integrations.firecrawl.get_firecrawl_client",
        lambda: (_ for _ in ()).throw(FirecrawlError("no key", status_code=401)),
    )
    captured: dict[str, Any] = {}

    def _cap_tavily(query, api_key, count=5, timeout_seconds=30):
        captured["key"] = api_key
        return WebSearchResult(
            success=True,
            query=query,
            provider="tavily",
            results=[SearchResult(title="t", url="u", snippet="ok")],
        )

    monkeypatch.setattr("navig.tools.web._search_tavily", _cap_tavily)
    monkeypatch.setattr(
        "navig.tools.web.get_web_config",
        lambda config_manager=None: {
            "search": {"provider": "auto", "api_key": "", "api_keys": {}}
        },
    )

    result = web_search("anything", provider="auto", use_cache=False)

    assert result.success is True
    assert result.provider == "tavily"
    # SecretStr was unwrapped via reveal() and the bare 'tavily' label was found.
    assert captured["key"] == "sk-tavily-real"


def test_web_search_auto_cascades_past_failing_keyed_provider(monkeypatch):
    """A keyed provider that ERRORS (bad key/quota) must fall through to the next
    engine, not dead-end — the old firecrawl-fallback picked Tavily on key-presence
    alone and never tried Brave if Tavily failed."""
    monkeypatch.setattr("navig.tools.web.REQUESTS_AVAILABLE", True)
    monkeypatch.setattr(
        "navig.vault.core.get_vault",
        lambda: _SecretStrVault({"tavily": "bad", "brave": "good"}),
    )
    monkeypatch.setattr(
        "navig.integrations.firecrawl.get_firecrawl_client",
        lambda: (_ for _ in ()).throw(FirecrawlError("no key", status_code=401)),
    )
    monkeypatch.setattr(
        "navig.tools.web._search_tavily",
        lambda q, k, c=5, t=30: WebSearchResult(
            success=False, query=q, provider="tavily", error="quota exceeded"
        ),
    )
    monkeypatch.setattr("navig.tools.web._search_brave", _ok_brave)
    monkeypatch.setattr(
        "navig.tools.web.get_web_config",
        lambda config_manager=None: {
            "search": {"provider": "auto", "api_key": "", "api_keys": {}}
        },
    )

    result = web_search("anything", provider="auto", use_cache=False)

    assert result.success is True
    assert result.provider == "brave"  # cascaded past the failing Tavily
    assert result.results and "good" in result.results[0].snippet


def test_web_search_cache_hit_returns_searchresult_objects(monkeypatch):
    """Regression: a repeat search within the TTL must return SearchResult objects, not
    raw dicts. Results are cached as dicts and the read path did WebSearchResult(**cached)
    with no rehydration, so every consumer's r.title raised AttributeError on the 2nd call
    (MCP web-search, `navig docs` search, the deck board deep-dive, the agent search tool)."""
    import navig.tools.web as web

    web._search_cache.clear()
    monkeypatch.setattr("navig.tools.web.REQUESTS_AVAILABLE", True)
    monkeypatch.setattr("navig.tools.web._search_brave", _ok_brave)
    monkeypatch.setattr("navig.tools.web._search_keyless", _ok_ddg)
    monkeypatch.setattr(
        "navig.tools.web.get_web_config",
        lambda config_manager=None: {
            "search": {"provider": "brave", "api_key": "k", "api_keys": {}}
        },
    )

    try:
        # First call runs the provider and populates the cache (as dicts).
        first = web_search("cache me", provider="brave", api_key="k", use_cache=True)
        assert first.success is True and first.cached is False
        assert first.results and isinstance(first.results[0], SearchResult)

        # Second identical call hits the cache — must rehydrate to SearchResult.
        second = web_search("cache me", provider="brave", api_key="k", use_cache=True)
        assert second.cached is True
        assert second.results, "cache hit dropped the results"
        for r in second.results:
            assert isinstance(r, SearchResult)  # NOT a dict
            # attribute access — exactly what every consumer does — must not raise
            _ = (r.title, r.url, r.snippet, r.age)
        assert second.results[0].title == first.results[0].title
        assert second.results[0].snippet == first.results[0].snippet
    finally:
        web._search_cache.clear()


def test_web_search_explicit_key_not_used_for_wrong_provider_in_auto(monkeypatch):
    """An explicit api_key is documented as the BRAVE key. In the auto-cascade it must NOT
    be handed to Tavily/SerpApi (a Brave key fired at the wrong API → 401) and must NOT
    mask the user's real vaulted per-provider keys. Regression for the correctness-hunt
    finding: the old code short-circuited `return explicit` for every provider_name."""
    monkeypatch.setattr("navig.tools.web.REQUESTS_AVAILABLE", True)
    monkeypatch.setattr(
        "navig.vault.core.get_vault",
        lambda: _SecretStrVault({"tavily": "real-tavily-key"}),
    )
    monkeypatch.setattr(
        "navig.integrations.firecrawl.get_firecrawl_client",
        lambda: (_ for _ in ()).throw(FirecrawlError("no key", status_code=401)),
    )
    captured: dict[str, Any] = {}

    def _cap_tavily(query, api_key, count=5, timeout_seconds=30):
        captured["tavily_key"] = api_key
        return WebSearchResult(
            success=True,
            query=query,
            provider="tavily",
            results=[SearchResult(title="t", url="u", snippet="ok")],
        )

    monkeypatch.setattr("navig.tools.web._search_tavily", _cap_tavily)
    monkeypatch.setattr(
        "navig.tools.web.get_web_config",
        lambda config_manager=None: {
            "search": {"provider": "auto", "api_key": "", "api_keys": {}}
        },
    )

    # Caller passes an explicit Brave key, exactly as the MCP tool forwards BRAVE_API_KEY.
    result = web_search(
        "anything", provider="auto", api_key="brave-explicit", use_cache=False
    )

    assert result.success is True
    assert result.provider == "tavily"
    # Tavily received its OWN vaulted key — never the Brave explicit key.
    assert captured["tavily_key"] == "real-tavily-key"


def test_web_search_explicit_key_still_honored_for_brave(monkeypatch):
    """Backward-compat: the explicit api_key IS the Brave key, so it must still be used
    when Brave runs — both when Brave is explicitly requested and when the cascade
    reaches Brave."""
    monkeypatch.setattr("navig.tools.web.REQUESTS_AVAILABLE", True)
    monkeypatch.setattr("navig.tools.web._search_brave", _ok_brave)
    monkeypatch.setattr("navig.tools.web._search_keyless", _ok_ddg)
    # No Firecrawl key → the auto path falls through to the keyed cascade (reaches Brave).
    monkeypatch.setattr(
        "navig.integrations.firecrawl.get_firecrawl_client",
        lambda: (_ for _ in ()).throw(FirecrawlError("no key", status_code=401)),
    )
    monkeypatch.setattr(
        "navig.tools.web.get_web_config",
        lambda config_manager=None: {
            "search": {"provider": "auto", "api_key": "", "api_keys": {}}
        },
    )

    # Explicitly requested Brave with an explicit key.
    explicit = web_search(
        "python", provider="brave", api_key="brave-explicit", use_cache=False
    )
    assert explicit.success is True and explicit.provider == "brave"
    assert explicit.results and "brave-explicit" in explicit.results[0].snippet

    # And when the auto-cascade reaches Brave, the explicit Brave key is still applied.
    cascaded = web_search(
        "python", provider="auto", api_key="brave-explicit", use_cache=False
    )
    assert cascaded.success is True and cascaded.provider == "brave"
    assert cascaded.results and "brave-explicit" in cascaded.results[0].snippet


# ── deepseek_search provider ─────────────────────────────────────────────────


def _ok_deepseek(
    query: str, api_key: str, count: int = 5, timeout_seconds: int = 30
) -> WebSearchResult:
    return WebSearchResult(
        success=True,
        query=query,
        provider="deepseek_search",
        results=[SearchResult(title="t", url="u", snippet=f"k={api_key}")],
    )


def test_web_search_explicit_deepseek_uses_vault_key(monkeypatch):
    monkeypatch.setattr("navig.tools.web.REQUESTS_AVAILABLE", True)
    monkeypatch.setattr("navig.tools.web._search_deepseek", _ok_deepseek)
    monkeypatch.setattr("navig.tools.web._search_keyless", _ok_ddg)
    monkeypatch.setattr(
        "navig.vault.core.get_vault",
        lambda: _SecretStrVault({"deepseek_search": "sk-ds-real"}),
    )
    monkeypatch.setattr(
        "navig.tools.web.get_web_config",
        lambda config_manager=None: {
            "search": {"provider": "auto", "api_key": "", "api_keys": {}}
        },
    )

    result = web_search("python", provider="deepseek_search", use_cache=False)

    assert result.success is True
    assert result.provider == "deepseek_search"
    assert result.results and "sk-ds-real" in result.results[0].snippet


def test_web_search_deepseek_alias_normalizes(monkeypatch):
    """`--provider deepseek` (and config value `deepseek`) resolve to deepseek_search."""
    monkeypatch.setattr("navig.tools.web.REQUESTS_AVAILABLE", True)
    monkeypatch.setattr("navig.tools.web._search_deepseek", _ok_deepseek)
    monkeypatch.setattr("navig.tools.web._search_keyless", _ok_ddg)
    monkeypatch.setattr(
        "navig.vault.core.get_vault",
        lambda: _SecretStrVault({"deepseek_search": "sk-ds"}),
    )
    monkeypatch.setattr(
        "navig.tools.web.get_web_config",
        lambda config_manager=None: {
            "search": {"provider": "auto", "api_key": "", "api_keys": {}}
        },
    )

    result = web_search("python", provider="deepseek", use_cache=False)

    assert result.success is True
    assert result.provider == "deepseek_search"


def test_web_search_deepseek_without_key_fails_loudly(monkeypatch):
    """The configured engine must NEVER silently answer as a keyless engine —
    attribution stays honest (deliberate deviation from tavily/brave's silent
    drop to Mojeek/DDG)."""
    monkeypatch.setattr("navig.tools.web.REQUESTS_AVAILABLE", True)
    monkeypatch.setattr("navig.tools.web._search_keyless", _ok_ddg)
    monkeypatch.delenv("DEEPSEEK_SEARCH_API_KEY", raising=False)
    monkeypatch.setattr(
        "navig.tools.web.get_web_config",
        lambda config_manager=None: {
            "search": {"provider": "deepseek_search", "api_key": "", "api_keys": {}}
        },
    )

    result = web_search("python", provider="auto", use_cache=False)

    assert result.success is False
    assert result.provider == "deepseek_search"
    assert result.error and "navig vault set deepseek_search/api_key" in result.error


def test_web_search_auto_cascade_runs_deepseek_before_tavily(monkeypatch):
    """Auto order pin: DeepSeek first among keyed engines; a failing DeepSeek
    cascades to Tavily (not dead-end, not straight to keyless)."""
    monkeypatch.setattr("navig.tools.web.REQUESTS_AVAILABLE", True)
    monkeypatch.setattr(
        "navig.vault.core.get_vault",
        lambda: _SecretStrVault({"deepseek_search": "ds", "tavily": "tv"}),
    )
    monkeypatch.setattr(
        "navig.integrations.firecrawl.get_firecrawl_client",
        lambda: (_ for _ in ()).throw(FirecrawlError("no key", status_code=401)),
    )
    order: list[str] = []

    def _fail_deepseek(query, api_key, count=5, timeout_seconds=30):
        order.append("deepseek_search")
        return WebSearchResult(
            success=False, query=query, provider="deepseek_search", error="quota"
        )

    monkeypatch.setattr("navig.tools.web._search_deepseek", _fail_deepseek)
    monkeypatch.setattr(
        "navig.tools.web._search_tavily",
        lambda q, k, c=5, t=30: (
            order.append("tavily")
            or WebSearchResult(
                success=True,
                query=q,
                provider="tavily",
                results=[SearchResult(title="t", url="u", snippet="ok")],
            )
        ),
    )
    monkeypatch.setattr("navig.tools.web._search_brave", lambda *a, **k: order.append("brave"))
    monkeypatch.setattr("navig.tools.web._search_keyless", _ok_ddg)
    monkeypatch.setattr(
        "navig.tools.web.get_web_config",
        lambda config_manager=None: {
            "search": {"provider": "auto", "api_key": "", "api_keys": {}}
        },
    )

    result = web_search("anything", provider="auto", use_cache=False)

    assert result.success is True and result.provider == "tavily"
    assert order == ["deepseek_search", "tavily"]


def test_cli_provider_flag_beats_env_and_config(monkeypatch):
    """Precedence contract: a non-auto --provider wins over env AND config."""
    monkeypatch.setattr("navig.tools.web.REQUESTS_AVAILABLE", True)
    monkeypatch.setattr("navig.tools.web._search_brave", _ok_brave)
    monkeypatch.setenv("NAVIG_WEB_SEARCH_PROVIDER", "mojeek")
    from navig.tools.web import get_web_config as _real_get_web_config

    class _CM:
        def get(self, key, default=None):
            return {"search": {"provider": "tavily", "api_key": "", "api_keys": {"brave": "b-key"}}} if key == "web" else default

    monkeypatch.setattr(
        "navig.tools.web.get_web_config",
        lambda config_manager=None: _real_get_web_config(_CM()),
    )

    result = web_search("python", provider="brave", use_cache=False)

    assert result.success is True
    assert result.provider == "brave"


# ── provider_status() — the /search/status payload ──────────────────────────

_KEY_ENV_VARS = (
    "BRAVE_API_KEY",
    "TAVILY_API_KEY",
    "SERPAPI_KEY",
    "SERPAPI_API_KEY",
    "FIRECRAWL_API_KEY",
    "DEEPSEEK_SEARCH_API_KEY",
)


def test_provider_status_reports_keys_cascade_and_coercion(monkeypatch):
    """Status shows key PRESENCE (never material), the cascade, and coerces the
    string 'false' kill switch the way `navig config set` stores it."""
    from navig.tools.web import provider_status

    for var in _KEY_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        "navig.vault.core.get_vault",
        lambda: _SecretStrVault({"deepseek_search": "sk-ds"}),
    )
    monkeypatch.setattr(
        "navig.tools.web.get_web_config",
        lambda config_manager=None: {
            "search": {
                "provider": "deepseek_search",
                "enabled": "false",
                "api_key": "",
                "api_keys": {},
            }
        },
    )

    status = provider_status()

    assert status["enabled"] is False  # string "false" coerced, not truthy
    assert status["provider"] == "deepseek_search"
    by_id = {p["id"]: p for p in status["providers"]}
    assert by_id["deepseek_search"]["has_key"] is True
    assert by_id["tavily"]["has_key"] is False
    assert by_id["brave"]["has_key"] is False
    assert by_id["firecrawl"]["has_key"] is False
    # Keyless engines need no key — the UI labels them from KEYLESS_PROVIDERS.
    assert by_id["mojeek"]["has_key"] is False
    assert by_id["duckduckgo"]["has_key"] is False
    assert status["cascade"][0] == "deepseek_search"
    assert set(status["cascade"]) == {
        "deepseek_search",
        "tavily",
        "brave",
        "serpapi",
        "mojeek",
        "duckduckgo",
    }
    # Never any key material in the status document.
    assert "sk-ds" not in repr(status)


def test_provider_status_surfaces_unknown_configured_value(monkeypatch):
    """A typo in config must appear as-is in status, not collapse to 'auto'."""
    from navig.tools.web import provider_status

    monkeypatch.setattr("navig.vault.core.get_vault", lambda: _SecretStrVault({}))
    monkeypatch.setattr(
        "navig.tools.web.get_web_config",
        lambda config_manager=None: {
            "search": {"provider": "nonesuch", "enabled": True, "api_keys": {}}
        },
    )

    assert provider_status()["provider"] == "nonesuch"


def test_web_search_firecrawl_uses_a_bare_vault_label(monkeypatch):
    """review F0204: `navig vault add firecrawl` stores the bare 'firecrawl' label; the
    Firecrawl branch ignored the key dispatch resolved and its client never checked it."""
    monkeypatch.delenv("FIRECRAWL_API_KEY", raising=False)
    monkeypatch.setattr("navig.tools.web.REQUESTS_AVAILABLE", True)
    monkeypatch.setattr(
        "navig.vault.core.get_vault",
        lambda: _SecretStrVault({"firecrawl": "fc-bare-key"}),
    )
    captured: dict[str, Any] = {}

    def _search(self, query, limit=5, sources=None, scrape_inline=False):  # noqa: ARG001
        captured["key"] = self.api_key
        return {"data": {"web": [{"title": "t", "url": "https://e.x", "description": "ok"}]}}

    monkeypatch.setattr("navig.integrations.firecrawl.client.FirecrawlClient.search", _search)
    monkeypatch.setattr("navig.tools.web._search_keyless", _ok_ddg)
    monkeypatch.setattr(
        "navig.tools.web.get_web_config",
        lambda config_manager=None: {
            "search": {"provider": "firecrawl", "api_key": "", "api_keys": {}}
        },
    )

    result = web_search("python", provider="firecrawl", use_cache=False)

    assert result.success is True
    assert result.provider == "firecrawl"
    assert captured["key"] == "fc-bare-key"


def test_get_firecrawl_client_resolves_the_bare_vault_label(monkeypatch):
    from navig.integrations.firecrawl import client as fc

    monkeypatch.setattr(
        fc, "resolve_secret", lambda env_vars, vault_labels: "k" if "firecrawl" in vault_labels else ""
    )
    assert fc.get_firecrawl_client().api_key == "k"


def test_serpapi_key_imported_by_onboarding_is_resolved(monkeypatch):
    """review F0205: onboarding imports SERPAPI_KEY under `serpapi/key`."""
    from navig.tools.web import resolve_provider_key

    for var in ("SERPAPI_KEY", "SERPAPI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(
        "navig.vault.core.get_vault",
        lambda: _SecretStrVault({"serpapi/key": "serp-onboarded"}),
    )
    assert resolve_provider_key("serpapi", {}) == "serp-onboarded"
