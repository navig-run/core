"""The bot must answer its own owner, however config stored their id.

Same root cause as the deck allowlist (#814), on the live Telegram channel. Ids
arrive from the API as **ints**; `telegram.allowed_users` comes from config, where
the operator can only produce strings — and `set(allowed_users or [])` kept them:

    allowed_users: ["12345"]     ->  {'12345'}                  owner IGNORED
    navig config set … 12345     ->  set("12345") = {'1'..'5'}  owner IGNORED
    allowed_users: [12345]       ->  {12345}                    owner answered

`_is_user_authorized` documents "Empty list = deny all", so the failure mode here is
the operator's own bot going silent on them — with the config plainly listing the id.

Both the channel and the deck now parse through `navig.core.coerce.coerce_id_set`,
so the two surfaces cannot disagree about which ids an operator listed.
"""
from __future__ import annotations

import pytest

from navig.core.coerce import coerce_id_rejects, coerce_id_set
from navig.gateway.channels.telegram import TelegramChannel

OWNER = 12345
STRANGER = 99999
GROUP = -1001234567


def _channel(**kwargs: object) -> TelegramChannel:
    channel = TelegramChannel.__new__(TelegramChannel)
    TelegramChannel.__init__(channel, bot_token="x", **kwargs)  # type: ignore[arg-type]
    return channel


@pytest.mark.parametrize(
    "config_value",
    [[12345], ["12345"], "12345", " 12345 ", "12345,99999", [12345, "99999"]],
)
def test_the_owner_is_authorized_however_config_stored_the_id(config_value: object) -> None:
    channel = _channel(allowed_users=config_value)

    assert channel._is_user_authorized(OWNER, chat_id=OWNER, is_group=False) is True, (
        f"allowed_users={config_value!r} made the bot ignore its own owner"
    )


@pytest.mark.parametrize("config_value", [[12345], ["12345"], "12345"])
def test_a_stranger_is_still_denied(config_value: object) -> None:
    """The partner — a fix that authorized everyone would satisfy every test above."""
    channel = _channel(allowed_users=config_value)

    assert channel._is_user_authorized(STRANGER, chat_id=STRANGER, is_group=False) is False


def test_an_empty_allowlist_still_denies_everyone() -> None:
    """Unchanged, and the OPPOSITE of the deck's policy: `_is_user_authorized`
    documents "Empty list = deny all". Pinned so the shared parser cannot quietly
    import the deck's "empty means open" semantics into the bot."""
    channel = _channel(allowed_users=[])

    assert channel._is_user_authorized(OWNER, chat_id=OWNER, is_group=False) is False


def test_open_mode_still_bypasses_the_allowlist() -> None:
    channel = _channel(allowed_users=["12345"], require_auth=False)

    assert channel._is_user_authorized(STRANGER, chat_id=STRANGER, is_group=False) is True


@pytest.mark.parametrize("config_value", [[-1001234567], ["-1001234567"], "-1001234567"])
def test_group_ids_survive_the_minus_sign(config_value: object) -> None:
    """Telegram group ids are large NEGATIVE integers — an easy thing for a parser
    to drop, and dropping one silently mutes the bot in that group."""
    channel = _channel(allowed_users=[], allowed_groups=config_value)

    assert channel._is_user_authorized(STRANGER, chat_id=GROUP, is_group=True) is True


def test_an_unparseable_entry_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("ERROR", logger="navig.gateway.channels.telegram"):
        channel = _channel(allowed_users=["12345", "not-an-id"])

    assert channel.allowed_users == {12345}
    assert any("not-an-id" in r.getMessage() for r in caplog.records), (
        "a dropped id is a person who cannot reach the bot — it must not be silent"
    )


# ── the shared parser ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,ids,present",
    [
        (None, set(), False),
        ([], set(), False),
        ("", set(), False),
        ("  ", set(), False),
        (12345, {12345}, True),
        ("12345", {12345}, True),
        (["12345", 99999], {12345, 99999}, True),
        ("12345 99999", {12345, 99999}, True),
        ("12345,99999", {12345, 99999}, True),
        ("-1001234567", {-1001234567}, True),
        ({12345}, {12345}, True),
        ("nobody", set(), True),  # present but unusable — NOT the same as unset
        (True, set(), False),  # bool is an int subclass; a bool here is a mistake
    ],
)
def test_coerce_id_set(raw: object, ids: set[int], present: bool) -> None:
    assert coerce_id_set(raw) == (ids, present)


@pytest.mark.parametrize(
    "raw,rejects",
    [(None, []), ([12345], []), ("12345", []), (["12345", "bad"], ["bad"]), ("nobody", ["nobody"])],
)
def test_coerce_id_rejects(raw: object, rejects: list[str]) -> None:
    assert coerce_id_rejects(raw) == rejects


def test_the_deck_and_the_channel_share_one_parser() -> None:
    """Anti-vacuity, and the point of the change: two surfaces deciding who may in
    must not each carry their own idea of what the operator wrote."""
    import inspect

    from navig.gateway.deck import auth

    assert "coerce_id_set" in inspect.getsource(auth._coerce_user_ids)
