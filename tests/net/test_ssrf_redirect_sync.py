"""safe_get redirect re-validation — the SYNC twin of safe_fetch must re-check
EVERY redirect hop, not just the initial URL, and must ignore a caller's
allow_redirects kwarg (which would otherwise let requests jump to a blocked IP
unchecked).

requests.get is monkeypatched so no real network I/O happens; resolve_host is
mapped per-host so each hop resolves to a known public/blocked IP.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

requests = pytest.importorskip("requests")

from navig.net.ssrf import SsrfBlockedError, safe_get

_REDIRECT_STATI = (301, 302, 303, 307, 308)


class _Resp:
    """Minimal requests.Response stand-in."""

    def __init__(self, status_code: int, headers: dict | None = None, text: str = ""):
        self.status_code = status_code
        self.headers = headers or {}
        self.text = text
        self.url = ""

    @property
    def is_redirect(self) -> bool:
        return self.status_code in _REDIRECT_STATI and "location" in self.headers


def _mock_resolve_map(mapping: dict[str, str], default: str = "8.8.8.8"):
    def _side_effect(host, *a, **k):
        return [mapping.get(host, default)]

    return patch("navig.net.ssrf.resolve_host", side_effect=_side_effect)


def _patch_requests_get(monkeypatch, handler):
    def _fake_get(url, **kw):
        return handler(str(url), **kw)

    monkeypatch.setattr(requests, "get", _fake_get)


def test_safe_get_blocks_redirect_to_internal(monkeypatch):
    def handler(url, **kw):
        if url == "http://start.example/":
            return _Resp(302, headers={"location": "http://evil.internal/meta"})
        raise AssertionError(f"blocked target should never be fetched: {url}")

    _patch_requests_get(monkeypatch, handler)
    with _mock_resolve_map({"start.example": "8.8.8.8", "evil.internal": "127.0.0.1"}):
        with pytest.raises(SsrfBlockedError):
            safe_get("http://start.example/")


def test_safe_get_follows_a_safe_redirect(monkeypatch):
    def handler(url, **kw):
        if url == "http://start.example/":
            return _Resp(302, headers={"location": "http://final.example/data"})
        return _Resp(200, text="landed")

    _patch_requests_get(monkeypatch, handler)
    with _mock_resolve_map({"start.example": "8.8.8.8", "final.example": "93.184.216.34"}):
        resp = safe_get("http://start.example/")
    assert resp.status_code == 200 and resp.text == "landed"


def test_safe_get_allow_redirects_kwarg_is_forced_off(monkeypatch):
    seen: dict = {}

    def handler(url, **kw):
        seen.update(kw)
        return _Resp(302, headers={"location": "http://evil.internal/"})

    _patch_requests_get(monkeypatch, handler)
    with _mock_resolve_map({"start.example": "8.8.8.8", "evil.internal": "169.254.169.254"}):
        with pytest.raises(SsrfBlockedError):
            # Even asked to follow, safe_get must re-check the hop itself.
            safe_get("http://start.example/", allow_redirects=True)
    assert seen.get("allow_redirects") is False  # caller's True stripped, forced False


def test_safe_get_relative_redirect_location_is_checked(monkeypatch):
    def handler(url, **kw):
        if url == "http://start.example/app":
            return _Resp(302, headers={"location": "/internal"})
        raise AssertionError(f"blocked target should never be fetched: {url}")

    _patch_requests_get(monkeypatch, handler)
    calls = {"n": 0}

    def resolve(host, *a, **k):
        calls["n"] += 1
        return ["8.8.8.8"] if calls["n"] == 1 else ["127.0.0.1"]

    with patch("navig.net.ssrf.resolve_host", side_effect=resolve):
        with pytest.raises(SsrfBlockedError):
            safe_get("http://start.example/app")


def test_safe_get_too_many_redirects_raises_valueerror(monkeypatch):
    def handler(url, **kw):
        return _Resp(302, headers={"location": url + "x"})

    _patch_requests_get(monkeypatch, handler)
    with _mock_resolve_map({}, default="8.8.8.8"):
        with pytest.raises(ValueError):
            safe_get("http://loop.example/", max_redirects=3)


def test_safe_get_initial_block_never_touches_the_network(monkeypatch):
    called = {"n": 0}

    def handler(url, **kw):
        called["n"] += 1
        return _Resp(200)

    _patch_requests_get(monkeypatch, handler)
    with _mock_resolve_map({"start.internal": "10.0.0.5"}):
        with pytest.raises(SsrfBlockedError):
            safe_get("http://start.internal/")
    assert called["n"] == 0  # check_url rejected it before any requests.get call
