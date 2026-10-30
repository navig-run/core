"""`navig telegram contacts missing` — who you filed that has since left the list.

Measured 2026-10-09: 105 of 1,280 filed contacts were gone. The first version of this
report answered "unknown" for every one of them, because the session is a StringSession
(no entity cache) and the journal never recorded access hashes — so nothing could be
asked by id. These tests pin the replay rules, the by-id path, and the opt-in username
fallback, with a fake client: no test reaches Telegram.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from telethon.errors import FloodWaitError
from telethon.tl.functions.contacts import GetContactsRequest
from telethon.tl.functions.users import GetUsersRequest

from navig.telegram import contacts as tgc


def _w(uid, *, action="add", tag="DAVI", handle=None, access_hash=None, at="2026-09-01"):
    return {"kind": "write", "action": action, "user_id": uid, "tag": tag,
            "handle": handle or f"h{uid}", "chat": "test", "at": at,
            "access_hash": access_hash}


# ── replay ───────────────────────────────────────────────────────────────────


def test_a_write_files_someone():
    assert set(tgc.expected_contacts([_w(1), _w(2)])) == {1, 2}


def test_a_deliberate_prune_is_not_somebody_who_left():
    recs = [_w(1), {"kind": "prune", "user_id": 1}]
    assert tgc.expected_contacts(recs) == {}


def test_undoing_an_add_removes_and_undoing_a_prune_restores():
    recs = [
        _w(1), {"kind": "undo", "action": "add", "user_id": 1},
        _w(2), {"kind": "prune", "user_id": 2}, {"kind": "undo", "action": "prune", "user_id": 2},
    ]
    assert set(tgc.expected_contacts(recs)) == {2}


def test_undoing_a_rename_keeps_the_contact():
    recs = [_w(1, action="update"), {"kind": "undo", "action": "update", "user_id": 1}]
    assert set(tgc.expected_contacts(recs)) == {1}


def test_the_first_write_is_the_one_kept():
    """`filed_at` must be when they were first filed, not the latest rename."""
    exp = tgc.expected_contacts([_w(1, at="2026-08-01"), _w(1, action="update", at="2026-09-01")])
    assert exp[1]["at"] == "2026-08-01"


# ── the report, against a fake client ────────────────────────────────────────


class _Client:
    def __init__(self, present, users=(), entities=None, flood_on=None):
        self.present = present
        self.users = {u.id: u for u in users}
        self.entities = entities or {}
        self.flood_on = flood_on
        self.lookups: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def __call__(self, req):
        if isinstance(req, GetContactsRequest):
            return SimpleNamespace(users=[SimpleNamespace(id=i) for i in self.present])
        if isinstance(req, GetUsersRequest):
            return [self.users[i.user_id] for i in req.id if i.user_id in self.users]
        raise AssertionError(f"unexpected request {req!r}")

    async def get_entity(self, handle):
        self.lookups.append(handle)
        if handle == self.flood_on:
            raise FloodWaitError(request=None, capture=3600)
        if handle not in self.entities:
            raise ValueError(f'No user has "{handle}" as username')
        return self.entities[handle]


def _run(monkeypatch, tmp_path, records, client, **kw):
    p = tmp_path / "runs.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    monkeypatch.setattr(tgc, "journal_path", lambda: p)
    monkeypatch.setattr(tgc, "UserClient", lambda: client)
    return asyncio.run(tgc.missing_contacts(resolve_delay=0, **kw))


def _verdicts(res):
    return {r["user_id"]: r["verdict"] for r in res["missing"]}


def test_present_contacts_are_not_missing(monkeypatch, tmp_path):
    res = _run(monkeypatch, tmp_path, [_w(1), _w(2)], _Client(present={1, 2}))
    assert res["missing"] == [] and res["present"] == 2


def test_by_id_tells_a_deleted_account_from_a_removed_contact(monkeypatch, tmp_path):
    client = _Client(present=set(), users=[
        SimpleNamespace(id=1, deleted=True), SimpleNamespace(id=2, deleted=False)])
    res = _run(monkeypatch, tmp_path, [_w(1, access_hash=11), _w(2, access_hash=22)], client)
    assert _verdicts(res) == {1: "deleted", 2: "removed"}
    assert client.lookups == [], "asking by id must spend no username lookups"


def test_without_a_hash_it_is_unknown_and_nothing_is_resolved(monkeypatch, tmp_path):
    client = _Client(present=set())
    res = _run(monkeypatch, tmp_path, [_w(1)], client)
    assert _verdicts(res) == {1: "unknown"}
    assert client.lookups == [], "--resolve is opt-in: it spends the daily budget"


def test_resolve_classifies_gone_reused_and_same_account(monkeypatch, tmp_path):
    client = _Client(present=set(), entities={
        "h2": SimpleNamespace(id=999, deleted=False),   # someone else holds it now
        "h3": SimpleNamespace(id=3, deleted=False),     # same account, just not a contact
    })
    res = _run(monkeypatch, tmp_path, [_w(1), _w(2), _w(3)], client, resolve=True)
    assert _verdicts(res) == {1: "handle_gone", 2: "handle_reused", 3: "removed"}
    assert res["resolved"] == 3


def test_a_flood_stops_the_lookups_and_is_never_read_as_gone(monkeypatch, tmp_path):
    client = _Client(present=set(), flood_on="h1")
    res = _run(monkeypatch, tmp_path, [_w(1), _w(2)], client, resolve=True)
    assert res["aborted"] and "FLOOD_WAIT" in res["aborted"]
    assert _verdicts(res) == {1: "unknown", 2: "unknown"}, "a flood is not a dead handle"
    assert client.lookups == ["h1"]


def test_the_lookup_cap_is_honoured(monkeypatch, tmp_path):
    client = _Client(present=set())
    res = _run(monkeypatch, tmp_path, [_w(i) for i in range(1, 6)], client,
               resolve=True, resolve_limit=2)
    assert len(client.lookups) == 2 and res["resolved"] == 2


def test_tag_scopes_the_report(monkeypatch, tmp_path):
    res = _run(monkeypatch, tmp_path, [_w(1, tag="DAVI"), _w(2, tag="MTP")],
               _Client(present=set()), tag="MTP")
    assert [r["user_id"] for r in res["missing"]] == [2]


@pytest.mark.parametrize("verdict", ["deleted", "removed", "handle_gone", "handle_reused", "unknown"])
def test_every_verdict_has_wording(verdict):
    assert tgc.MISSING_VERDICTS[verdict]


def test_a_later_write_supplies_the_access_hash_the_first_lacked():
    """A first, pre-hash write used to win outright, so a hash journalled later was
    never used and the contact stayed `unknown`."""
    exp = tgc.expected_contacts([_w(1, at="2026-08-01"),
                                 _w(1, action="update", at="2026-09-01", access_hash=77)])
    assert exp[1]["at"] == "2026-08-01"
    assert exp[1]["access_hash"] == 77


def test_an_unrelated_rpc_failure_is_not_a_gone_handle(monkeypatch, tmp_path):
    """Any non-flood RPCError used to become `handle_gone`, so a transient server
    error reported a live account as deleted or renamed."""
    from telethon.errors import RPCError, UsernameNotOccupiedError

    class _Erroring(_Client):
        async def get_entity(self, handle):
            self.lookups.append(handle)
            if handle == "h1":
                raise RPCError(request=None, message="INTERNAL_SERVER_ERROR", code=500)
            raise UsernameNotOccupiedError(request=None)

    res = _run(monkeypatch, tmp_path, [_w(1), _w(2)], _Erroring(present=set()), resolve=True)
    assert _verdicts(res) == {1: "unknown", 2: "handle_gone"}
    row = next(r for r in res["missing"] if r["user_id"] == 1)
    assert row["lookup_error"] == "RPCError"
