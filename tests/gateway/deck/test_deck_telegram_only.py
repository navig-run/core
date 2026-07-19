"""Deck telegram_only lockdown — remote access is initData-only; local survives.

When deck.telegram_only is on, valid Telegram initData is the ONLY *remote*
credential: the api_key Bearer and the dev_mode header are disabled, and
tunneled traffic (CF-Ray / forwarded headers — beyond the caller's control)
gets no user id (→ 401). Genuinely-local requests keep the desktop bypass —
the desktop OS app proxies to its own daemon over plain loopback, and
`navig miniapp deploy` sets this flag, so an absolute lock would cut the
operator's own desktop off. With the flag off, the existing bypasses work.
"""

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from navig.gateway.deck import auth as deck_auth
from navig.gateway.deck.auth import _DEV_BYPASS_SENTINEL, _get_user_id, configure_deck_auth

BOT_TOKEN = "123456:test-bot-token"
API_KEY = "navig_" + "k" * 40


def _make_init_data(user_id: int = 555, auth_date: int | None = None) -> str:
    """Build a valid Telegram WebApp initData query string for BOT_TOKEN."""
    auth_date = auth_date or int(time.time())
    user_json = json.dumps({"id": user_id, "first_name": "T"}, separators=(",", ":"))
    fields = {"auth_date": str(auth_date), "user": user_json}
    data_check_string = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, data_check_string.encode(), hashlib.sha256).hexdigest()
    return urlencode({"auth_date": str(auth_date), "user": user_json, "hash": h})


class _Req:
    """Minimal stand-in for aiohttp.web.Request (only what _get_user_id reads)."""

    def __init__(self, headers: dict | None = None, remote: str = "127.0.0.1", query: dict | None = None):
        self.headers = headers or {}
        self.remote = remote
        self.query = query or {}


def _configure(telegram_only: bool) -> None:
    configure_deck_auth(
        bot_token=BOT_TOKEN,
        allowed_users=[],
        require_auth=True,
        dev_mode=False,
        auth_max_age=3600,
        api_key=API_KEY,
        telegram_only=telegram_only,
    )


def test_locked_accepts_valid_initdata():
    _configure(telegram_only=True)
    req = _Req(headers={"X-Telegram-Init-Data": _make_init_data(user_id=555)})
    assert _get_user_id(req) == 555


def test_locked_keeps_local_desktop_bypass():
    """The desktop OS talks to its own daemon over plain loopback — locking the
    public deck must not lock the operator's own desktop out."""
    _configure(telegram_only=True)
    assert _get_user_id(_Req(headers={}, remote="127.0.0.1")) == _DEV_BYPASS_SENTINEL
    # Local dev server / extension origins stay allowed…
    ok = _Req(headers={"Origin": "http://localhost:7432"}, remote="127.0.0.1")
    assert _get_user_id(ok) == _DEV_BYPASS_SENTINEL
    # …but a hostile website's fetch to 127.0.0.1 carries its own origin → 401.
    evil = _Req(headers={"Origin": "https://evil.example"}, remote="127.0.0.1")
    assert _get_user_id(evil) is None


def test_locked_rejects_tunneled_traffic_masquerading_as_local():
    """Uplink/tunnel traffic reaches the daemon FROM 127.0.0.1 but always carries
    edge headers the caller cannot strip — it must never claim the local bypass."""
    _configure(telegram_only=True)
    cf = _Req(headers={"CF-Ray": "8f2-IAD", "CF-Connecting-IP": "203.0.113.9"}, remote="127.0.0.1")
    assert _get_user_id(cf) is None
    fwd = _Req(headers={"X-Forwarded-For": "203.0.113.9"}, remote="127.0.0.1")
    assert _get_user_id(fwd) is None


def test_locked_rejects_bearer_api_key():
    # Bearer is a remote-browser credential; under the lock it stays disabled
    # (a REMOTE request presenting it gets nothing).
    _configure(telegram_only=True)
    req = _Req(headers={"Authorization": f"Bearer {API_KEY}", "CF-Ray": "8f2-IAD"}, remote="127.0.0.1")
    assert _get_user_id(req) is None


def test_locked_rejects_bad_initdata():
    # Real TMA traffic arrives via the tunnel (CF-Ray present) — forged/expired
    # initData authenticates nothing and there is no fallback for remote callers.
    _configure(telegram_only=True)
    req = _Req(
        headers={
            "X-Telegram-Init-Data": "auth_date=1&user=%7B%22id%22%3A1%7D&hash=deadbeef",
            "CF-Ray": "8f2-IAD",
        }
    )
    assert _get_user_id(req) is None


def test_unlocked_still_bypasses_loopback_and_bearer():
    _configure(telegram_only=False)
    assert _get_user_id(_Req(headers={}, remote="127.0.0.1")) == _DEV_BYPASS_SENTINEL
    assert _get_user_id(_Req(headers={"Authorization": f"Bearer {API_KEY}"})) == _DEV_BYPASS_SENTINEL


def test_accessors_reflect_config():
    _configure(telegram_only=True)
    assert deck_auth.deck_telegram_only() is True
    assert deck_auth.deck_bot_token() == BOT_TOKEN
    _configure(telegram_only=False)
    assert deck_auth.deck_telegram_only() is False
