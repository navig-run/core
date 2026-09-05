"""attachment_bytes resolution — data / path / SSRF-guarded url.

Regression for the "phantom media" class: a URL attachment used to resolve ONLY when the
caller happened to pass an aiohttp session (``if url and session is not None``), so with no
session the URL was silently dropped and the send degraded to text-only. URL media now
resolves through the SSRF-guarded ``safe_fetch`` — no caller session needed, and a URL that
targets an internal/redirected address is blocked, not fetched.
"""

from __future__ import annotations

import base64

from navig.messaging.attachments import attachment_bytes


class _FakeResp:
    def __init__(self, status_code: int, content: bytes) -> None:
        self.status_code = status_code
        self.content = content


async def test_data_bytes_resolve():
    assert await attachment_bytes({"data": b"hello"}) == b"hello"


async def test_data_base64_str_resolves():
    enc = base64.b64encode(b"world").decode()
    assert await attachment_bytes({"data": enc}) == b"world"


async def test_path_resolves(tmp_path):
    f = tmp_path / "a.bin"
    f.write_bytes(b"filebytes")
    assert await attachment_bytes({"path": str(f)}) == b"filebytes"


async def test_path_missing_returns_none(tmp_path):
    assert await attachment_bytes({"path": str(tmp_path / "nope.bin")}) is None


async def test_url_resolves_without_a_session(monkeypatch):
    """The bug: URL media was dropped when no session was supplied."""

    async def _fake(url, policy, **kw):
        return _FakeResp(200, b"urlbytes")

    monkeypatch.setattr("navig.net.ssrf.safe_fetch", _fake)
    assert await attachment_bytes({"url": "https://cdn.example/x.png"}) == b"urlbytes"


async def test_url_goes_through_ssrf_guard_not_the_session(monkeypatch):
    """Security: even with a session passed, URLs must go through safe_fetch (which
    re-validates redirects), never the caller's session (which would not)."""

    async def _fake(url, policy, **kw):
        return _FakeResp(200, b"viasafe")

    monkeypatch.setattr("navig.net.ssrf.safe_fetch", _fake)

    class _BadSession:
        def get(self, *a, **k):
            raise AssertionError("session.get must NOT be used for URL fetches")

    out = await attachment_bytes({"url": "https://cdn.example/x"}, session=_BadSession())
    assert out == b"viasafe"


async def test_url_blocked_by_ssrf_returns_none(monkeypatch):
    from navig.net.ssrf import SsrfBlockedError

    async def _blocked(url, policy, **kw):
        raise SsrfBlockedError("blocked: 169.254.169.254")

    monkeypatch.setattr("navig.net.ssrf.safe_fetch", _blocked)
    assert await attachment_bytes({"url": "http://169.254.169.254/latest/meta-data"}) is None


async def test_url_non_200_returns_none(monkeypatch):
    async def _fake(url, policy, **kw):
        return _FakeResp(404, b"")

    monkeypatch.setattr("navig.net.ssrf.safe_fetch", _fake)
    assert await attachment_bytes({"url": "https://cdn.example/missing.png"}) is None


async def test_url_network_error_returns_none(monkeypatch):
    async def _boom(url, policy, **kw):
        raise ConnectionError("dns failure")

    monkeypatch.setattr("navig.net.ssrf.safe_fetch", _boom)
    assert await attachment_bytes({"url": "https://cdn.example/x"}) is None


async def test_empty_descriptor_returns_none():
    assert await attachment_bytes({}) is None
