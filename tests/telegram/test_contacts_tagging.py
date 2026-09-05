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
