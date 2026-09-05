"""The deck allowlist must match the operator's own user id, however config stored it.

The Telegram user id from initData is an **int**, but this value comes from config,
where the operator can only produce strings. Both of these reached `set(...)` untouched:

    allowed_users: ["12345"]     # quoted in YAML/JSON  -> {'12345'}
    navig config set … 12345     # stores a STRING      -> set("12345") is
                                 #                         {'1','2','3','4','5'}

so `12345 not in allowed` was True and the owner was locked out of their own deck —
`user 12345 not in allowed_users` in the log, while the config plainly listed 12345.

The second half of the fix matters more than the first. The middleware treats an EMPTY
allowlist as "no restriction", so a value that is present but yields no usable id
(`allowed_users: "nobody"`) must not collapse into the same state as "unset" — that
would turn a typo into an OPEN deck. `allowed_users_configured` keeps the two apart.
"""
from __future__ import annotations

import pytest

from navig.gateway.deck.auth import _coerce_user_ids, _deck_config, configure_deck_auth

OWNER = 12345
STRANGER = 99999


def _decide(user_id: int) -> str:
    """The middleware's actual verdict, expressed from the module state it reads."""
    allowed = _deck_config["allowed_users"]
    configured = _deck_config["allowed_users_configured"]
    return "deny" if (configured and user_id not in allowed) else "allow"


@pytest.mark.parametrize(
    "config_value",
    [
        [12345],  # YAML list of ints — the only shape that used to work
        ["12345"],  # YAML/JSON list of quoted strings
        "12345",  # what `navig config set` stores
        " 12345 ",  # stray whitespace
        "12345,99999",  # comma-separated, as a human would type it
        [12345, "99999"],  # mixed list
    ],
)
def test_the_owner_is_allowed_however_config_stored_the_id(config_value: object) -> None:
    configure_deck_auth(bot_token="t", allowed_users=config_value, require_auth=True)

    assert _decide(OWNER) == "allow", (
        f"allowed_users={config_value!r} locked the owner out of their own deck"
    )


@pytest.mark.parametrize("config_value", [[12345], ["12345"], "12345"])
def test_a_stranger_is_still_denied(config_value: object) -> None:
    """The partner. A fix that allowed everyone would satisfy every test above."""
    configure_deck_auth(bot_token="t", allowed_users=config_value, require_auth=True)

    assert _decide(STRANGER) == "deny"


@pytest.mark.parametrize("config_value", [None, [], ""])
def test_an_unset_allowlist_still_means_no_restriction(config_value: object) -> None:
    """Unchanged behaviour: no allowlist configured = the deck is not user-restricted.
    Folding this into the deny case would lock out every install that never set one."""
    configure_deck_auth(bot_token="t", allowed_users=config_value, require_auth=True)

    assert _decide(OWNER) == "allow"
    assert _decide(STRANGER) == "allow"


def test_a_configured_but_unusable_allowlist_denies_rather_than_opening() -> None:
    """The dangerous direction, and the reason `allowed_users_configured` exists.

    Parsing `"nobody"` yields no ids. If that were indistinguishable from "unset", a
    single typo would silently turn a restricted deck into an open one — the fix would
    have introduced a worse bug than the one it closed."""
    configure_deck_auth(bot_token="t", allowed_users="nobody", require_auth=True)

    assert _deck_config["allowed_users"] == set()
    assert _deck_config["allowed_users_configured"] is True
    assert _decide(OWNER) == "deny"
    assert _decide(STRANGER) == "deny"


def test_an_unparseable_entry_is_reported_not_dropped_in_silence(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A dropped entry is a person who cannot reach the deck."""
    with caplog.at_level("ERROR", logger="navig.gateway.deck.auth"):
        configure_deck_auth(
            bot_token="t", allowed_users=["12345", "not-an-id"], require_auth=True
        )

    assert _deck_config["allowed_users"] == {12345}
    assert any("not-an-id" in r.getMessage() for r in caplog.records), (
        "the rejected entry was dropped without telling anyone"
    )


# ── the parser itself ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw,ids,configured",
    [
        (None, set(), False),
        ([], set(), False),
        ("", set(), False),
        ("   ", set(), False),
        (12345, {12345}, True),
        ("12345", {12345}, True),
        (["12345", 99999], {12345, 99999}, True),
        ("12345 99999", {12345, 99999}, True),
        ("12345,99999", {12345, 99999}, True),
        ({12345}, {12345}, True),
        ("-100200", {-100200}, True),  # group ids are negative
        ("nobody", set(), True),  # configured, unusable → NOT "unset"
    ],
)
def test_coerce_user_ids(raw: object, ids: set[int], configured: bool) -> None:
    assert _coerce_user_ids(raw) == (ids, configured)


def test_the_middleware_reads_the_configured_flag_not_just_the_set() -> None:
    """Anti-vacuity: every test here models the decision with `_decide`. If the real
    middleware went back to keying on `allowed` alone, these would all still pass while
    the configured-but-unusable case silently re-opened."""
    import inspect

    from navig.gateway.deck import auth

    source = inspect.getsource(auth)
    middleware = source[source.index("allowed = _deck_config"):]
    assert "allowed_users_configured" in middleware[:800], (
        "the auth middleware no longer consults allowed_users_configured — a "
        "configured-but-unparseable allowlist would fall through to 'no restriction'"
    )
