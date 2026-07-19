"""safe_fetch redirect re-validation — the SSRF guard must re-check EVERY redirect
hop, not just the initial URL, and must ignore a caller's follow_redirects kwarg
(which would otherwise let httpx jump to a blocked IP unchecked).

httpx is monkeypatched so no real network I/O happens; resolve_host is mapped
per-host so each hop resolves to a known public/blocked IP.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

httpx = pytest.importorskip("httpx")

from navig.net.ssrf import SsrfBlockedError, safe_fetch


def _mock_resolve_map(mapping: dict[str, str], default: str = "8.8.8.8"):
    """Patch resolve_host so each hostname resolves to a chosen IP (default public)."""
    def _side_effect(host, *a, **k):
        return [mapping.get(host, default)]

    return patch("navig.net.ssrf.resolve_host", side_effect=_side_effect)


def _patch_httpx_get(monkeypatch, handler):
    """Replace httpx.AsyncClient.get with *handler(url, **kw) -> httpx.Response*."""
    async def _fake_get(self, url, **kw):
        resp = handler(str(url), **kw)
        resp.request = httpx.Request("GET", str(url))
        return resp

    monkeypatch.setattr(httpx.AsyncClient, "get", _fake_get)


async def test_blocks_redirect_to_internal(monkeypatch):
    # start.example (public) 302s to evil.internal (loopback) → must be blocked.
    def handler(url, **kw):
        if url == "http://start.example/":
            return httpx.Response(302, headers={"location": "http://evil.internal/meta"})
        raise AssertionError(f"blocked target should never be fetched: {url}")

    _patch_httpx_get(monkeypatch, handler)
    with _mock_resolve_map({"start.example": "8.8.8.8", "evil.internal": "127.0.0.1"}):
        with pytest.raises(SsrfBlockedError):
            await safe_fetch("http://start.example/")


async def test_follows_a_safe_redirect(monkeypatch):
    def handler(url, **kw):
        if url == "http://start.example/":
            return httpx.Response(302, headers={"location": "http://final.example/data"})
        return httpx.Response(200, text="landed")

    _patch_httpx_get(monkeypatch, handler)
    with _mock_resolve_map({"start.example": "8.8.8.8", "final.example": "93.184.216.34"}):
        resp = await safe_fetch("http://start.example/")
    assert resp.status_code == 200 and resp.text == "landed"


async def test_follow_redirects_kwarg_is_ignored_and_stripped(monkeypatch):
    seen_kwargs = {}

    def handler(url, **kw):
        seen_kwargs.update(kw)
        return httpx.Response(302, headers={"location": "http://evil.internal/"})

    _patch_httpx_get(monkeypatch, handler)
    with _mock_resolve_map({"start.example": "8.8.8.8", "evil.internal": "169.254.169.254"}):
        with pytest.raises(SsrfBlockedError):
            # Even with follow_redirects=True, safe_fetch must re-check the hop.
            await safe_fetch("http://start.example/", follow_redirects=True)
    assert "follow_redirects" not in seen_kwargs  # stripped before httpx sees it


async def test_relative_redirect_location_is_resolved_and_checked(monkeypatch):
    # A relative Location must be joined against the current URL, then re-checked.
    def handler(url, **kw):
        if url == "http://start.example/app":
            return httpx.Response(302, headers={"location": "/internal"})
        raise AssertionError(f"blocked target should never be fetched: {url}")

    _patch_httpx_get(monkeypatch, handler)
    # start.example resolves public; the joined http://start.example/internal then
    # resolves to loopback (a rebind-style split) → blocked.
    calls = {"n": 0}

    def resolve(host, *a, **k):
        calls["n"] += 1
        return ["8.8.8.8"] if calls["n"] == 1 else ["127.0.0.1"]

    with patch("navig.net.ssrf.resolve_host", side_effect=resolve):
        with pytest.raises(SsrfBlockedError):
            await safe_fetch("http://start.example/app")


async def test_too_many_redirects_raises_valueerror(monkeypatch):
    # Every hop redirects onward to a public host → never terminates.
    def handler(url, **kw):
        return httpx.Response(302, headers={"location": url + "x"})

    _patch_httpx_get(monkeypatch, handler)
    with _mock_resolve_map({}, default="8.8.8.8"):
        with pytest.raises(ValueError):
            await safe_fetch("http://loop.example/", max_redirects=3)


async def test_initial_block_never_touches_the_network(monkeypatch):
    called = {"n": 0}

    def handler(url, **kw):
        called["n"] += 1
        return httpx.Response(200)

    _patch_httpx_get(monkeypatch, handler)
    with _mock_resolve_map({"start.internal": "10.0.0.5"}):
        with pytest.raises(SsrfBlockedError):
            await safe_fetch("http://start.internal/")
    assert called["n"] == 0  # check_url rejected it before any httpx call
