"""The edge version probe must not send urllib's default User-Agent.

Measured 2026-09-04 against the operator's live edge::

    urllib, default UA  -> HTTPError 403 Forbidden   (0.09s)
    curl, same URL      -> 200 {"version":"1.2.0"}
    urllib + a real UA  -> 200, version 1.2.0

Cloudflare refuses `Python-urllib/3.x` in front of a workers.dev Worker. Every
caller of `_deployed_worker_version` treats "" as "unknown", so that single 403
silently disabled three surfaces at once -- `lighthouse status` ("edge v?"),
`lighthouse version` ("?"), and `navig update`, whose

    if latest and deployed and latest != deployed:

can never be true with `deployed == ""`. So the automatic "your edge is behind,
redeploy?" prompt could not fire for anyone, which is the entire delivery path
for an edge fix. `LIGHTHOUSE_VERSION` is documented as load-bearing for exactly
that comparison -- and the comparison could not run.
"""

from __future__ import annotations

import json
import urllib.request

import pytest

from navig.commands import lighthouse


class _Resp:
    def __init__(self, body: bytes) -> None:
        self._b = body

    def read(self) -> bytes:
        return self._b

    def __enter__(self):
        return self

    def __exit__(self, *a) -> bool:
        return False


def _capture(monkeypatch: pytest.MonkeyPatch, body: dict) -> dict:
    seen: dict = {}

    def fake_urlopen(req, timeout=None):
        seen["req"] = req
        seen["timeout"] = timeout
        return _Resp(json.dumps(body).encode())

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return seen


def test_the_probe_sends_a_non_default_user_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """THE regression. A bare string URL means urllib's default UA, which is 403'd."""
    seen = _capture(monkeypatch, {"version": "1.2.0"})

    got = lighthouse._deployed_worker_version("https://edge.example.workers.dev")

    assert got == "1.2.0"
    req = seen["req"]
    assert isinstance(req, urllib.request.Request), (
        "the probe passed a bare URL to urlopen, so urllib sends "
        "'Python-urllib/3.x' -- Cloudflare answers that with 403 and every "
        "caller reads the resulting '' as 'version unknown', which silently "
        "disables the redeploy nudge for every user"
    )
    ua = req.get_header("User-agent") or ""
    assert ua, "no User-Agent set on the edge version probe"
    assert "python-urllib" not in ua.lower(), ua


def test_it_still_returns_empty_when_the_edge_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown must stay unknown -- callers distinguish "" from a real version."""

    def boom(req, timeout=None):
        raise OSError("no route to host")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert lighthouse._deployed_worker_version("https://edge.example.workers.dev") == ""


def test_a_response_without_a_version_field_is_empty_not_a_crash(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _capture(monkeypatch, {"ok": True})
    assert lighthouse._deployed_worker_version("https://edge.example.workers.dev") == ""
