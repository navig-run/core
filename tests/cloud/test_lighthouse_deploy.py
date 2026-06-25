"""Tests for the pure-Python Cloudflare deploy module (no real network).

`requests` is monkeypatched with a scripted fake so we can assert the deploy
flow: token verify → account resolve → subdomain → existence check → upload
(with/without the DO migration) → enable workers.dev, plus error handling.
"""

from __future__ import annotations

import json

import pytest

from navig.cloud import lighthouse_deploy as ld


class FakeResp:
    def __init__(self, status: int, payload):
        self.status_code = status
        self._payload = payload

    def json(self):
        if self._payload is _NON_JSON:
            raise ValueError("not json")
        return self._payload


_NON_JSON = object()


def _ok(result):
    return {"success": True, "errors": [], "result": result}


def _err(msg):
    return {"success": False, "errors": [{"message": msg}], "result": None}


class FakeRequests:
    """Routes by (method, url-substring) to canned (status, payload)."""

    def __init__(self, routes, exists_status=404):
        self._routes = routes
        self._exists_status = exists_status
        self.calls = []
        self.last_put_files = None

    def _match(self, method, url):
        # Longest matching fragment wins, so "/accounts" doesn't shadow the more
        # specific "/workers/subdomain" (both are substrings of the same URL).
        best = None
        for (m, frag), resp in self._routes.items():
            if m == method and frag in url:
                if best is None or len(frag) > len(best[0]):
                    best = (frag, resp)
        if best is None:
            raise AssertionError(f"unexpected {method} {url}")
        return best[1]

    def get(self, url, **kw):
        self.calls.append(("GET", url))
        if "/workers/scripts/" in url and "/subdomain" not in url:
            return FakeResp(self._exists_status, _ok(None))
        return FakeResp(*self._match("GET", url))

    def put(self, url, **kw):
        self.calls.append(("PUT", url))
        self.last_put_files = kw.get("files")
        return FakeResp(*self._match("PUT", url))

    def post(self, url, **kw):
        self.calls.append(("POST", url))
        return FakeResp(*self._match("POST", url))

    def delete(self, url, **kw):
        self.calls.append(("DELETE", url))
        return FakeResp(*self._match("DELETE", url))


def _routes(account="acc123", subdomain="mysub"):
    return {
        ("GET", "/user/tokens/verify"): (200, _ok({"status": "active"})),
        ("GET", "/accounts"): (200, _ok([{"id": account, "name": "Me"}])),
        ("GET", "/workers/subdomain"): (200, _ok({"subdomain": subdomain})),
        ("PUT", "/workers/scripts/navig-lighthouse"): (200, _ok({"id": "navig-lighthouse"})),
        ("POST", "/workers/scripts/navig-lighthouse/subdomain"): (200, _ok({"enabled": True})),
    }


def _put_metadata(fake: FakeRequests) -> dict:
    files = fake.last_put_files
    assert files is not None
    return json.loads(files["metadata"][1])


def test_deploy_first_time_includes_migration(monkeypatch):
    fake = FakeRequests(_routes(), exists_status=404)  # script does not exist yet
    monkeypatch.setattr(ld, "requests", fake)
    result = ld.deploy(token="tok")
    assert result.url == "https://navig-lighthouse.mysub.workers.dev"
    assert result.created is True
    assert result.account_id == "acc123"
    meta = _put_metadata(fake)
    assert meta["main_module"] == "index.js"
    assert meta["bindings"][0]["class_name"] == "BrainSocket"
    assert meta["migrations"] == {"new_tag": "v1", "new_sqlite_classes": ["BrainSocket"]}
    # workers.dev route must be enabled
    assert ("POST", "https://api.cloudflare.com/client/v4/accounts/acc123/workers/scripts/navig-lighthouse/subdomain") in [
        (m, u) for (m, u) in fake.calls
    ]


def test_redeploy_omits_migration(monkeypatch):
    fake = FakeRequests(_routes(), exists_status=200)  # script already exists
    monkeypatch.setattr(ld, "requests", fake)
    result = ld.deploy(token="tok")
    assert result.created is False
    assert "migrations" not in _put_metadata(fake)


def test_fresh_forces_migration_even_when_exists(monkeypatch):
    fake = FakeRequests(_routes(), exists_status=200)
    monkeypatch.setattr(ld, "requests", fake)
    ld.deploy(token="tok", fresh=True)
    assert "migrations" in _put_metadata(fake)


def test_missing_subdomain_raises(monkeypatch):
    routes = _routes()
    routes[("GET", "/workers/subdomain")] = (200, _ok({"subdomain": ""}))
    fake = FakeRequests(routes)
    monkeypatch.setattr(ld, "requests", fake)
    with pytest.raises(ld.DeployError, match="workers.dev subdomain"):
        ld.deploy(token="tok")


def test_token_rejected_raises(monkeypatch):
    # Truly bad credential: both the API-token verify AND the /accounts fallback 401.
    routes = _routes()
    routes[("GET", "/user/tokens/verify")] = (401, _err("invalid"))
    routes[("GET", "/accounts")] = (401, _err("invalid"))
    fake = FakeRequests(routes)
    monkeypatch.setattr(ld, "requests", fake)
    with pytest.raises(ld.DeployError, match="rejected"):
        ld.deploy(token="bad")


def test_oauth_token_verifies_via_accounts_fallback(monkeypatch):
    # OAuth access tokens aren't API tokens → /user/tokens/verify 401s, but the
    # token is valid for /accounts. Deploy must still succeed.
    routes = _routes()
    routes[("GET", "/user/tokens/verify")] = (401, _err("not an API token"))
    fake = FakeRequests(routes, exists_status=404)
    monkeypatch.setattr(ld, "requests", fake)
    result = ld.deploy(token="oauth-access")
    assert result.url == "https://navig-lighthouse.mysub.workers.dev"
    assert result.account_id == "acc123"


def test_multiple_accounts_requires_explicit_id(monkeypatch):
    routes = _routes()
    routes[("GET", "/accounts")] = (
        200,
        _ok([{"id": "a", "name": "A"}, {"id": "b", "name": "B"}]),
    )
    fake = FakeRequests(routes)
    monkeypatch.setattr(ld, "requests", fake)
    with pytest.raises(ld.DeployError, match="multiple accounts"):
        ld.deploy(token="tok")
