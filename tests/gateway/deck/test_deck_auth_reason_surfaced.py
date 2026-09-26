"""A rejected Mini App `initData` must name WHY, and never leak the secret.

`validate_init_data` returns an identical `None` for empty / no-hash / hash
mismatch / stale — which is exactly why one wrong config value (a stale window,
a rotated bot token) took a multi-agent investigation instead of one log line.
It now accepts a `reason` out-param that the middleware folds into its single
"unauthorized" WARNING.

Two things are load-bearing and pinned here:
1. the reason is SPECIFIC (a stale token says "stale" with the numbers; a bad
   HMAC says "mismatch") so the next occurrence is a one-line diagnosis; and
2. the reason NEVER contains the raw initData, the hash, or the bot token —
   the repo rule is no secrets in logs, even under --debug, and this string
   goes straight to a WARNING.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from navig.gateway.deck.auth import validate_init_data

BOT_TOKEN = "123456:test-bot-token"


def _sign(fields: dict[str, str], *, bot_token: str = BOT_TOKEN) -> str:
    dcs = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": h})


def _init_data(age_seconds: int = 0, *, bot_token: str = BOT_TOKEN) -> str:
    auth_date = int(time.time()) - age_seconds
    user_json = json.dumps({"id": 555, "first_name": "T"}, separators=(",", ":"))
    return _sign({"auth_date": str(auth_date), "user": user_json}, bot_token=bot_token)


def test_valid_initdata_records_no_reason():
    reasons: list[str] = []
    result = validate_init_data(_init_data(0), BOT_TOKEN, 86400, reason=reasons)

    assert result and result["user"]["id"] == 555
    assert reasons == [], "a successful validation must not append a reason"


def test_stale_reason_is_specific_and_carries_the_numbers():
    reasons: list[str] = []
    # 25h old against the 24h default → stale.
    assert validate_init_data(_init_data(90000), BOT_TOKEN, 86400, reason=reasons) is None

    assert len(reasons) == 1
    why = reasons[0].lower()
    assert "stale" in why
    assert "age=" in why and "max=" in why, "the stale reason must name the numbers to diagnose"
    assert "reopen" in why, "the operator-facing hint to reopen the Mini App"


def test_hash_mismatch_reason():
    # Sign with a DIFFERENT bot token → the HMAC will not match BOT_TOKEN.
    forged_for_other_bot = _init_data(0, bot_token="999999:someone-elses-bot")
    reasons: list[str] = []

    assert validate_init_data(forged_for_other_bot, BOT_TOKEN, 86400, reason=reasons) is None
    assert reasons and "mismatch" in reasons[0].lower()


@pytest.mark.parametrize(
    "init_data,needle",
    [
        ("", "no initdata"),
        ("auth_date=1&user=%7B%7D", "no hash"),  # no `hash` field at all
    ],
)
def test_other_rejections_are_named(init_data, needle):
    reasons: list[str] = []
    assert validate_init_data(init_data, BOT_TOKEN, 86400, reason=reasons) is None
    assert reasons and needle in reasons[0].lower()


def test_malformed_reason_needs_a_valid_signature_over_bad_payload():
    """The `except` branch only bites AFTER the HMAC matches — a correctly-signed
    but unparseable user payload. (A wrong hash is caught earlier as 'mismatch'.)"""
    fresh = str(int(time.time()))
    signed_bad_user = _sign({"auth_date": fresh, "user": "not-json"})
    reasons: list[str] = []

    assert validate_init_data(signed_bad_user, BOT_TOKEN, 86400, reason=reasons) is None
    assert reasons and "malformed" in reasons[0].lower()


def test_reason_never_leaks_the_initdata_or_hash():
    """The reason string goes to a WARNING — it must contain no secret material."""
    init = _init_data(90000)  # stale, so a reason is produced
    received_hash = dict(p.split("=", 1) for p in init.split("&"))["hash"]

    reasons: list[str] = []
    validate_init_data(init, BOT_TOKEN, 86400, reason=reasons)

    assert reasons
    blob = reasons[0]
    assert received_hash not in blob, "the initData hash must never appear in the log reason"
    assert init not in blob, "the raw initData must never appear in the log reason"
    assert BOT_TOKEN not in blob, "the bot token must never appear in the log reason"
    # And the user payload must not be echoed either.
    assert "555" not in blob and "first_name" not in blob


def test_reason_out_param_is_optional_and_backward_compatible():
    """Existing positional callers (events.py, older code) pass no `reason`."""
    assert validate_init_data(_init_data(0), BOT_TOKEN, 86400) is not None
    assert validate_init_data(_init_data(90000), BOT_TOKEN, 86400) is None
