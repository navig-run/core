"""Telegram Bot API 8.0 added a `signature` field to initData — and it broke auth.

Since Bot API 8.0 (2024-11) EVERY real Mini App launch carries a `signature`
field (an Ed25519 value for third-party validation). Telegram builds the HMAC
data-check-string EXCLUDING both `hash` AND `signature`; `validate_init_data`
excluded only `hash`, so it folded `signature` into the string, computed a hash
Telegram never produced, and rejected every genuine launch as "hash mismatch".

A minimal (signature-less) forge passed, which is exactly why probes said the
server was "healthy" while the operator's real Mini App showed `unauthorized`
for days. This pins the fix: a launch carrying `signature` must validate.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from navig.gateway.deck.auth import validate_init_data

BOT_TOKEN = "123456:test-bot-token"


def _sign(fields: dict[str, str]) -> str:
    """Sign like Telegram: data-check-string EXCLUDES hash and signature."""
    dcs = "\n".join(
        f"{k}={fields[k]}" for k in sorted(fields) if k not in ("hash", "signature")
    )
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": h})


def _launch(*, with_signature: bool, age: int = 0) -> str:
    auth_date = int(time.time()) - age
    fields = {
        "auth_date": str(auth_date),
        "user": json.dumps({"id": 159901607, "first_name": "S"}, separators=(",", ":")),
        "query_id": "AAExampleQueryId",
        "chat_instance": "-1234567890123456789",
        "chat_type": "sender",
    }
    if with_signature:
        # A real Ed25519 signature is base64url; its VALUE is irrelevant to the
        # bot-token HMAC — its mere presence in the string is what broke things.
        fields["signature"] = "3q2-7_8AAAAExampleTelegramEd25519SignatureValue"
    return _sign(fields)


def test_a_real_8_0_launch_with_signature_validates():
    """The exact production failure: a launch carrying `signature` must pass."""
    result = validate_init_data(_launch(with_signature=True), BOT_TOKEN, 86400)

    assert result is not None, (
        "initData carrying Telegram's 8.0 `signature` field was rejected — the "
        "data-check-string must exclude `signature`, not fold it into the HMAC"
    )
    assert result["user"]["id"] == 159901607


def test_signature_is_ignored_not_required():
    """A signature-less launch (older clients, forges) still validates."""
    assert validate_init_data(_launch(with_signature=False), BOT_TOKEN, 86400) is not None


def test_signature_bearing_launch_still_enforces_staleness():
    """Excluding `signature` must not weaken the age check — 25h is still rejected."""
    assert validate_init_data(_launch(with_signature=True, age=90_000), BOT_TOKEN, 86400) is None


def test_a_tampered_signature_launch_still_fails_on_a_real_mismatch():
    """Excluding `signature` from the HMAC must not accept a genuinely bad hash."""
    from urllib.parse import parse_qs

    good = _launch(with_signature=True)
    parsed = {k: v[0] for k, v in parse_qs(good, keep_blank_values=True).items()}
    parsed["hash"] = "0" * 64  # a genuinely wrong hash
    assert validate_init_data(urlencode(parsed), BOT_TOKEN, 86400) is None
