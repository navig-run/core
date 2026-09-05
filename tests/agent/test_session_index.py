"""Unit tests for navig.memory.session_index.SessionEventIndex.

Pure-logic (stdlib sqlite3 only) so they run anywhere FTS5 is compiled in. Drop-in target:
``core/tests/agent/test_session_index.py``.
"""

from __future__ import annotations

import sqlite3
import threading

import pytest

from navig.memory.session_index import (
    SessionEvent,
    SessionEventIndex,
    fts5_available,
    get_session_index,
    record_event,
    recover_context,
    render_recovery,
    reset_session_index,
    session_index_enabled,
)

pytestmark = pytest.mark.skipif(not fts5_available(), reason="SQLite built without FTS5")


@pytest.fixture
def idx() -> SessionEventIndex:
    return SessionEventIndex.open(":memory:")


def test_fts5_available() -> None:
    assert fts5_available() is True


def test_ensure_schema_is_idempotent(idx: SessionEventIndex) -> None:
    idx.ensure_schema()
    idx.ensure_schema()  # must not raise on an existing table
    assert idx.stats()["events"] == 0


def test_record_returns_monotonic_seq(idx: SessionEventIndex) -> None:
    a = idx.record("s1", "tool_result", "first")
    b = idx.record("s1", "tool_result", "second")
    assert b > a


def test_search_returns_a_match(idx: SessionEventIndex) -> None:
    idx.record("s1", "file_edit", "refactored the approval gate in trust.py")
    hits = idx.search("s1", "approval gate")
    assert len(hits) == 1
    assert "approval gate" in hits[0].text
    assert hits[0].kind == "file_edit"


def test_search_matches_accented_latin_and_cyrillic(idx: SessionEventIndex) -> None:
    # ASCII-only [A-Za-z_] would have stripped these to nothing; \w+ keeps them so any
    # space-delimited script is searchable.
    idx.record("s1", "note", "déployé le café résumé serveur")
    idx.record("s1", "note", "Привет мир сервер")
    assert len(idx.search("s1", "café")) == 1
    assert len(idx.search("s1", "résumé")) == 1
    assert len(idx.search("s1", "déployé")) == 1
    assert len(idx.search("s1", "Привет")) == 1  # Cyrillic
    assert len(idx.search("s1", "сервер")) == 1


def test_cjk_matches_on_a_full_token(idx: SessionEventIndex) -> None:
    # unicode61 has no CJK word segmentation: a space-less run is one token, so an exact
    # token matches (a substring would need a segmenting tokenizer — documented limitation).
    idx.record("s1", "note", "中文文档 已更新")
    assert len(idx.search("s1", "中文文档")) == 1
    assert len(idx.search("s1", "已更新")) == 1


def test_search_is_scoped_to_session(idx: SessionEventIndex) -> None:
    idx.record("s1", "note", "secret one belongs to session one")
    idx.record("s2", "note", "secret two belongs to session two")
    hits = idx.search("s1", "secret")
    assert len(hits) == 1
    assert hits[0].session_id == "s1"
    assert "session one" in hits[0].text  # no cross-session leak


def test_bm25_ranks_more_relevant_first(idx: SessionEventIndex) -> None:
    idx.record("s1", "note", "one passing mention of deploy amid a long unrelated ramble "
                             "about planning and budgets and hiring and roadmaps and lunch")
    idx.record("s1", "note", "deploy deploy deploy pipeline")  # denser + shorter → better BM25
    hits = idx.search("s1", "deploy")
    assert len(hits) == 2
    assert hits[0].text.startswith("deploy deploy deploy")


def test_clear_removes_only_that_session(idx: SessionEventIndex) -> None:
    idx.record("s1", "note", "alpha alpha")
    idx.record("s2", "note", "alpha alpha")
    removed = idx.clear("s1")
    assert removed == 1
    assert idx.search("s1", "alpha") == []
    assert len(idx.search("s2", "alpha")) == 1  # sibling session untouched


def test_prune_keeps_newest_and_scopes_to_session(idx: SessionEventIndex) -> None:
    for i in range(6):
        idx.record("s1", "note", f"event {i}")
    idx.record("s2", "note", "sibling")
    assert idx.prune("s1", keep=2) == 4
    assert [e.text for e in idx.recent("s1", limit=10)] == ["event 5", "event 4"]
    assert idx.stats("s2")["events"] == 1  # sibling session untouched


def test_prune_keep_zero_clears_the_session(idx: SessionEventIndex) -> None:
    idx.record("s1", "note", "a")
    idx.record("s1", "note", "b")
    assert idx.prune("s1", keep=0) == 2
    assert idx.stats("s1")["events"] == 0


def test_prune_under_the_limit_deletes_nothing(idx: SessionEventIndex) -> None:
    idx.record("s1", "note", "only one")
    assert idx.prune("s1", keep=5) == 0
    assert idx.stats("s1")["events"] == 1


@pytest.mark.parametrize("query", ["", "   ", "!!! ??? ...", "\n\t"])
def test_blank_or_punctuation_query_returns_empty(idx: SessionEventIndex, query: str) -> None:
    idx.record("s1", "note", "something searchable here")
    assert idx.search("s1", query) == []


@pytest.mark.parametrize(
    "query",
    [
        '"',                       # a lone quote
        "foo) OR bar",             # stray paren + operator
        'NEAR("a" "b")',           # FTS5 function syntax
        "*",                       # bare wildcard
        "session_events MATCH x",  # keyword salad
        "'; DROP TABLE session_events; --",  # SQL-injection shape
    ],
)
def test_adversarial_query_never_raises_and_never_mutates(
    idx: SessionEventIndex, query: str
) -> None:
    idx.record("s1", "note", "a b c payload that must survive")
    result = idx.search("s1", query)  # must not raise
    assert isinstance(result, list)
    # the table (and its data) must still be intact after every hostile query
    assert idx.stats("s1")["events"] == 1


def test_recent_returns_newest_first(idx: SessionEventIndex) -> None:
    idx.record("s1", "note", "oldest")
    idx.record("s1", "note", "middle")
    idx.record("s1", "note", "newest")
    texts = [e.text for e in idx.recent("s1", limit=2)]
    assert texts == ["newest", "middle"]


def test_stats_scopes_to_session_or_whole_index(idx: SessionEventIndex) -> None:
    idx.record("s1", "note", "x")
    idx.record("s1", "note", "y")
    idx.record("s2", "note", "z")
    assert idx.stats("s1")["events"] == 2
    assert idx.stats("s2")["events"] == 1
    assert idx.stats()["events"] == 3


def test_reopen_persists_and_integrity_is_ok(tmp_path) -> None:
    path = str(tmp_path / "session.db")
    ix = SessionEventIndex.open(path)
    for i in range(50):
        ix.record("s1", "tool_result", f"event number {i} mentions widget {i % 5}")
    ix.close()

    reopened = SessionEventIndex.open(path)
    hits = reopened.search("s1", "widget")
    assert len(hits) == 10  # default limit; all 50 match "widget"
    assert reopened.stats("s1")["events"] == 50
    # the standalone-FTS5 design's whole point: no external-content drift, no corruption
    integrity = reopened._conn.execute("PRAGMA integrity_check").fetchone()[0]
    assert integrity == "ok"
    reopened.close()


def test_search_on_empty_index_returns_empty(idx: SessionEventIndex) -> None:
    assert idx.search("s1", "anything") == []
    assert idx.recent("s1") == []


def test_clear_absent_session_returns_zero(idx: SessionEventIndex) -> None:
    assert idx.clear("never-existed") == 0


def test_explicit_timestamp_round_trips(idx: SessionEventIndex) -> None:
    idx.record("s1", "note", "stamped", ts=1234.5)
    (event,) = idx.recent("s1")
    assert event.ts == 1234.5
    assert event.kind == "note"


@pytest.mark.parametrize("limit", [0, -1, -50])
def test_non_positive_limit_returns_empty(idx: SessionEventIndex, limit: int) -> None:
    # A non-positive limit means "nothing requested" — and must never become LIMIT -1,
    # which SQLite reads as "every row".
    idx.record("s1", "note", "alpha alpha alpha")
    assert idx.search("s1", "alpha", limit=limit) == []
    assert idx.recent("s1", limit=limit) == []


def test_accepts_an_injected_connection() -> None:
    # navig will hand it the storage engine's connection rather than a path.
    conn = sqlite3.connect(":memory:")
    ix = SessionEventIndex(conn)
    ix.record("s1", "note", "shared connection works")
    assert len(ix.search("s1", "shared")) == 1


# --- retrieval → re-injection (render_recovery / search_and_render) ------------


def _ev(kind: str, text: str, seq: int = 1) -> SessionEvent:
    return SessionEvent(seq=seq, session_id="s1", kind=kind, ts=0.0, text=text)


def test_render_recovery_empty_is_blank() -> None:
    assert render_recovery([]) == ""
    assert render_recovery([_ev("note", "x")], max_chars=0) == ""


def test_render_recovery_includes_kind_and_text() -> None:
    out = render_recovery([_ev("file_edit", "touched trust.py")])
    assert out.startswith("## Recovered context")
    assert "[file_edit] touched trust.py" in out


def test_render_recovery_collapses_newlines_to_one_line() -> None:
    out = render_recovery([_ev("tool_result", "line one\n\n  line two\ttabbed")])
    body = out.splitlines()[1]  # the event line, after the header
    assert body == "- [tool_result] line one line two tabbed"


def test_render_recovery_respects_budget_and_notes_omissions() -> None:
    events = [_ev("note", f"event {i} with some words to take up room", seq=i) for i in range(20)]
    out = render_recovery(events, max_chars=200)
    assert len(out) <= 200 + 40  # budget honoured (+ the short "more" line)
    assert "(+" in out and "more)" in out  # omissions are disclosed, not silently dropped


def test_render_recovery_always_shows_at_least_one_event() -> None:
    huge = _ev("tool_result", "x" * 5000)
    out = render_recovery([huge], max_chars=50)
    assert "[tool_result]" in out          # the one event is shown…
    assert out.splitlines()[1].endswith("…")  # …but capped, so it can't run away


def test_search_and_render_round_trip(idx: SessionEventIndex) -> None:
    idx.record("s1", "file_edit", "refactored the approval gate")
    idx.record("s1", "note", "unrelated lunch plans")
    block = idx.search_and_render("s1", "approval gate")
    assert "approval gate" in block
    assert "lunch" not in block  # only the relevant event is recovered


def test_search_and_render_no_match_is_blank(idx: SessionEventIndex) -> None:
    idx.record("s1", "note", "something")
    assert idx.search_and_render("s1", "nonexistentterm") == ""


# --- best-effort resilience (never crash the conversation on a transient DB error) ------


def test_operations_degrade_on_a_broken_connection(caplog) -> None:
    import logging

    conn = sqlite3.connect(":memory:")
    ix = SessionEventIndex(conn)
    ix.record("s1", "note", "recorded while healthy")
    conn.close()  # every subsequent op now raises sqlite3.ProgrammingError

    with caplog.at_level(logging.WARNING):
        # Reads degrade to their empty value; a write returns -1; nothing raises.
        assert ix.record("s1", "note", "this write is lost") == -1
        assert ix.search("s1", "recorded") == []
        assert ix.recent("s1") == []
        assert ix.search_and_render("s1", "recorded") == ""
        assert ix.clear("s1") == 0
        assert ix.stats("s1") == {"events": 0}
    # The failures are logged (visible), not swallowed silently.
    assert any("best-effort" in r.getMessage() for r in caplog.records)


def test_record_survives_none_lastrowid() -> None:
    # ``lastrowid`` is typed int|None. If a driver ever returns None, record must return -1,
    # not raise TypeError — which, not being a sqlite3.Error, would bypass best-effort.
    conn = sqlite3.connect(":memory:")
    ix = SessionEventIndex(conn)

    class _NullRowidConn:
        def __init__(self, inner: sqlite3.Connection) -> None:
            self._inner = inner

        def execute(self, *a: object, **k: object) -> object:
            inner = self._inner.execute(*a, **k)  # type: ignore[arg-type]

            class _Cur:
                lastrowid = None
                rowcount = inner.rowcount

                def fetchall(self_) -> list:
                    return inner.fetchall()

                def fetchone(self_) -> object:
                    return inner.fetchone()

            return _Cur()

        def commit(self) -> None:
            self._inner.commit()

    ix._conn = _NullRowidConn(conn)  # type: ignore[assignment]
    assert ix.record("s1", "note", "x") == -1  # returned, not raised


def test_usable_from_another_thread(idx: SessionEventIndex) -> None:
    # Python's sqlite3 refuses cross-thread use by default, and that ProgrammingError IS an
    # sqlite3.Error — so without check_same_thread=False the index would silently record
    # nothing in navig's multi-threaded daemon. This pins the fix.
    out: dict[str, int] = {}

    def worker() -> None:
        out["seq"] = idx.record("s1", "note", "written from a worker thread")
        out["hits"] = len(idx.search("s1", "worker"))

    t = threading.Thread(target=worker)
    t.start()
    t.join()
    assert out["seq"] > 0      # not -1: the write really happened
    assert out["hits"] == 1    # and is searchable


def test_concurrent_writers_lose_nothing(idx: SessionEventIndex) -> None:
    # The instance lock serialises writes, so N threads x M events all land.
    threads = 4
    per_thread = 25

    def writer(n: int) -> None:
        for i in range(per_thread):
            idx.record("s1", "tool_result", f"thread {n} event {i}")

    ts = [threading.Thread(target=writer, args=(n,)) for n in range(threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert idx.stats("s1")["events"] == threads * per_thread


def test_open_sets_a_busy_timeout() -> None:
    # An in-process lock orders threads, never a second PROCESS on the same file (the CLI
    # writing while the daemon writes). Without a busy timeout SQLite gives up on the first
    # blip and a write the wait would have completed is dropped. Guarded repo-wide by
    # tests/quality/test_sqlite_busy_timeout.py.
    ix = SessionEventIndex.open(":memory:")
    (timeout_ms,) = ix._conn.execute("PRAGMA busy_timeout").fetchone()
    assert timeout_ms == 5000
    ix.close()


def test_close_is_idempotent_and_never_raises() -> None:
    ix = SessionEventIndex.open(":memory:")
    ix.record("s1", "note", "x")
    ix.close()
    ix.close()  # second close must be a no-op, not an error
    # post-close operations degrade rather than raise
    assert ix.record("s1", "note", "after close") == -1
    assert ix.search("s1", "x") == []


def test_context_manager_closes_deterministically() -> None:
    with SessionEventIndex.open(":memory:") as ix:
        ix.record("s1", "note", "inside the block")
        assert len(ix.search("s1", "inside")) == 1
    # exited -> connection closed -> ops degrade
    assert ix.record("s1", "note", "after") == -1


def test_context_manager_closes_even_when_the_block_raises() -> None:
    ix = SessionEventIndex.open(":memory:")
    with pytest.raises(RuntimeError):
        with ix:
            ix.record("s1", "note", "before the raise")
            raise RuntimeError("boom")
    assert ix.record("s1", "note", "after") == -1  # closed despite the exception


def test_non_db_errors_still_surface(idx: SessionEventIndex) -> None:
    # _best_effort catches only sqlite3.Error — a genuine misuse (non-str query) must still
    # blow up loudly rather than be hidden as "no results".
    with pytest.raises((AttributeError, TypeError)):
        idx.search("s1", 123)  # type: ignore[arg-type]


# --- the shared accessor -------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_shared_index(tmp_path, monkeypatch):
    """Point the accessor at a temp data dir and drop the singleton around every test.

    The singleton is process-global, which is the leak shape navig's conftest already
    contains for the approval gate: without this, one test's index (and its file handle)
    outlives it and the next test silently reads the wrong database.
    """
    monkeypatch.setenv("NAVIG_DATA_DIR", str(tmp_path / "data"))
    reset_session_index()
    yield
    reset_session_index()


def test_accessor_returns_a_usable_index() -> None:
    ix = get_session_index()
    assert ix is not None
    assert ix.record("s1", "note", "via the accessor") > 0
    assert len(ix.search("s1", "accessor")) == 1


def test_accessor_is_a_singleton() -> None:
    assert get_session_index() is get_session_index()


def test_accessor_honours_navig_data_dir(tmp_path) -> None:
    ix = get_session_index()
    assert ix is not None
    ix.record("s1", "note", "x")
    db = tmp_path / "data" / "session_index.db"
    assert db.exists()  # written where data_dir() says, not into the operator's real dir


def test_records_persist_across_accessor_calls() -> None:
    first = get_session_index()
    assert first is not None
    first.record("s1", "note", "durable across calls")
    again = get_session_index()
    assert again is not None
    assert len(again.search("s1", "durable")) == 1


def test_reset_drops_the_instance_but_not_the_data() -> None:
    first = get_session_index()
    assert first is not None
    first.record("s1", "note", "written before the reset")

    reset_session_index()
    second = get_session_index()
    assert second is not None
    assert second is not first  # a genuinely new instance…
    # …reopened on the same file: reset closes the connection, it does not delete data.
    assert len(second.search("s1", "before")) == 1


def test_accessor_returns_none_without_fts5(monkeypatch) -> None:
    # A SQLite build without FTS5 must degrade to "no searchable memory", not raise.
    monkeypatch.setattr("navig.memory.session_index.fts5_available", lambda *a, **k: False)
    assert get_session_index() is None


def test_accessor_returns_none_when_the_data_dir_is_unusable(monkeypatch) -> None:
    def _boom(*_a: object, **_k: object) -> None:
        raise OSError("read-only filesystem")

    monkeypatch.setattr("pathlib.Path.mkdir", _boom)
    assert get_session_index() is None


# --- agent-loop wiring (record_event / recover_context) ------------------------


@pytest.fixture
def _enabled(monkeypatch):
    """Turn the feature on without touching the operator's config."""
    monkeypatch.setattr(
        "navig.memory.session_index.session_index_enabled", lambda: True
    )


def test_disabled_by_default_records_nothing(monkeypatch) -> None:
    # The flag is off unless an operator opts in: record_event must not even create a DB.
    monkeypatch.setattr(
        "navig.memory.session_index.session_index_enabled", lambda: False
    )
    record_event("s1", "tool_result", "should not be stored")
    ix = get_session_index()
    assert ix is not None
    assert ix.stats("s1")["events"] == 0


def test_record_event_stores_when_enabled(_enabled) -> None:
    record_event("s1", "tool_result", "ssh connected to prod-web-01")
    ix = get_session_index()
    assert ix is not None
    assert len(ix.search("s1", "prod-web-01")) == 1


def test_record_event_ignores_empty_text(_enabled) -> None:
    record_event("s1", "tool_result", "")
    ix = get_session_index()
    assert ix is not None
    assert ix.stats("s1")["events"] == 0


def test_record_event_truncates_a_huge_result(_enabled) -> None:
    record_event("s1", "tool_result", "x" * 100_000)
    ix = get_session_index()
    assert ix is not None
    (event,) = ix.recent("s1")
    assert len(event.text) == 4000  # _MAX_RECORD_CHARS — the DB cannot grow unbounded


def test_recover_context_returns_relevant_events(_enabled) -> None:
    record_event("s1", "tool_result", "deployed the api worker to cloudflare")
    record_event("s1", "tool_result", "unrelated chatter about lunch")
    messages = [
        {"role": "user", "content": "what happened with the cloudflare deploy?"},
        {"role": "assistant", "content": "..."},
    ]
    block = recover_context("s1", messages)
    assert "cloudflare" in block
    assert "lunch" not in block


def test_recover_context_is_blank_when_disabled(monkeypatch) -> None:
    monkeypatch.setattr(
        "navig.memory.session_index.session_index_enabled", lambda: False
    )
    assert recover_context("s1", [{"role": "user", "content": "anything"}]) == ""


def test_recover_context_uses_the_NEWEST_user_message(_enabled) -> None:
    record_event("s1", "tool_result", "postgres backup finished")
    record_event("s1", "tool_result", "nginx reloaded")
    messages = [
        {"role": "user", "content": "check the postgres backup"},
        {"role": "assistant", "content": "..."},
        {"role": "user", "content": "now what about nginx"},
    ]
    block = recover_context("s1", messages)
    assert "nginx" in block
    assert "postgres" not in block  # answered the current ask, not a stale one


def test_recover_context_without_a_user_message_is_blank(_enabled) -> None:
    record_event("s1", "tool_result", "something")
    assert recover_context("s1", [{"role": "assistant", "content": "hi"}]) == ""


def test_recover_context_survives_malformed_messages(_enabled) -> None:
    record_event("s1", "tool_result", "widget rebuilt")
    messages = [None, 42, {"no_role": True}, {"role": "user", "content": "widget"}]
    assert "widget" in recover_context("s1", messages)


def test_recover_context_accepts_objects_not_just_dicts(_enabled) -> None:
    class _Msg:
        def __init__(self, role: str, content: str) -> None:
            self.role, self.content = role, content

    record_event("s1", "tool_result", "cache purged")
    assert "cache" in recover_context("s1", [_Msg("user", "cache")])


def test_flag_reads_config_through_coerce_bool(monkeypatch) -> None:
    # `navig config set … false` stores the STRING "false", which is truthy.
    class _CM:
        @staticmethod
        def get(key: str, default: object = None) -> object:
            return "false" if key == "memory.session_index.enabled" else default

    monkeypatch.setattr("navig.config.get_config_manager", lambda *a, **k: _CM())
    assert session_index_enabled() is False


def test_flag_is_off_when_config_is_unreadable(monkeypatch) -> None:
    def _boom(*_a: object, **_k: object) -> None:
        raise RuntimeError("config unreadable")

    monkeypatch.setattr("navig.config.get_config_manager", _boom)
    assert session_index_enabled() is False  # never fail OPEN into a behaviour change


# --- retention: the file must not grow forever -----------------------------------


def test_purge_older_than_deletes_only_old_events(idx: SessionEventIndex) -> None:
    import time as _t

    now = _t.time()
    idx.record("s1", "note", "ancient", ts=now - 60 * 86400)
    idx.record("s1", "note", "recent", ts=now - 1 * 86400)
    idx.record("s2", "note", "ancient sibling", ts=now - 60 * 86400)

    removed = idx.purge_older_than(days=30)
    assert removed == 2  # both old ones, across BOTH sessions — this bounds the file
    assert idx.stats()["events"] == 1
    assert idx.recent("s1")[0].text == "recent"


def test_purge_older_than_zero_days_is_disabled(idx: SessionEventIndex) -> None:
    # 0 must mean "keep everything", not "delete everything" — the difference between a
    # disabled knob and data loss.
    idx.record("s1", "note", "keep me", ts=0.0)  # epoch: older than any cutoff
    assert idx.purge_older_than(days=0) == 0
    assert idx.stats()["events"] == 1


def test_purge_older_than_never_raises_on_a_dead_connection() -> None:
    import sqlite3 as _sq

    conn = _sq.connect(":memory:")
    ix = SessionEventIndex(conn)
    conn.close()
    assert ix.purge_older_than(days=1) == 0


def test_record_event_caps_a_runaway_session(monkeypatch) -> None:
    """The amortised prune actually fires and bounds one session."""
    from navig.memory import session_index as si

    monkeypatch.setattr(si, "session_index_enabled", lambda: True)
    monkeypatch.setattr(si, "_PRUNE_EVERY", 10)
    monkeypatch.setattr(si, "_int_setting", lambda key, default: 25)

    for i in range(60):
        si.record_event("s1", "tool_result", f"event {i}")

    index = get_session_index()
    assert index is not None
    # Bounded, not unbounded: far below the 60 recorded, and it kept the NEWEST.
    count = index.stats("s1")["events"]
    assert count <= 35, f"session grew to {count} — the cap never fired"
    assert "event 59" in index.recent("s1")[0].text


def test_a_zero_cap_disables_pruning(monkeypatch) -> None:
    from navig.memory import session_index as si

    monkeypatch.setattr(si, "session_index_enabled", lambda: True)
    monkeypatch.setattr(si, "_PRUNE_EVERY", 5)
    monkeypatch.setattr(si, "_int_setting", lambda key, default: 0)

    for i in range(20):
        si.record_event("s1", "tool_result", f"event {i}")

    index = get_session_index()
    assert index is not None
    assert index.stats("s1")["events"] == 20  # nothing pruned


def test_int_setting_survives_a_garbage_value(monkeypatch) -> None:
    from navig.memory import session_index as si

    class _CM:
        @staticmethod
        def get(key: str, default: object = None) -> object:
            return "not-a-number"

    monkeypatch.setattr("navig.config.get_config_manager", lambda *a, **k: _CM())
    assert si._int_setting("whatever", 2000) == 2000  # falls back, never raises


def test_int_setting_reads_a_string_from_config(monkeypatch) -> None:
    # `navig config set ... 500` stores the STRING "500".
    from navig.memory import session_index as si

    class _CM:
        @staticmethod
        def get(key: str, default: object = None) -> object:
            return "500"

    monkeypatch.setattr("navig.config.get_config_manager", lambda *a, **k: _CM())
    assert si._int_setting("whatever", 2000) == 500


# --- event kinds: a read and an action read differently in a recovered block -------


def test_event_kind_marks_a_read_vs_an_action() -> None:
    from navig.agent.speculative import READ_ONLY_TOOLS
    from navig.memory.session_index import event_kind_for_tool

    a_read = next(iter(READ_ONLY_TOOLS))
    assert event_kind_for_tool(a_read) == "tool_result"
    assert event_kind_for_tool("write_file") == "action"


def test_unknown_tool_is_treated_as_an_action() -> None:
    # Fail-closed, like the rest of navig: not-known-to-be-a-read is an action.
    from navig.memory.session_index import event_kind_for_tool

    assert event_kind_for_tool("some_plugin_tool_we_never_saw") == "action"


def test_event_kind_never_raises(monkeypatch) -> None:
    from navig.memory import session_index as si

    monkeypatch.setitem(__import__("sys").modules, "navig.agent.speculative", None)
    assert si.event_kind_for_tool("write_file") in {"tool_result", "action"}


def test_the_kind_shows_up_in_a_recovered_block(idx: SessionEventIndex) -> None:
    idx.record("s1", "action", "wrote core/navig/thing.py")
    block = idx.search_and_render("s1", "wrote thing")
    assert "[action]" in block  # the model can tell a change from a look
def test_recover_context_is_blank_when_no_user_message_survives(_enabled) -> None:
    """A compacted list can have NO user turn left — then there is nothing to search for.

    This is the shape that made the feature inert in practice: `_latest_user_text` returns
    "" and recovery bails before touching the index. Pinning it here documents *why* the
    caller must pass the pre-compaction messages (see the sibling test below) rather than
    leaving the blank result looking like "nothing matched".
    """
    record_event("s1", "tool_result", "nginx reloaded on prod-web-01")
    compacted = [
        {"role": "system", "content": "[Context Summary - 12 messages compressed]"},
        {"role": "assistant", "content": "..."},
    ]
    assert recover_context("s1", compacted) == ""


def test_recover_context_never_raises_even_when_the_index_does(_enabled, monkeypatch) -> None:
    """The docstring promises "anything at all goes wrong" -> "". It must be true.

    It was not: there was no try/except, so a raising index propagated into the agent
    loop's broad handler, which logs "Context compression skipped" — a message about a
    step that had already succeeded.
    """

    class _Exploding:
        def search_and_render(self, *_a: object, **_k: object) -> str:
            raise RuntimeError("index exploded")

    monkeypatch.setattr(
        "navig.memory.session_index.get_session_index", lambda: _Exploding()
    )
    messages = [{"role": "user", "content": "what happened with nginx?"}]
    assert recover_context("s1", messages) == ""


# --- CJK: a 2-character query must find text inside an unspaced run ---------------


def test_two_character_cjk_query_now_matches(idx: SessionEventIndex) -> None:
    # The whole point: unicode61 makes "中文文档" ONE token, so "中文" could never match
    # it. SQLite's segmenting tokenizer is unavailable on the build navig ships
    # (tokenize='icu' -> "no such tokenizer"), so the bigrams are added on the write side.
    idx.record("s1", "note", "中文文档 已更新")
    assert len(idx.search("s1", "中文")) == 1
    assert len(idx.search("s1", "文档")) == 1
    assert len(idx.search("s1", "中文文档")) == 1  # the full token still works


def test_cjk_expansion_does_not_leak_into_the_rendered_block(idx: SessionEventIndex) -> None:
    # `search` carries the bigrams; `text` is what a human/model reads back.
    idx.record("s1", "note", "中文文档 已更新")
    (event,) = idx.recent("s1")
    assert event.text == "中文文档 已更新"
    assert "## Recovered context" in idx.search_and_render("s1", "中文")


def test_ascii_is_untouched_by_the_expansion() -> None:
    from navig.memory.session_index import _expand_for_search

    assert _expand_for_search("deployed the api worker") == "deployed the api worker"


def test_cjk_search_has_no_false_positives(idx: SessionEventIndex) -> None:
    idx.record("s1", "note", "中文文档")
    assert idx.search("s1", "韓國語") == []


def test_an_old_schema_is_migrated_and_keeps_its_rows(tmp_path) -> None:
    """A database written before the search column upgrades instead of silently failing."""
    import sqlite3 as _sq

    db = str(tmp_path / "old.db")
    old = _sq.connect(db)
    old.execute(
        "CREATE VIRTUAL TABLE session_events USING fts5("
        "  session_id UNINDEXED, kind UNINDEXED, ts UNINDEXED, text,"
        "  tokenize = 'porter unicode61')"
    )
    old.execute(
        "INSERT INTO session_events(session_id, kind, ts, text) VALUES (?,?,?,?)",
        ("s1", "note", 1.0, "中文文档 legacy row"),
    )
    old.commit()
    old.close()

    ix = SessionEventIndex.open(db)          # ensure_schema runs the migration
    cols = [r[1] for r in ix._conn.execute("PRAGMA table_info(session_events)").fetchall()]
    assert "search" in cols
    assert ix.stats("s1")["events"] == 1                      # the row survived …
    assert len(ix.search("s1", "legacy")) == 1
    assert len(ix.search("s1", "中文")) == 1                   # … and gained CJK search
    ix.close()


# --- the third bound: total events across ALL sessions ---------------------------


def test_cap_total_keeps_the_newest_across_sessions(idx: SessionEventIndex) -> None:
    for i in range(30):
        idx.record(f"s{i % 6}", "note", f"event {i}")
    removed = idx.cap_total(keep=10)
    assert removed == 20
    assert idx.stats()["events"] == 10
    assert "event 29" in idx.recent("s5")[0].text


def test_cap_total_zero_is_disabled(idx: SessionEventIndex) -> None:
    idx.record("s1", "note", "keep")
    assert idx.cap_total(keep=0) == 0
    assert idx.stats()["events"] == 1


def test_record_event_applies_the_global_cap(monkeypatch) -> None:
    from navig.memory import session_index as si

    monkeypatch.setattr(si, "session_index_enabled", lambda: True)
    monkeypatch.setattr(si, "_PRUNE_EVERY", 10)
    monkeypatch.setattr(
        si, "_int_setting",
        lambda key, default: 0 if key == si._MAX_EVENTS_KEY else (15 if key == si._MAX_TOTAL_KEY else default),
    )
    for i in range(40):
        si.record_event(f"s{i % 8}", "tool_result", f"event {i}")

    index = get_session_index()
    assert index is not None
    # Many small sessions: neither the per-session cap nor age would bite. This does.
    assert index.stats()["events"] <= 25


# --- single-character CJK, and re-expansion when the algorithm changes -----------


def test_single_character_cjk_query_matches(idx: SessionEventIndex) -> None:
    # Many Chinese words are one character (人 水 火), so stopping at bigrams left the
    # shortest real query unanswerable.
    idx.record("s1", "note", "中文文档 已更新")
    assert len(idx.search("s1", "文")) == 1
    assert len(idx.search("s1", "中")) == 1
    assert len(idx.search("s1", "中文")) == 1      # bigram still works
    assert len(idx.search("s1", "中文文档")) == 1   # full token still works


def test_single_character_cjk_has_no_false_positives(idx: SessionEventIndex) -> None:
    idx.record("s1", "note", "中文文档")
    assert idx.search("s1", "韓") == []


def test_expansion_emits_unigrams_and_bigrams() -> None:
    from navig.memory.session_index import _expand_for_search

    out = _expand_for_search("中文档").split()
    assert "中" in out and "文" in out and "档" in out      # unigrams
    assert "中文" in out and "文档" in out                   # bigrams
    assert out[0] == "中文档"                                # original first


def test_a_v1_index_is_re_expanded_and_gains_single_char_search(tmp_path) -> None:
    """The point of the version stamp: an index expanded by an older navig is rebuilt.

    Without it, `search` exists so the old migration would return early, and the index
    would keep matching only what v1 could — silently, forever.
    """
    import sqlite3 as _sq

    from navig.memory.session_index import _CJK_RUN, _EXPANSION_VERSION, _SCHEMA

    db = str(tmp_path / "v1.db")
    con = _sq.connect(db)
    con.execute(_SCHEMA)
    # v1 expansion: bigrams only
    text = "中文文档 已更新"
    bigrams = []
    for run in _CJK_RUN.findall(text):
        bigrams.extend(run[i : i + 2] for i in range(len(run) - 1))
    con.execute(
        "INSERT INTO session_events(session_id, kind, ts, text, search) VALUES (?,?,?,?,?)",
        ("s1", "note", 1.0, text, f"{text} {' '.join(bigrams)}"),
    )
    con.execute("CREATE TABLE session_index_meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO session_index_meta VALUES ('expansion_version','1')")
    con.commit()
    con.close()

    ix = SessionEventIndex.open(db)
    assert ix.stats("s1")["events"] == 1                 # the row survived the rebuild
    assert len(ix.search("s1", "文")) == 1                # …and gained 1-char search
    stamped = ix._conn.execute(
        "SELECT value FROM session_index_meta WHERE key='expansion_version'"
    ).fetchone()[0]
    assert int(stamped) == _EXPANSION_VERSION            # and is marked current
    ix.close()


def test_a_fresh_index_is_stamped_without_a_rebuild(tmp_path) -> None:
    from navig.memory.session_index import _EXPANSION_VERSION

    ix = SessionEventIndex.open(str(tmp_path / "fresh.db"))
    stamped = ix._conn.execute(
        "SELECT value FROM session_index_meta WHERE key='expansion_version'"
    ).fetchone()
    assert stamped is not None and int(stamped[0]) == _EXPANSION_VERSION
    ix.record("s1", "note", "中文文档")
    assert len(ix.search("s1", "文")) == 1
    ix.close()
