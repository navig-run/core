"""`navig config set deck.auth_max_age 86400` stores a STRING, which used to
reject EVERY Mini App request.

`validate_init_data` does `time.time() - auth_date > max_age`. With `max_age`
the string "86400" that `navig config set` writes, `float > str` raises
TypeError, which the validator's `except` swallows into "not valid". So the
moment an operator tuned this key, every Telegram initData was rejected as
unauthorized — silently, with no reason logged — and the deck showed
"unauthorized" forever. Measured live: `validate(max_age=86400)` → valid,
`validate(max_age="86400")` → None, on the same fresh initData.

Same class as the `coerce_bool` config toggles; fixed with `coerce_int` at
`configure_deck_auth`, at `validate_init_data` (defensively), and at
`deck_auth_max_age`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from navig.gateway.deck import auth as deck_auth
from navig.gateway.deck.auth import (
    configure_deck_auth,
    deck_auth_max_age,
    validate_init_data,
)

BOT_TOKEN = "123456:test-bot-token"


def _init_data(age_seconds: int = 0) -> str:
    auth_date = int(time.time()) - age_seconds
    user_json = json.dumps({"id": 555, "first_name": "T"}, separators=(",", ":"))
    fields = {"auth_date": str(auth_date), "user": user_json}
    dcs = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    h = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": h})


def test_validate_does_not_crash_on_a_string_max_age():
    """The exact bug: a str max_age must not reject a fresh, valid token."""
    fresh = _init_data(0)

    assert validate_init_data(fresh, BOT_TOKEN, 86400), "int baseline"
    assert validate_init_data(fresh, BOT_TOKEN, "86400"), "a string max_age rejected a valid token"


def test_configure_coerces_a_string_from_config():
    """`navig config set` stores strings; the module config must hold an int."""
    configure_deck_auth(bot_token=BOT_TOKEN, allowed_users=[], auth_max_age="86400")

    assert deck_auth._deck_config["auth_max_age"] == 86400
    assert isinstance(deck_auth._deck_config["auth_max_age"], int)
    assert deck_auth_max_age() == 86400


def test_the_default_window_is_24h():
    """A dashboard is kept open for hours; 1h re-nagged every hour. 86400 = the
    schema max and the applied default across every call path."""
    from navig.core.config_schema import DeckConfig  # noqa: PLC0415

    assert DeckConfig().auth_max_age == 86400

    configure_deck_auth(bot_token=BOT_TOKEN, allowed_users=[])  # no auth_max_age
    assert deck_auth._deck_config["auth_max_age"] == 86400


@pytest.mark.parametrize(
    "age,expected_valid",
    [(0, True), (7200, True), (82800, True), (90000, False)],
)
def test_24h_staleness_boundary(age, expected_valid):
    """fresh / 2h / 23h accepted, 25h rejected — verified live against the edge."""
    configure_deck_auth(bot_token=BOT_TOKEN, allowed_users=[], auth_max_age="86400")
    max_age = deck_auth._deck_config["auth_max_age"]

    assert bool(validate_init_data(_init_data(age), BOT_TOKEN, max_age)) is expected_valid


def test_a_garbage_string_falls_back_to_the_default_not_a_crash():
    """coerce_int must not let a nonsense value reintroduce the TypeError."""
    configure_deck_auth(bot_token=BOT_TOKEN, allowed_users=[], auth_max_age="not-a-number")

    assert deck_auth._deck_config["auth_max_age"] == 86400
    assert validate_init_data(_init_data(0), BOT_TOKEN, deck_auth._deck_config["auth_max_age"])
