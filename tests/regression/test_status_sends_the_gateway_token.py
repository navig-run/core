"""`navig status` reported a RUNNING gateway as "stopped".

`get_gateway_status()` called the gateway's `/status` route with no bearer token.
`/status` is authenticated, so a healthy gateway answered 401 — and every non-200
collapsed to `{"running": False}`. Meanwhile `navig gateway status` (which sends the
token) said running, and the 401 body itself promised "The NAVIG CLI does this for
you". Seen while recording the dashboard showcase: `/health` 200, `navig status`
"Gateway: stopped".
"""

from __future__ import annotations

from navig.commands import status


class _Resp:
    def __init__(self, code: int, body: dict | None = None):
        self.status_code = code
        self._body = body or {}

    def json(self):
        return self._body


def test_the_status_probe_carries_the_gateway_token(monkeypatch) -> None:
    seen: dict = {}

    def fake_get(url, headers=None, timeout=None):
        seen["headers"] = headers or {}
        return _Resp(
            200,
            {"ok": True, "data": {"uptime_seconds": 5, "sessions": {"active": 1}}, "error": None},
        )

    import requests

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(
        "navig.gateway_client.gateway_request_headers", lambda: {"Authorization": "Bearer t0k"}
    )
    out = status.get_gateway_status()
    assert seen["headers"].get("Authorization") == "Bearer t0k"
    assert out["running"] is True and out["sessions"] == 1


def test_a_rejected_token_is_running_not_stopped(monkeypatch) -> None:
    import requests

    monkeypatch.setattr(requests, "get", lambda *a, **k: _Resp(401))
    out = status.get_gateway_status()
    assert out == {"running": True, "auth_error": True}
