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
    """

    class _EmptyVault:
        def get_secret(self, label: str) -> str:
            return ""

    monkeypatch.setattr("navig.vault.core.get_vault", lambda: _EmptyVault())


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
