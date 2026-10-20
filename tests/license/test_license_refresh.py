"""Licence auto-refresh — adopt a renewed token, never a worse one.

A monthly subscriber used to fall to Free 31 days after their last paste, even
while paying, because the daemon never learned about the token each renewal
minted. ``refresh_once`` asks api.navig.run for the newest token. These tests
pin the safety properties around that: an unverifiable reply is refused, a
network failure changes nothing, and a dev override is never sent anywhere.
The server is mocked; signing and verification are real.
"""

from __future__ import annotations

import datetime as _dt
import json
from uuid import uuid4

import pytest

from navig.license.keys import _b64url_encode

KEY_ID = "k_test_refresh"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Isolated config dir + an ephemeral signing key the verifier trusts."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    import navig.license as lic
    import navig.license.keys as keys_mod

    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(tmp_path))
    sk = Ed25519PrivateKey.generate()
    pub = sk.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    orig = keys_mod.get_public_key
    monkeypatch.setattr(keys_mod, "get_public_key", lambda kid: pub if kid == KEY_ID else orig(kid))
    lic._invalidate_cache()
    yield lic, sk
    lic._invalidate_cache()


def _token(sk, tier: str, days: int | None) -> str:
    until = None if days is None else (
        _dt.datetime.now(tz=_dt.timezone.utc) + _dt.timedelta(days=days)
    ).isoformat()
    payload = {
        "license_version": 1, "license_id": str(uuid4()), "tier": tier, "hosts": 5,
        "capabilities": [], "commercial_use": True,
        "billing_period": "monthly" if days is not None else "one_time",
        "issued_at": _dt.datetime.now(tz=_dt.timezone.utc).isoformat(),
        "subscription_until": until, "entitlements_perpetual": {"modules": []},
        "branding": None, "signature_key_id": KEY_ID, "buyer_email": "b@example.com",
    }
    b64 = _b64url_encode(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode())
    return f"NAVIG-LICENSE-v1:{b64}.{_b64url_encode(sk.sign(b64.encode('ascii')))}"


class _Resp:
    def __init__(self, status: int, body):
        self.status_code = status
        self._body = body

    def json(self):
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


def _server(monkeypatch, reply, seen: list | None = None):
    import httpx

    def fake_post(url, json=None, timeout=None):  # noqa: A002 — mirrors httpx
        if seen is not None:
            seen.append((url, json))
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(httpx, "post", fake_post)


def test_a_renewed_token_is_adopted(env, monkeypatch):
    lic, sk = env
    old = _token(sk, "plus", days=-1)  # the pasted monthly token has lapsed
    assert lic.paste_license(old).valid
    lic._invalidate_cache()
    assert lic.current_status().effective_tier == "free", "precondition: lapsed"

    renewed = _token(sk, "plus", days=30)
    seen: list = []
    _server(monkeypatch, _Resp(200, {"ok": True, "changed": True, "token": renewed}), seen)

    from navig.license.refresh import refresh_once

    result = refresh_once()
    assert result.outcome == "updated"
    assert lic.read_raw_token() == renewed
    assert lic.current_status().effective_tier == "plus"
    assert seen[0][1] == {"token": old}, "the credential is the token we hold, nothing else"


def test_a_token_that_does_not_verify_is_refused(env, monkeypatch):
    lic, sk = env
    good = _token(sk, "plus", days=10)
    lic.paste_license(good)
    tampered = good[:-3] + ("AAA" if not good.endswith("AAA") else "BBB")
    _server(monkeypatch, _Resp(200, {"ok": True, "changed": True, "token": tampered}))

    from navig.license.refresh import refresh_once

    assert refresh_once().outcome == "rejected"
    assert lic.read_raw_token() == good, "the server's word never beats the signature"


@pytest.mark.parametrize(
    "reply",
    [ConnectionError("offline"), _Resp(500, {}), _Resp(200, ValueError("not json"))],
)
def test_a_failure_leaves_the_licence_alone(env, monkeypatch, reply):
    lic, sk = env
    good = _token(sk, "max", days=10)
    lic.paste_license(good)
    _server(monkeypatch, reply)

    from navig.license.refresh import refresh_once

    assert refresh_once().outcome == "unreachable"
    assert lic.read_raw_token() == good


def test_an_unknown_token_is_unchanged_not_an_error(env, monkeypatch):
    lic, sk = env
    lic.paste_license(_token(sk, "enterprise", days=None))  # a hand-signed comp licence
    _server(monkeypatch, _Resp(404, {"ok": False, "error": "unknown_token"}))

    from navig.license.refresh import refresh_once

    assert refresh_once().outcome == "unchanged"


def test_nothing_is_sent_without_a_licence(env, monkeypatch):
    seen: list = []
    _server(monkeypatch, _Resp(200, {}), seen)

    from navig.license.refresh import refresh_once

    assert refresh_once().outcome == "skipped"
    assert seen == []


def test_a_dev_override_is_never_sent(env, monkeypatch):
    lic, sk = env
    lic.paste_license(_token(sk, "plus", days=10))
    monkeypatch.setattr(lic, "_is_source_checkout", lambda: True)
    p = lic.dev_override_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"tier": "max"}), encoding="utf-8")
    lic._invalidate_cache()
    seen: list = []
    _server(monkeypatch, _Resp(200, {}), seen)

    from navig.license.refresh import refresh_once

    assert refresh_once().outcome == "skipped"
    assert seen == []


def test_auto_refresh_can_be_switched_off(env, monkeypatch):
    from navig.license import refresh

    class _Cfg:
        def get(self, key, default=None):
            return "false" if key == "license.auto_refresh" else default

    monkeypatch.setattr("navig.config.get_config_manager", lambda *a, **k: _Cfg())
    assert refresh.auto_refresh_enabled() is False, '"false" from `navig config set` must mean off'
