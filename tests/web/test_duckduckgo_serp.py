"""Regression: keyless DuckDuckGo search must return real SERP results.

The old free-tier path used the Instant Answer API (api.duckduckgo.com), which
returns HTTP 202 (anti-bot) for automated requests and only answers known
entities — so every keyless search came back empty and the agent confabulated
"Not much here". These tests lock in the HTML SERP scrape that replaced it.
"""

from __future__ import annotations

import json

import navig.tools.web as web
from navig.tools.web import (
    WebSearchResult,
    _ddg_unwrap_url,
    _search_duckduckgo,
    _search_keyless,
    _search_mojeek,
    _search_serpapi,
)

_HTML_BODY = """
<div class="result results_links results_links_deep web-result">
  <h2 class="result__title">
    <a rel="nofollow" class="result__a"
       href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fcybesis.com%2F&amp;rut=x">Cybesis Studios &mdash; Home</a>
  </h2>
  <a class="result__snippet" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fcybesis.com%2F">
     Cybesis Studios builds indie games.</a>
</div>
<div class="result results_links results_links_deep web-result">
  <a rel="nofollow" class="result__a"
     href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FCybesis">Cybesis on Wikipedia</a>
  <a class="result__snippet">The studio's Wikipedia page.</a>
</div>
"""

_LITE_BODY = """
<table>
<tr><td class='result-count'>1.</td>
    <td><a rel="nofollow" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fcybesis.io%2F"
           class='result-link'>Cybesis (lite)</a></td></tr>
<tr><td class='result-snippet'>Lite endpoint result.</td></tr>
</table>
"""


class _Resp:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def _make_post(status_by_endpoint: dict[str, _Resp]):
    def _post(url, data=None, headers=None, timeout=None):  # noqa: ANN001, ARG001
        return status_by_endpoint.get(url, _Resp(404, ""))

    return _post


def test_ddg_unwrap_url_decodes_redirect_wrapper():
    wrapped = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fpage%3Fa%3D1&rut=z"
    assert _ddg_unwrap_url(wrapped) == "https://example.com/page?a=1"
    # A plain absolute URL passes through untouched.
    assert _ddg_unwrap_url("https://example.com/x") == "https://example.com/x"
    assert _ddg_unwrap_url("") == ""


def test_ddg_html_endpoint_returns_real_results(monkeypatch):
    monkeypatch.setattr(
        web.requests,
        "post",
        _make_post({web.DUCKDUCKGO_HTML_ENDPOINT: _Resp(200, _HTML_BODY)}),
    )

    result = _search_duckduckgo("Cybesis Studios", count=5)

    assert result.success is True
    assert result.provider == "duckduckgo"
    assert len(result.results) == 2
    top = result.results[0]
    assert top.url == "https://cybesis.com/"  # unwrapped from the /l/?uddg= redirect
    assert top.title == "Cybesis Studios — Home"  # entities decoded, tags stripped
    assert "indie games" in top.snippet
    # No leftover DuckDuckGo redirect wrappers.
    assert all("duckduckgo.com/l/" not in r.url for r in result.results)


def test_ddg_html_202_falls_back_to_lite(monkeypatch):
    """HTTP 202 (anti-bot) on the html endpoint must fall through to lite, not fail."""
    monkeypatch.setattr(
        web.requests,
        "post",
        _make_post(
            {
                web.DUCKDUCKGO_HTML_ENDPOINT: _Resp(202, "<html>challenge</html>"),
                web.DUCKDUCKGO_LITE_ENDPOINT: _Resp(200, _LITE_BODY),
            }
        ),
    )

    result = _search_duckduckgo("Cybesis Studios", count=5)

    assert result.success is True
    assert result.provider == "duckduckgo"
    assert result.results[0].url == "https://cybesis.io/"
    assert result.results[0].title == "Cybesis (lite)"


def test_ddg_all_blocked_returns_honest_error(monkeypatch):
    """When every endpoint 202s, surface an honest 'no results' error — never a
    misleading success — and mention the anti-bot status + how to get a real key."""
    monkeypatch.setattr(
        web.requests,
        "post",
        _make_post(
            {
                web.DUCKDUCKGO_HTML_ENDPOINT: _Resp(202, ""),
                web.DUCKDUCKGO_LITE_ENDPOINT: _Resp(202, ""),
            }
        ),
    )
    # Instant Answer supplement also unavailable.
    monkeypatch.setattr(
        web,
        "_search_duckduckgo_instant",
        lambda q, c=5, t=30: WebSearchResult(
            success=False, query=q, provider="duckduckgo", error="none"
        ),
    )

    result = _search_duckduckgo("Cybesis Studios", count=5)

    assert result.success is False
    assert "202" in (result.error or "")
    assert "no results" in (result.error or "").lower()
    assert "navig config" in (result.error or "")


_MOJEEK_BODY = """
<div class="infobox"><h1>ignore me</h1><a href="https://wikipedia.org/x">infobox</a></div>
<ul class="results-standard">
<!--rs--><li class="r1">
  <a title="https://cybesis.com/" href="https://cybesis.com/" class="ob">
     <p class="i"><span class="url">https://cybesis.com</span></p></a>
  <h2><a class="title" title="https://cybesis.com/" href="https://cybesis.com/">Cybesis Studio &mdash; Home</a></h2>
  <p class="s">Founded in <strong>Montpellier</strong>, France.</p></li><!--re-->
<!--rs--><li class="r2">
  <a title="https://cybesis.com/blog" href="https://cybesis.com/blog" class="ob"></a>
  <h2><a class="title" href="https://cybesis.com/blog">Cybesis Blog</a></h2>
  <p class="s">Digital creative agency news.</p></li><!--re-->
</ul>
"""


class _GetResp:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


def test_mojeek_parses_organic_results(monkeypatch):
    monkeypatch.setattr(
        web.requests, "get", lambda url, **kw: _GetResp(200, _MOJEEK_BODY)  # noqa: ARG005
    )

    result = _search_mojeek("Cybesis Studios", count=5)

    assert result.success is True
    assert result.provider == "mojeek"
    assert len(result.results) == 2
    # The infobox link must NOT be counted as an organic result.
    assert result.results[0].url == "https://cybesis.com/"
    assert result.results[0].title == "Cybesis Studio — Home"
    assert "Montpellier" in result.results[0].snippet
    assert result.results[1].url == "https://cybesis.com/blog"


def test_keyless_prefers_mojeek_and_short_circuits(monkeypatch):
    """Mojeek success must return immediately without touching DuckDuckGo."""
    monkeypatch.setattr(
        web,
        "_search_mojeek",
        lambda q, c=5, t=30: WebSearchResult(
            success=True,
            query=q,
            provider="mojeek",
            results=[web.SearchResult(title="ok", url="https://ok", snippet="")],
        ),
    )

    def _boom(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("DuckDuckGo must not be called when Mojeek succeeds")

    monkeypatch.setattr(web, "_search_duckduckgo", _boom)

    result = _search_keyless("anything", count=5)
    assert result.success is True
    assert result.provider == "mojeek"


def test_keyless_falls_back_to_duckduckgo_when_mojeek_fails(monkeypatch):
    monkeypatch.setattr(
        web,
        "_search_mojeek",
        lambda q, c=5, t=30: WebSearchResult(
            success=False, query=q, provider="mojeek", error="Mojeek returned HTTP 429"
        ),
    )
    monkeypatch.setattr(
        web,
        "_search_duckduckgo",
        lambda q, c=5, t=30: WebSearchResult(
            success=True,
            query=q,
            provider="duckduckgo",
            results=[web.SearchResult(title="ddg", url="https://ddg", snippet="")],
        ),
    )

    result = _search_keyless("anything", count=5)
    assert result.success is True
    assert result.provider == "duckduckgo"


def test_serpapi_parses_organic_results(monkeypatch):
    payload = {
        "organic_results": [
            {"title": "Cybesis Studios", "link": "https://cybesis.com/", "snippet": "Digital agency."},
            {"title": "No link — dropped", "snippet": "should be skipped"},
            {"title": "Second", "link": "https://cybesis.com/blog", "snippet": "Blog."},
        ]
    }

    class _JsonResp:
        status_code = 200
        text = json.dumps(payload)

        def json(self):
            return payload

    monkeypatch.setattr(web.requests, "get", lambda url, **kw: _JsonResp())  # noqa: ARG005

    result = _search_serpapi("Cybesis Studios", "fake-key", count=5)

    assert result.success is True
    assert result.provider == "serpapi"
    assert len(result.results) == 2  # the entry without a link is dropped
    assert result.results[0].url == "https://cybesis.com/"
    assert result.results[1].url == "https://cybesis.com/blog"


def test_serpapi_reports_api_error(monkeypatch):
    class _ErrResp:
        status_code = 200
        text = '{"error": "Invalid API key"}'

        def json(self):
            return {"error": "Invalid API key"}

    monkeypatch.setattr(web.requests, "get", lambda url, **kw: _ErrResp())  # noqa: ARG005

    result = _search_serpapi("q", "bad-key", count=5)
    assert result.success is False
    assert "Invalid API key" in (result.error or "")
