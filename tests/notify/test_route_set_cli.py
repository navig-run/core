"""`navig notify route set <type> <chat>` accepts the id the Telegram app shows.

Every channel and supergroup id is NEGATIVE, and Click reads a leading "-" as an
option: `route set sale -1004466964318` died with "No such option: -1" — i.e. the
command could not take the only ids it documents ("Channel/group id (e.g.
4466964318), -100… id, or @username"). The same trap was already fixed on
`deletions target`/`deletions mute` with `ignore_unknown_options`.

Nothing here writes the notify store: `resolve_chat`/`set_route` are patched, so
the test only proves PARSE behaviour (and a test that "isolates" and then writes
could still edit the operator's live preferences — see test_appointment_routing).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

runner = CliRunner()

NEGATIVE_ID = "-1004466964318"


@pytest.fixture()
def route_calls(monkeypatch):
    """Capture what the command resolves, without touching Telegram or config."""
    from navig.notify import telegram_routes

    calls: dict = {}

    def _resolve(chat: str):
        calls["chat"] = chat
        return SimpleNamespace(chat_id=int(chat), title="Money Machine", kind="channel")

    def _set(type_key, chat_id, title):
        calls["route"] = (type_key, chat_id, title)

    monkeypatch.setattr(telegram_routes, "resolve_chat", _resolve)
    monkeypatch.setattr(telegram_routes, "set_route", _set)
    return calls


def test_route_set_takes_a_negative_channel_id(route_calls):
    from navig.commands.notify import notify_app

    res = runner.invoke(notify_app, ["route", "set", "sale", NEGATIVE_ID])
    assert res.exit_code == 0, res.output
    assert route_calls["chat"] == NEGATIVE_ID
    assert route_calls["route"] == ("sale", -1004466964318, "Money Machine")


def test_route_set_still_takes_a_positive_id_and_username(route_calls):
    from navig.commands.notify import notify_app

    for value, want in (("4466964318", 4466964318),):
        res = runner.invoke(notify_app, ["route", "set", "sale", value])
        assert res.exit_code == 0, res.output
        assert route_calls["route"] == ("sale", want, "Money Machine")


def test_help_is_still_help_not_a_positional(route_calls):
    """ignore_unknown_options must not swallow --help into the chat argument."""
    from navig.commands.notify import notify_app

    res = runner.invoke(notify_app, ["route", "set", "--help"])
    assert res.exit_code == 0, res.output
    assert "-100" in res.output          # the help still documents negative ids
    assert "chat" not in route_calls     # nothing was resolved


def test_a_missing_chat_is_still_an_error(route_calls):
    from navig.commands.notify import notify_app

    res = runner.invoke(notify_app, ["route", "set", "sale"])
    assert res.exit_code != 0
    assert "route" not in route_calls
