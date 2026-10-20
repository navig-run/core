"""`navig contacts resolve` — obtaining a liveness verdict instead of guessing one.

The whole point of the module is what it refuses to write down. A flood-wait, a
timeout and a deleted account are indistinguishable from the outside, and a
verdict is believed forever by whoever consumes it — so these tests are mostly
about the cases where *no* verdict is the correct answer.

No test here reaches Telegram: the client is faked, as everywhere else in
`tests/telegram`.
"""

from __future__ import annotations

import asyncio

from telethon.errors import FloodWaitError

from navig.telegram import contacts as tgc
from navig.telegram import resolve as resolve_mod

# ── fakes ────────────────────────────────────────────────────────────────────


class User:
    def __init__(self, *, deleted=False, bot=False):
        self.deleted = deleted
        self.bot = bot


class Channel:
    pass


class _Client:
    """Answers `get_entity` from a table; raises whatever it is told to raise."""

    def __init__(self, entities=None, errors=None, flood_on=None, flood_seconds=3600):
        self.entities = entities or {}
        self.errors = errors or {}
        self.flood_on = flood_on
        self.flood_seconds = flood_seconds
        self.lookups: list[str] = []
        self._flooded = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_entity(self, handle):
        self.lookups.append(handle)
        if self.flood_on and handle == self.flood_on and not self._flooded:
            self._flooded = True
            raise FloodWaitError(request=None, capture=self.flood_seconds)
        if handle in self.errors:
            raise self.errors[handle]
        if handle not in self.entities:
            raise ValueError(f'No user has "{handle}" as username')
        return self.entities[handle]


def _run(monkeypatch, handles, client, **kw):
    monkeypatch.setattr(tgc, "UserClient", lambda: client)
    kw.setdefault("delay", 0)
    return asyncio.run(resolve_mod.resolve_handles(handles, **kw))


def _states(result):
    return {v["handle"]: v["state"] for v in result["verdicts"]}


# ── what a verdict looks like ────────────────────────────────────────────────


def test_a_person_resolves_to_ok(monkeypatch):
    res = _run(monkeypatch, ["alice"], _Client(entities={"alice": User()}))
    assert _states(res) == {"alice": "ok"}
    assert res["unchecked"] == [] and res["aborted"] is None


def test_a_deleted_account_is_gone(monkeypatch):
    res = _run(monkeypatch, ["ghost"], _Client(entities={"ghost": User(deleted=True)}))
    assert _states(res) == {"ghost": "gone"}


def test_a_channel_and_a_bot_are_not_a_user(monkeypatch):
    client = _Client(entities={"news": Channel(), "helper": User(bot=True)})
    res = _run(monkeypatch, ["news", "helper"], client)
    assert _states(res) == {"news": "not_a_user", "helper": "not_a_user"}


def test_an_unregistered_username_is_gone(monkeypatch):
    """Not a person on the end of a `t.me/` link any more."""
    res = _run(monkeypatch, ["old_handle"],
               _Client(errors={"old_handle": ValueError("no entity")}))
    assert _states(res) == {"old_handle": "gone"}


def test_a_malformed_username_is_invalid(monkeypatch):
    class UsernameInvalidError(Exception):
        pass

    res = _run(monkeypatch, ["---"], _Client(errors={"---": UsernameInvalidError()}))
    assert _states(res) == {"---": "invalid"}


def test_the_at_sign_is_stripped_before_asking(monkeypatch):
    client = _Client(entities={"alice": User()})
    _run(monkeypatch, ["@alice"], client)
    assert client.lookups == ["alice"]


def test_the_same_handle_is_only_asked_about_once(monkeypatch):
    """The book can carry one handle on two contacts; budget is not free."""
    client = _Client(entities={"alice": User()})
    res = _run(monkeypatch, ["alice", "Alice", "@alice"], client)
    assert client.lookups == ["alice"]
    assert res["resolved"] == 1


# ── the cases that must produce NO verdict ───────────────────────────────────


def test_an_unknown_error_is_silence_not_a_death_sentence(monkeypatch):
    """
    The property the whole module exists for.

    `get_entity` failing says "no answer", not "no such person". Recording the
    difference wrongly marks a live contact dead, and `flag --from-file` will
    believe it without checking.
    """
    class SomeRPCError(Exception):
        pass

    res = _run(monkeypatch, ["alice", "bob"],
               _Client(errors={"alice": SomeRPCError("boom")}, entities={"bob": User()}))
    assert _states(res) == {"bob": "ok"}
    assert res["unchecked"] == ["alice"]
    assert res["aborted"] is None, "an unclassifiable error is not an abort"


def test_a_flood_over_the_ceiling_stops_the_run(monkeypatch):
    client = _Client(entities={"a": User(), "b": User()}, flood_on="a",
                     flood_seconds=3600)
    res = _run(monkeypatch, ["a", "b"], client, max_flood_wait=300)
    assert res["aborted"] and "FLOOD_WAIT" in res["aborted"]
    assert res["verdicts"] == []
    assert set(res["unchecked"]) == {"a", "b"}


def test_nothing_after_a_flood_is_recorded_as_dead(monkeypatch):
    """
    Stopping must not read as "the rest were not found" — which is exactly what
    a naive loop that catches-and-continues would produce.
    """
    client = _Client(entities={"b": User(), "c": User()}, flood_on="a",
                     flood_seconds=3600)
    res = _run(monkeypatch, ["a", "b", "c"], client, max_flood_wait=300)
    assert _states(res) == {}
    assert set(res["unchecked"]) == {"a", "b", "c"}


def test_a_flood_under_the_ceiling_waits_and_retries(monkeypatch):
    client = _Client(entities={"alice": User()}, flood_on="alice", flood_seconds=5)
    res = _run(monkeypatch, ["alice"], client, max_flood_wait=300, delay=0)
    assert _states(res) == {"alice": "ok"}, "the retry after the wait must be used"


def test_an_error_after_the_flood_retry_is_still_classified(monkeypatch):
    class UsernameNotOccupiedError(Exception):
        pass

    client = _Client(errors={"alice": UsernameNotOccupiedError()},
                     flood_on="alice", flood_seconds=5)
    res = _run(monkeypatch, ["alice"], client, max_flood_wait=300, delay=0)
    assert _states(res) == {"alice": "gone"}


def test_an_unclassifiable_error_after_the_retry_is_unchecked(monkeypatch):
    class SomeRPCError(Exception):
        pass

    client = _Client(errors={"alice": SomeRPCError("boom")},
                     flood_on="alice", flood_seconds=5)
    res = _run(monkeypatch, ["alice"], client, max_flood_wait=300, delay=0)
    assert res["verdicts"] == []
    assert res["unchecked"] == ["alice"]


# ── budget ───────────────────────────────────────────────────────────────────


def test_the_limit_stops_the_run_and_names_what_was_skipped(monkeypatch):
    client = _Client(entities={h: User() for h in "abcdef"})
    res = _run(monkeypatch, list("abcdef"), client, limit=2)
    assert res["resolved"] == 2
    assert res["checked"] == 2
    assert set(res["unchecked"]) == {"c", "d", "e", "f"}
    assert client.lookups == ["a", "b"]


def test_the_cap_never_exceeds_the_handles_given(monkeypatch):
    client = _Client(entities={"a": User()})
    res = _run(monkeypatch, ["a"], client, limit=85)
    assert res["resolved"] == 1 and res["unchecked"] == []


def test_progress_reports_every_handle_checked(monkeypatch):
    seen: list[dict] = []
    _run(monkeypatch, ["a", "b"],
         _Client(entities={"a": User(), "b": User(deleted=True)}),
         progress=seen.append)
    assert [s["state"] for s in seen] == ["ok", "gone"]


def test_an_empty_list_never_opens_a_client(monkeypatch):
    class Exploding:
        def __call__(self):
            raise AssertionError("a client must not be opened for no work")

    monkeypatch.setattr(tgc, "UserClient", Exploding())
    res = asyncio.run(resolve_mod.resolve_handles([]))
    assert res["verdicts"] == [] and res["checked"] == 0
