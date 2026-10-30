"""The tag helpers and the planner — the parts that decide what gets written.

Everything here is pure: no Telethon, no network. The planner is what stands
between "file a group into my contacts" and "overwrite 300 curated names", so
its buckets are pinned by test rather than by inspection.
"""

from __future__ import annotations

import json

import pytest

from navig.telegram import contacts as tgc

# ── the marker itself ────────────────────────────────────────────────────────


@pytest.mark.parametrize("last,expected", [
    ("Popov", "Popov MTP"),
    ("", "MTP"),
    (None, "MTP"),
    ("   ", "MTP"),
    ("Popov MTP", "Popov MTP"),          # idempotent — re-running adds nothing
    ("MTP", "MTP"),
    ("Бабенко", "Бабенко MTP"),
    ("van der Berg", "van der Berg MTP"),
])
def test_apply_tag(last, expected):
    assert tgc.apply_tag(last, "MTP") == expected


def test_apply_tag_is_idempotent_under_repetition():
    v = None
    for _ in range(5):
        v = tgc.apply_tag(v, "MTP")
    assert v == "MTP"


@pytest.mark.parametrize("last", ["MTPX", "XMTP", "Mtp", "SomeMTPName"])
def test_tag_matches_whole_words_only(last):
    """A surname that merely contains the letters is not tagged.

    Without the word boundary, a real surname like "Mtprovich" would be read as
    already-tagged and the person silently skipped forever.
    """
    assert not tgc.is_tagged(None, last, "MTP")
    assert tgc.apply_tag(last, "MTP") == f"{last} MTP"


def test_is_tagged_checks_both_names():
    assert tgc.is_tagged("Katya", "MTP")
    assert tgc.is_tagged("MTP", None)
    assert not tgc.is_tagged("Katya", "Ivanova")


@pytest.mark.parametrize("last,expected", [
    ("Popov MTP", "Popov"),
    ("MTP", ""),
    ("Popov", "Popov"),
    ("MTP Popov", "Popov"),
])
def test_strip_tag(last, expected):
    assert tgc.strip_tag(last, "MTP") == expected


# ── the planner ──────────────────────────────────────────────────────────────


def _member(uid, **kw):
    base = {"user_id": uid, "username": f"u{uid}", "first_name": f"F{uid}",
            "last_name": f"L{uid}", "is_bot": False, "deleted": False,
            "is_contact": False}
    base.update(kw)
    return base


def _plan(members, contacts=None, **kw):
    kw.setdefault("mark", "surname")
    return tgc.plan_tagging(members, contacts or {}, me_id=999, **kw)


def test_new_member_is_added_with_the_tag():
    (row,) = _plan([_member(1)])
    assert row["action"] == "add"
    assert row["new_first"] == "F1"
    assert row["new_last"] == "L1 MTP"


def test_self_bots_and_deleted_are_never_written():
    plan = _plan([_member(999), _member(2, is_bot=True), _member(3, deleted=True)])
    assert [r["action"] for r in plan] == ["self", "bot", "deleted"]
    assert tgc.summarize_plan(plan)["add"] == 0


def test_bots_are_included_on_request():
    (row,) = _plan([_member(2, is_bot=True)], include_bots=True)
    assert row["action"] == "add"


def test_already_tagged_contact_is_skipped():
    contacts = {1: {"user_id": 1, "first_name": "Alexei", "last_name": "Popov MTP"}}
    (row,) = _plan([_member(1)], contacts)
    assert row["action"] == "already-tagged"


def test_existing_untagged_contact_is_left_alone_by_default():
    """The default must not touch names you already curated."""
    contacts = {1: {"user_id": 1, "first_name": "Alexei", "last_name": "Popov"}}
    (row,) = _plan([_member(1)], contacts)
    assert row["action"] == "existing-untagged"
    assert "new_last" not in row


def test_update_existing_keeps_the_saved_name_not_the_profile_name():
    """The saved name wins.

    A contact you filed as "Alexei Popov" must not become whatever nickname they
    are using in the group today just because you tagged them.
    """
    contacts = {1: {"user_id": 1, "first_name": "Alexei", "last_name": "Popov"}}
    members = [_member(1, first_name="🔥Лёха🔥", last_name="")]
    (row,) = _plan(members, contacts, update_existing=True)
    assert row["action"] == "update"
    assert row["before_first"], row["before_last"] == ("Alexei", "Popov")
    assert (row["new_first"], row["new_last"]) == ("Alexei", "Popov MTP")


def test_nameless_member_falls_back_to_a_handle_not_an_empty_name():
    (row,) = _plan([_member(1, first_name="", last_name="")])
    assert row["new_first"] == "u1"
    assert row["new_last"] == "MTP"


def test_nameless_and_handleless_member_falls_back_to_the_id():
    (row,) = _plan([_member(1, first_name="", last_name="", username=None)])
    assert row["new_first"] == "1"


def test_every_member_lands_in_exactly_one_known_bucket():
    members = [_member(i) for i in range(1, 6)] + [_member(999)]
    plan = _plan(members, {2: {"user_id": 2, "first_name": "A", "last_name": "B MTP"}})
    assert len(plan) == len(members)
    assert all(r["action"] in tgc.ACTIONS for r in plan)
    assert sum(tgc.summarize_plan(plan).values()) == len(members)


def test_custom_tag_is_honoured():
    (row,) = _plan([_member(1)], tag="VIBE")
    assert row["new_last"] == "L1 VIBE"


# ── the note marker (the default) ────────────────────────────────────────────


def test_note_mode_leaves_the_name_completely_alone():
    """The whole point of notes: a real surname stays a real surname."""
    (row,) = _plan([_member(1)], mark="note", note="MTP · g · 2026-08-21")
    assert row["action"] == "add"
    assert (row["new_first"], row["new_last"]) == ("F1", "L1")
    assert row["new_note"] == "MTP · g · 2026-08-21"


def test_surname_mode_writes_no_note():
    (row,) = _plan([_member(1)], mark="surname", note="ignored")
    assert row["new_note"] is None
    assert row["new_last"] == "L1 MTP"


def test_both_mode_writes_name_and_note():
    (row,) = _plan([_member(1)], mark="both", note="N")
    assert row["new_last"] == "L1 MTP"
    assert row["new_note"] == "N"


def test_note_mode_sees_an_existing_note_marker_as_already_marked():
    contacts = {1: {"user_id": 1, "first_name": "A", "last_name": "B"}}
    (row,) = _plan([_member(1)], contacts, mark="note", note="MTP · g · d",
                   notes_by_id={1: "MTP · g · 2026-01-01"})
    assert row["action"] == "already-tagged"


def test_note_mode_without_fetched_notes_degrades_to_a_rewrite_not_a_wrong_name():
    """Missing note data may cost a redundant write; it must never mangle a name."""
    contacts = {1: {"user_id": 1, "first_name": "A", "last_name": "B"}}
    (row,) = _plan([_member(1)], contacts, mark="note", note="N", update_existing=True)
    assert row["action"] == "update"
    assert (row["new_first"], row["new_last"]) == ("A", "B")
    assert row["new_note"] == "N"


def test_surname_marker_alone_is_not_already_marked_in_note_mode():
    """A legacy surname-tagged contact still needs its note written."""
    contacts = {1: {"user_id": 1, "first_name": "A", "last_name": "B MTP"}}
    (row,) = _plan([_member(1)], contacts, mark="note", note="N",
                   notes_by_id={1: ""})
    assert row["action"] == "existing-untagged"


def test_both_mode_requires_both_markers_to_count_as_done():
    contacts = {1: {"user_id": 1, "first_name": "A", "last_name": "B MTP"}}
    (row,) = _plan([_member(1)], contacts, mark="both", note="N",
                   notes_by_id={1: ""})
    assert row["action"] == "existing-untagged"


def test_update_captures_the_previous_note_so_undo_can_restore_it():
    contacts = {1: {"user_id": 1, "first_name": "A", "last_name": "B"}}
    (row,) = _plan([_member(1)], contacts, mark="note", note="new",
                   notes_by_id={1: "handwritten"}, update_existing=True)
    assert row["before_note"] == "handwritten"
    assert row["new_note"] == "new"


def test_an_unknown_mark_is_rejected_rather_than_silently_ignored():
    with pytest.raises(ValueError, match="mark must be one of"):
        tgc.plan_tagging([_member(1)], {}, me_id=999, mark="surnam")


# ── note rendering ───────────────────────────────────────────────────────────


def test_note_template_fills_placeholders_and_strips_the_at_sign():
    got = tgc.render_note(tag="MTP", group="@vibe_sud_france", date="2026-08-21")
    assert got == "MTP · vibe_sud_france · 2026-08-21"


def test_note_is_truncated_to_telegrams_limit():
    """An oversized note fails the whole addContact call, so clamp it here."""
    got = tgc.render_note("{tag} " + "x" * 400, tag="MTP", group="g", date="d")
    assert len(got) <= tgc.NOTE_LENGTH_LIMIT
    assert got.endswith("…")


def test_a_note_that_fits_is_left_exactly_alone():
    got = tgc.render_note("{tag}", tag="MTP", group="g", date="d")
    assert got == "MTP"


# ── the default must stay findable ───────────────────────────────────────────


class _Ent:
    """Stand-in for a resolved telethon entity."""
    def __init__(self, cls, **kw):
        self.__class__ = type(cls, (_Ent,), {})
        self.__dict__.update(kw)


def _fake(cls, **kw):
    obj = _Ent.__new__(type(cls, (object,), {}))
    obj.__dict__.update(kw)
    return obj


def test_a_channel_handle_is_rejected_rather_than_crashing_the_run():
    """t.me/foo may be a channel, and addContact raises TypeError on one.

    That TypeError is not an RPCError, so before this check a single channel in
    a handle list aborted the entire batch.
    """
    assert tgc.taggable_reason(_fake("Channel", id=1)) == "not a person (Channel)"
    assert tgc.taggable_reason(_fake("Chat", id=1)) == "not a person (Chat)"


def test_deleted_and_bot_accounts_are_rejected():
    assert tgc.taggable_reason(_fake("User", id=1, deleted=True)) == "account is deleted"
    assert tgc.taggable_reason(_fake("User", id=1, bot=True)) == "is a bot"


def test_a_real_person_is_taggable():
    assert tgc.taggable_reason(_fake("User", id=1, deleted=False, bot=False)) is None


# ── per-person surname override ──────────────────────────────────────────────


def test_an_explicit_surname_base_becomes_the_marker_prefix():
    """{"last_name": "Lyon"} + tag DAVI → "Lyon DAVI"."""
    assert tgc.apply_tag("Lyon", "DAVI") == "Lyon DAVI"
    assert tgc.apply_tag("MINSK", "DAVI") == "MINSK DAVI"


def test_an_empty_override_yields_the_bare_marker():
    assert tgc.apply_tag("", "DAVI") == "DAVI"


def test_the_override_is_still_idempotent():
    assert tgc.apply_tag("Lyon DAVI", "DAVI") == "Lyon DAVI"


# ── a write that Telegram accepts but does not keep ──────────────────────────


def test_a_write_whose_contact_never_appeared_is_reported():
    """addContact can succeed without the contact sticking.

    Observed on a real account: @kamiliana1 was written, journalled and counted,
    and never became a contact — she had blocked the owner. Counting sent
    requests as filed people over-reports the result.
    """
    written = [{"user_id": 1, "username": "kept"}, {"user_id": 2, "username": "vanished"}]
    got = tgc.unpersisted_ids(written, {1})
    assert [r["username"] for r in got] == ["vanished"]


def test_nothing_is_reported_when_every_write_stuck():
    written = [{"user_id": 1}, {"user_id": 2}]
    assert tgc.unpersisted_ids(written, {1, 2}) == []


def test_an_empty_run_reports_nothing():
    assert tgc.unpersisted_ids([], set()) == []


def test_contacts_present_but_never_written_are_not_reported():
    """The check is one-directional: it audits this run, not the whole list."""
    assert tgc.unpersisted_ids([{"user_id": 1}], {1, 99, 100}) == []


# ── last-seen buckets ────────────────────────────────────────────────────────


class _Status:
    def __init__(self, cls):
        self.__class__ = type(cls, (object,), {})


def _user_with_status(cls_name):
    u = _fake("User", id=1)
    u.status = None if cls_name is None else _fake(cls_name)
    return u


def test_absent_status_is_the_long_time_ago_bucket():
    """No status is what Telegram renders as 'last seen a long time ago'."""
    assert tgc.status_bucket(_user_with_status(None)) == "gone"
    assert tgc.status_bucket(_user_with_status("UserStatusEmpty")) == "gone"


@pytest.mark.parametrize("cls,bucket", [
    ("UserStatusOnline", "online"),
    ("UserStatusOffline", "offline"),
    ("UserStatusRecently", "recently"),
    ("UserStatusLastWeek", "last-week"),
    ("UserStatusLastMonth", "last-month"),
])
def test_known_statuses_map_to_their_bucket(cls, bucket):
    assert tgc.status_bucket(_user_with_status(cls)) == bucket


def test_recently_is_not_gone():
    """The common false positive: 'last seen recently' is a privacy bucket.

    Most of a marked cohort sits in `recently`; treating it as gone would delete
    almost everyone.
    """
    assert tgc.status_bucket(_user_with_status("UserStatusRecently")) != "gone"


def test_an_unrecognised_status_is_not_silently_pruned():
    """A future status class must not fall into a bucket prune acts on."""
    assert tgc.status_bucket(_user_with_status("UserStatusSomethingNew")) == "unknown"
    assert "unknown" not in tgc.PRUNE_STATUSES


def test_tag_handles_rejects_an_unknown_mark_before_touching_the_network():
    import asyncio
    with pytest.raises(ValueError, match="mark must be one of"):
        asyncio.run(tgc.tag_handles([{"handle": "@x"}], mark="nope"))


def test_the_default_mark_writes_the_name_so_contact_search_can_find_it():
    """Regression: the marker must land somewhere Telegram can actually search.

    Telegram's contact search indexes names only — tdesktop's ``UserData::note()``
    never reaches ``_nameWords``. A default that wrote only the note produced 30
    contacts that were invisible to a search for "MTP" on a real account.
    """
    (row,) = tgc.plan_tagging([_member(1)], {}, me_id=999, note="N")
    assert tgc.DEFAULT_MARK == "both"
    assert row["new_last"] == "L1 MTP", "default must tag the searchable surname"
    assert row["new_note"] == "N", "default must still carry the note"


# ── the journal ──────────────────────────────────────────────────────────────


def test_journal_round_trips_and_survives_a_torn_line(tmp_path, monkeypatch):
    """A half-written last line must not hide the runs before it."""
    p = tmp_path / "contact_runs.jsonl"
    monkeypatch.setattr(tgc, "journal_path", lambda: p)

    tgc._append_journal({"run_id": "r1", "kind": "write", "user_id": 1, "chat": "@g"})
    tgc._append_journal({"run_id": "r1", "kind": "write", "user_id": 2, "chat": "@g"})
    with p.open("a", encoding="utf-8") as fh:
        fh.write('{"run_id": "r1", "kind": "wri')  # crash mid-write

    recs = tgc.read_journal("r1")
    assert [r["user_id"] for r in recs] == [1, 2]
    (run,) = tgc.list_runs()
    assert run["run_id"] == "r1" and run["written"] == 2


def test_journal_is_written_as_utf8_not_escaped(tmp_path, monkeypatch):
    p = tmp_path / "contact_runs.jsonl"
    monkeypatch.setattr(tgc, "journal_path", lambda: p)
    tgc._append_journal({"run_id": "r", "kind": "write", "user_id": 1,
                         "new_last": "Бабенко MTP"})
    assert "Бабенко MTP" in p.read_text(encoding="utf-8")
    assert json.loads(p.read_text(encoding="utf-8"))["new_last"] == "Бабенко MTP"


def test_undone_entries_are_not_reverted_twice(tmp_path, monkeypatch):
    p = tmp_path / "contact_runs.jsonl"
    monkeypatch.setattr(tgc, "journal_path", lambda: p)
    tgc._append_journal({"run_id": "r", "kind": "write", "user_id": 1, "action": "add"})
    tgc._append_journal({"run_id": "r", "kind": "write", "user_id": 2, "action": "add"})
    tgc._append_journal({"run_id": "r", "kind": "undo", "user_id": 1, "action": "add"})

    import asyncio
    res = asyncio.run(tgc.undo_run("r", confirm=False))
    assert [r["user_id"] for r in res["todo"]] == [2]


# ── a flood on the post-wait retry still aborts the run ──────────────────────


class _FakeClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_me(self):
        return _fake("User", id=999)

    async def get_input_entity(self, uid):
        return _fake("User", id=uid, access_hash=1)

    async def get_entity(self, handle):
        uid = int(handle.lstrip("u"))
        return _fake("User", id=uid, first_name=f"F{uid}", last_name="", username=handle,
                    bot=False, deleted=False)


def _flood_then(second: Exception):
    """A write stub: the first call floods briefly, the retry raises *second*."""
    from telethon.errors import FloodWaitError

    calls: list[int] = []

    async def write(client, user, first, last, note=None):
        calls.append(user.id)
        if len(calls) == 1:
            raise FloodWaitError(request=None, capture=5)
        if len(calls) == 2:
            raise second

    return write, calls


def _second_errors():
    from telethon.errors import FloodWaitError, PeerFloodError

    return [PeerFloodError(request=None), FloodWaitError(request=None, capture=5)]


@pytest.fixture
def _offline(monkeypatch):
    async def no_sleep(_s):
        return None

    async def none(*_a, **_k):
        return []

    monkeypatch.setattr(tgc, "UserClient", _FakeClient)
    monkeypatch.setattr(tgc.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(tgc, "_contacts", none)
    monkeypatch.setattr(tgc, "_unpersisted", none)
    monkeypatch.setattr(tgc, "_append_journal", lambda rec: None)


@pytest.mark.parametrize("second", _second_errors(), ids=["peer-flood", "flood-again"])
def test_tag_members_aborts_when_the_retry_floods(_offline, monkeypatch, second):
    import asyncio

    async def members(client, chat, **_k):
        return [_member(1), _member(2), _member(3)]

    write, calls = _flood_then(second)
    monkeypatch.setattr(tgc, "_members", members)
    monkeypatch.setattr(tgc, "_write_contact", write)
    out = asyncio.run(tgc.tag_members("grp", mark="surname", confirm=True, delay=0))
    assert out["aborted"], "a flood on the retry must stop the run"
    assert calls == [1, 1], "no further member is written after the retry floods"
    assert out["failed"] == [] and out["written"] == []


@pytest.mark.parametrize("second", _second_errors(), ids=["peer-flood", "flood-again"])
def test_tag_handles_aborts_when_the_retry_floods(_offline, monkeypatch, second):
    import asyncio

    write, calls = _flood_then(second)
    monkeypatch.setattr(tgc, "_write_contact", write)
    entries = [{"handle": "u1"}, {"handle": "u2"}, {"handle": "u3"}]
    out = asyncio.run(tgc.tag_handles(entries, mark="surname", confirm=True, delay=0,
                                      resolve_delay=0))
    assert out["aborted"]
    assert calls == [1, 1]
    assert out["failed"] == [] and out["written"] == []


# ── a journal that cannot be written stops the run ───────────────────────────


def _journal_fails(monkeypatch):
    def boom(rec):
        raise OSError(30, "Read-only file system")

    monkeypatch.setattr(tgc, "_append_journal", boom)
    calls: list[int] = []

    async def write(client, user, first, last, note=None):
        calls.append(user.id)

    monkeypatch.setattr(tgc, "_write_contact", write)
    return calls


def test_tag_members_stops_and_names_the_write_it_could_not_journal(_offline, monkeypatch):
    """The Telegram write already happened: raising past it left the contact with no
    undo record and no report; carrying on wrote more changes nothing could reverse."""
    import asyncio

    async def members(client, chat, **_k):
        return [_member(1), _member(2), _member(3)]

    monkeypatch.setattr(tgc, "_members", members)
    calls = _journal_fails(monkeypatch)
    out = asyncio.run(tgc.tag_members("grp", mark="surname", confirm=True, delay=0))
    assert calls == [1], "no further contact is written once journaling fails"
    assert out["aborted"] and out["aborted"].startswith("JOURNAL"), out["aborted"]
    assert [r["user_id"] for r in out["written"]] == [1]


def test_tag_handles_stops_and_names_the_write_it_could_not_journal(_offline, monkeypatch):
    import asyncio

    calls = _journal_fails(monkeypatch)
    entries = [{"handle": "u1"}, {"handle": "u2"}]
    out = asyncio.run(tgc.tag_handles(entries, mark="surname", confirm=True, delay=0,
                                      resolve_delay=0))
    assert calls == [1]
    assert out["aborted"] and "@u1" in out["aborted"], out["aborted"]
    assert [r["user_id"] for r in out["written"]] == [1]


# ── undo resolves people a fresh session has never seen ──────────────────────


class _ColdClient(_FakeClient):
    """A fresh StringSession: no entity is cached, so a bare id cannot resolve."""

    async def get_input_entity(self, uid):
        raise ValueError(f"Could not find the input entity for {uid}")

    async def __call__(self, request):
        self.requests.append(request)
        return None


def test_undo_reverts_with_the_journaled_access_hash(tmp_path, monkeypatch):
    import asyncio

    p = tmp_path / "contact_runs.jsonl"
    monkeypatch.setattr(tgc, "journal_path", lambda: p)
    tgc._append_journal({"run_id": "r", "kind": "write", "user_id": 7, "action": "add",
                         "access_hash": 42})
    tgc._append_journal({"run_id": "r", "kind": "write", "user_id": 8, "action": "update",
                         "access_hash": 43, "before_first": "A", "before_last": "B",
                         "new_note": None})
    client = _ColdClient()
    client.requests = []
    monkeypatch.setattr(tgc, "UserClient", lambda: client)
    written: list = []

    async def write(c, user, first, last, note=None):
        written.append((user.user_id, user.access_hash, first, last))

    monkeypatch.setattr(tgc, "_write_contact", write)
    res = asyncio.run(tgc.undo_run("r", confirm=True))
    assert res["failed"] == []
    assert [r["user_id"] for r in res["reverted"]] == [7, 8]
    (delete,) = client.requests
    assert [(u.user_id, u.access_hash) for u in delete.id] == [(7, 42)]
    assert written == [(8, 43, "A", "B")]


def test_undo_warms_the_contact_list_for_rows_without_a_hash(tmp_path, monkeypatch):
    import asyncio

    p = tmp_path / "contact_runs.jsonl"
    monkeypatch.setattr(tgc, "journal_path", lambda: p)
    tgc._append_journal({"run_id": "r", "kind": "write", "user_id": 9, "action": "add"})
    warmed: list = []

    class _Warmable(_ColdClient):
        async def get_input_entity(self, uid):
            if not warmed:
                raise ValueError("not cached")
            return _fake("User", id=uid, access_hash=1)

    client = _Warmable()
    client.requests = []

    async def contacts(c):
        warmed.append(True)
        return []

    monkeypatch.setattr(tgc, "UserClient", lambda: client)
    monkeypatch.setattr(tgc, "_contacts", contacts)
    res = asyncio.run(tgc.undo_run("r", confirm=True))
    assert res["failed"] == [] and [r["user_id"] for r in res["reverted"]] == [9]


@pytest.mark.parametrize("flood", ["long-flood-wait", "peer-flood", "flood-again"])
def test_undo_stops_on_a_flood_instead_of_firing_the_rest(tmp_path, monkeypatch, flood):
    """A flood mid-undo used to read as one contact's failure, and the loop fired
    every remaining request into the known rate-limit window."""
    import asyncio

    from telethon.errors import FloodWaitError, PeerFloodError

    p = tmp_path / "contact_runs.jsonl"
    monkeypatch.setattr(tgc, "journal_path", lambda: p)
    for uid in (1, 2, 3):
        tgc._append_journal({"run_id": "r", "kind": "write", "user_id": uid,
                             "action": "update", "access_hash": uid, "new_note": None})
    client = _ColdClient()
    client.requests = []
    monkeypatch.setattr(tgc, "UserClient", lambda: client)
    slept: list[float] = []

    async def no_sleep(s):
        slept.append(s)

    monkeypatch.setattr(tgc.asyncio, "sleep", no_sleep)
    attempted: list[int] = []

    async def write(c, user, first, last, note=None):
        attempted.append(user.user_id)
        if user.user_id != 2:
            return
        if flood == "long-flood-wait":
            raise FloodWaitError(request=None, capture=600)
        if flood == "peer-flood":
            raise PeerFloodError(request=None)
        raise FloodWaitError(request=None, capture=5)  # short, but floods again on retry

    monkeypatch.setattr(tgc, "_write_contact", write)
    res = asyncio.run(tgc.undo_run("r", confirm=True, delay=0.5))
    assert res["aborted"], "a flood must stop the undo"
    assert 3 not in attempted, "nothing is sent after the flood"
    assert [r["user_id"] for r in res["reverted"]] == [1]
    assert res["failed"] == []
    assert 0.5 in slept, "requests are spaced by the delay"
    # The unreached records are still un-undone, so a later undo resumes them.
    assert [r["user_id"] for r in asyncio.run(tgc.undo_run("r"))["todo"]] == [2, 3]


# ── an overwritten note is journaled so undo can bring it back ───────────────


def test_tag_handles_journals_the_note_it_overwrites(_offline, monkeypatch):
    import asyncio

    async def contacts(c):
        return [{"user_id": 1, "first_name": "Ann", "last_name": "Lee"}]

    async def read_note(c, user):
        return "met in Lyon"

    async def write(*_a, **_k):
        return None

    monkeypatch.setattr(tgc, "_contacts", contacts)
    monkeypatch.setattr(tgc, "_read_note", read_note)
    monkeypatch.setattr(tgc, "_write_contact", write)
    out = asyncio.run(tgc.tag_handles([{"handle": "u1"}], mark="note", confirm=True,
                                      delay=0, resolve_delay=0))
    (rec,) = out["written"]
    assert rec["action"] == "update" and rec["before_note"] == "met in Lyon"


def _saved_tagged(monkeypatch, note: str):
    async def contacts(c):
        return [{"user_id": 1, "first_name": "Ivan", "last_name": "Popov MTP"}]

    async def read_note(c, user):
        return note

    monkeypatch.setattr(tgc, "_contacts", contacts)
    monkeypatch.setattr(tgc, "_read_note", read_note)


@pytest.mark.parametrize("mark", ["both", "note"])
def test_tag_handles_adds_the_missing_note_to_a_surname_tagged_contact(_offline, monkeypatch,
                                                                      mark):
    """A legacy surname-tagged contact used to be skipped whatever the mark, so in
    note/both mode it never received the note the run was asked to write."""
    import asyncio

    _saved_tagged(monkeypatch, "")
    out = asyncio.run(tgc.tag_handles([{"handle": "u1"}], tag="MTP", mark=mark,
                                      resolve_delay=0))
    (row,) = out["todo"]
    assert row["new_note"] == "MTP"
    assert (row["new_first"], row["new_last"]) == ("Ivan", "Popov MTP"), "the name stays as saved"


def test_tag_handles_skips_a_contact_that_carries_every_marker(_offline, monkeypatch):
    import asyncio

    _saved_tagged(monkeypatch, "MTP")
    out = asyncio.run(tgc.tag_handles([{"handle": "u1"}], tag="MTP", mark="both",
                                      resolve_delay=0))
    assert out["todo"] == []


def test_tag_handles_paces_every_lookup_not_only_the_ones_it_keeps(_offline, monkeypatch):
    """Dead handles `continue` before a row is appended; the delay used to run only
    after an append, so consecutive dead lookups went out back-to-back."""
    import asyncio

    slept: list[float] = []

    async def record_sleep(s):
        slept.append(s)

    async def dead(self, handle):
        raise ValueError(f"No user has {handle!r} as username")

    monkeypatch.setattr(tgc.asyncio, "sleep", record_sleep)
    monkeypatch.setattr(_FakeClient, "get_entity", dead)
    out = asyncio.run(tgc.tag_handles([{"handle": "u1"}, {"handle": "u2"}, {"handle": "u3"}],
                                      resolve_delay=1.5))
    assert len(out["unresolved"]) == 3
    assert slept == [1.5, 1.5], "one pause between each pair of lookups"


# ── a flood on the resolve retry is never an unresolved handle ───────────────


def test_tag_handles_aborts_rather_than_marking_a_twice_flooded_handle_dead(_offline,
                                                                          monkeypatch):
    import asyncio

    from telethon.errors import FloodWaitError

    async def get_entity(self, handle):
        raise FloodWaitError(request=None, capture=5)

    monkeypatch.setattr(_FakeClient, "get_entity", get_entity)
    out = asyncio.run(tgc.tag_handles([{"handle": "u1"}, {"handle": "u2"}], mark="surname",
                                      confirm=False, resolve_delay=0))
    assert out["unresolved"] == []
    assert out["aborted"] and "FLOOD_WAIT" in out["aborted"]


# ── note mode never rewrites a surname, even with an explicit override ───────


def test_tag_handles_note_mode_ignores_a_surname_override(_offline, monkeypatch):
    """A `last_name` override used to replace the saved surname in note mode,
    where the documented promise is that names are left untouched."""
    import asyncio

    async def contacts(c):
        return [{"user_id": 1, "first_name": "Ann", "last_name": "Lee"}]

    async def read_note(c, user):
        return ""

    monkeypatch.setattr(tgc, "_contacts", contacts)
    monkeypatch.setattr(tgc, "_read_note", read_note)
    out = asyncio.run(tgc.tag_handles([{"handle": "u1", "last_name": "Lyon"}], tag="MTP",
                                      mark="note", resolve_delay=0))
    (row,) = out["todo"]
    assert (row["new_first"], row["new_last"]) == ("Ann", "Lee")
    assert row["new_note"] == "MTP"


# ── prune --tag finds a cohort marked only in the note ───────────────────────


def test_prune_tag_scope_matches_a_note_only_marker(_offline, monkeypatch):
    """A `--mark note` cohort carries its marker only in the private note; the
    name-only filter used to plan nothing for it."""
    import asyncio

    users = [_fake("User", id=1, first_name="Ann", last_name="Lee", username="u1",
                   status=None),
             _fake("User", id=2, first_name="Bob", last_name="Ray MTP", username="u2",
                   status=None),
             _fake("User", id=3, first_name="Cy", last_name="Doe", username="u3",
                   status=None)]

    async def call(self, request):
        return _fake("Contacts", users=users)

    asked: list[int] = []

    async def read_note(c, user):
        asked.append(user.id)
        return "MTP" if user.id == 1 else "met in Lyon"

    monkeypatch.setattr(_FakeClient, "__call__", call, raising=False)
    monkeypatch.setattr(tgc, "_read_note", read_note)
    out = asyncio.run(tgc.prune_contacts(tag="MTP"))
    assert sorted(r["user_id"] for r in out["todo"]) == [1, 2]
    assert sorted(asked) == [1, 3], "notes are read only where the name misses the marker"


# ── undoing a prune never re-adds whoever holds the username now ─────────────


def test_undo_prune_refuses_a_reassigned_username(tmp_path, monkeypatch):
    import asyncio

    p = tmp_path / "contact_runs.jsonl"
    monkeypatch.setattr(tgc, "journal_path", lambda: p)
    tgc._append_journal({"run_id": "r", "kind": "prune", "user_id": 5, "username": "u77",
                         "removed_first": "Ann", "removed_last": "Lee"})
    client = _FakeClient()
    monkeypatch.setattr(tgc, "UserClient", lambda: client)
    written: list = []

    async def write(c, user, first, last, note=None):
        written.append(user.id)

    monkeypatch.setattr(tgc, "_write_contact", write)
    res = asyncio.run(tgc.undo_run("r", confirm=True))
    assert written == [], "the new owner of the handle is never added"
    assert res["reverted"] == []
    (row,) = res["failed"]
    assert row["error"] == "HandleReassigned"
    # Not journalled as undone, so the record is still pending.
    assert [r["user_id"] for r in asyncio.run(tgc.undo_run("r"))["todo"]] == [5]


def test_persisted_count_excludes_writes_that_did_not_stick():
    written = [{"user_id": 1}, {"user_id": 2}, {"user_id": 3}]
    assert tgc.persisted_count(written, [{"user_id": 2}]) == 2
    assert tgc.persisted_count(written, []) == 3


def test_tag_list_success_line_counts_only_contacts_that_stuck(tmp_path, monkeypatch):
    """`Not counted as filed` must be true: the success line used len(written)."""
    from typer.testing import CliRunner

    import navig.commands.telegram as tgcmd

    written = [{"user_id": i, "handle": f"u{i}"} for i in range(1, 4)]
    gone = [written[1]]

    async def fake_tag_handles(entries, **kw):
        return {"run_id": "r1", "label": "handles", "tag": "MTP", "mark": "both",
                "todo": written, "unresolved": [], "written": written, "failed": [],
                "aborted": None, "not_persisted": gone,
                "persisted": tgc.persisted_count(written, gone), "dry_run": False}

    monkeypatch.setattr(tgc, "tag_handles", fake_tag_handles)
    f = tmp_path / "h.jsonl"
    f.write_text('{"handle": "@u1"}\n', encoding="utf-8")
    res = CliRunner().invoke(
        tgcmd.telegram_app, ["contacts", "tag-list", str(f), "--confirm"])
    assert res.exit_code == 0, res.output
    assert "2 marked" in res.output
    assert "3 marked" not in res.output
