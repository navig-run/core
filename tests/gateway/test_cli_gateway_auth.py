"""Every CLI -> gateway admin request must carry the operator's bearer token.

Measured on the operator's own machine, 2026-09-04: all SEVEN `navig cron`
commands answered HTTP 401 against their own healthy gateway. Two independent
defects stacked, and either one alone is enough to break authentication.

**1. `_cron_api` sent no headers at all.** The helper that centralised seven
commands' request code built `requests.request(method, url, json=..., timeout=...)`
with no `headers=`.

**2. `gateway_request_headers()` could not find the token even when asked.** It
read `get_config_manager()._load_global_config()`, which returns the
PYDANTIC-VALIDATED view (`validate_global_config(...).model_dump()`) and therefore
keeps ONLY fields the schema declares. The schema does not declare
`gateway.auth`. Measured against one install:

    config.yaml           -> gateway: {auth: {token: ...}, mesh_token: ...}
    _load_global_config() -> gateway: {allowed_origins, enabled, host, port,
                                       require_auth}

Every real key replaced by schema defaults, silently. The gateway's own 401 body
reads "(The NAVIG CLI does this for you.)" -- it did not.

That second one is the dangerous shape: a *read* that drops undeclared keys is
one read-modify-write away from erasing `gateway.auth.token` and
`gateway.mesh_token` from the operator's config file.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from navig import gateway_client

# The two views of ONE config file. A stub, but not an invented one: these are the
# exact key sets observed on the operator's install.
_REAL_FILE_VIEW = {"gateway": {"auth": {"token": "tok-abc123"}, "mesh_token": "mesh-xyz"}}
_SCHEMA_TRUNCATED_VIEW = {
    "gateway": {
        "allowed_origins": [],
        "enabled": True,
        "host": "127.0.0.1",
        "port": 8789,
        "require_auth": True,
    }
}


def _config_manager_stub():
    return SimpleNamespace(
        get_global_config=lambda: _REAL_FILE_VIEW,
        _load_global_config=lambda *a, **k: _SCHEMA_TRUNCATED_VIEW,
    )


@pytest.fixture
def _stub_config(monkeypatch: pytest.MonkeyPatch):
    import navig.config as navig_config

    monkeypatch.setattr(navig_config, "get_config_manager", _config_manager_stub)


def test_headers_carry_the_token(_stub_config) -> None:
    """The whole point: a configured token must reach the gateway."""
    headers = gateway_client.gateway_request_headers()
    assert headers.get("Authorization") == "Bearer tok-abc123", (
        "gateway_request_headers() found no token, so every CLI admin request goes "
        "out unauthenticated and the gateway answers 401"
    )


def test_the_schema_view_really_does_hide_the_token() -> None:
    """Discriminator: pins WHY the old reader failed, so the fix can't be undone.

    If someone switches this helper back to `_load_global_config()`, the test above
    fails -- and this one explains it: the validated view genuinely has no token to
    find. Without this, that fixture looks like an arbitrary stub.
    """
    assert "auth" not in _SCHEMA_TRUNCATED_VIEW["gateway"], "fixture no longer reproduces the bug"
    assert "auth" in _REAL_FILE_VIEW["gateway"]


def test_no_token_configured_is_still_a_valid_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unauthenticated gateway must keep working -- no empty Bearer header."""
    import navig.config as navig_config

    monkeypatch.setattr(
        navig_config, "get_config_manager", lambda: SimpleNamespace(get_global_config=lambda: {})
    )
    headers = gateway_client.gateway_request_headers()
    assert "Authorization" not in headers
    assert headers.get("X-Actor") == "navig-cli"


def test_an_unreadable_config_does_not_crash_the_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    import navig.config as navig_config

    def boom():
        raise OSError("config locked")

    monkeypatch.setattr(navig_config, "get_config_manager", boom)
    assert gateway_client.gateway_request_headers().get("X-Actor") == "navig-cli"


def test_cron_api_actually_sends_the_headers(_stub_config, monkeypatch: pytest.MonkeyPatch) -> None:
    """The other half: the helper must PASS the headers to requests.

    Seven commands share this one function, so a missing `headers=` here is a
    seven-command outage -- which is what it was.
    """
    from navig.commands import cron as cron_cmd

    seen: dict = {}

    class _FakeResponse:
        status_code = 200
        text = "{}"

        @staticmethod
        def json():
            return {"ok": True, "data": {"jobs": []}}

    def _capture(method, url, **kwargs):
        seen.update(kwargs)
        seen["method"] = method
        return _FakeResponse()

    import requests

    monkeypatch.setattr(requests, "request", _capture)
    cron_cmd._cron_api("GET", "/cron/jobs", action="list jobs")

    assert "headers" in seen, "_cron_api sent no headers at all -- the original bug"
    assert seen["headers"].get("Authorization") == "Bearer tok-abc123"
