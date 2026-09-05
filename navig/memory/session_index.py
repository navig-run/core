"""navig.memory.session_index — an FTS5-backed, per-session event index.

The gap this closes (see ``.lab/gh-shortlist/NAVIG-INTEGRATION.md`` Brief 2): every MCP
tool call and file read dumps raw text into the model's context, and on compaction the
working set is lost. navig already *captures* facts (``agent.memory_auto_extractor`` /
``memory.fact_extractor``); what it lacks is a **searchable event log** that keeps raw
output *out of* context and hands back only the relevant slice on demand.

``SessionEventIndex`` is that log: append every event (a tool result, a file edit, a user
decision) and later ``search`` it with a natural-language query, ranked by SQLite's built-in
**BM25**. One session's events are never visible to another (``session_id`` scopes every
read), and ``clear`` gives context-mode's "a fresh session is a clean slate".

Design notes — deliberately a **standalone** FTS5 table (no external-content, no triggers).
navig's own history (`.lab` memory ``navig-links-fts-corruption``) records that
external-content FTS5 + hand-written triggers produced ``database disk image is malformed``.
A plain FTS5 table written by direct ``INSERT`` has no such failure mode: ``DELETE`` and
rebuild are first-class, and there is no shadow content table to drift out of sync.

Using it — ask for the shared instance, never build a path yourself::

    from navig.memory.session_index import get_session_index

    ix = get_session_index()
    if ix is not None:                       # None on a SQLite build without FTS5
        ix.record(session_id, "tool_result", text)
        recovered = ix.search_and_render(session_id, last_user_query)  # "" when nothing fits

The index lives at ``data_dir()/session_index.db`` and the accessor owns that choice, so two
callers cannot end up writing to two different files. The agent loop records every tool result
and re-injects the relevant ones after a compaction, both gated behind
``memory.session_index.enabled`` (read via ``navig.core.coerce.coerce_bool`` — config stores raw
strings, so a bare ``if`` reads ``"false"`` as True), off by default.
"""

from __future__ import annotations

import logging
import pathlib
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable, TypeVar

__all__ = [
    "SessionEvent",
    "SessionEventIndex",
    "fts5_available",
    "render_recovery",
    "get_session_index",
    "reset_session_index",
    "session_index_enabled",
    "record_event",
    "recover_context",
    "event_kind_for_tool",
]

logger = logging.getLogger(__name__)
_T = TypeVar("_T")

# Only word characters survive into a MATCH query. This is what makes an arbitrary,
# possibly-adversarial query string safe: FTS5 operators (``NEAR``, ``OR``, ``*``, quotes,
# parens, a stray ``"``) are not word characters, so they can never reach the parser and
# turn a user's search box into an injection point. Empty result → no query at all.
#
# ``\w`` (Unicode-aware in Python 3) rather than ASCII-only ``[0-9A-Za-z_]``: events and
# queries can be in any language — a ``café`` or ``中文`` query must still match what the
# ``unicode61`` tokenizer stored. None of ``\w``'s characters are FTS5-special, so wrapping
# each token in quotes stays injection-safe.
_WORD = re.compile(r"\w+")

# An index entry is a pointer back into history, not a transcript — collapse internal
# whitespace so one event is one line in the recovered-context block.
_COLLAPSE = re.compile(r"\s+")

#: A single recovered event never occupies more than this many characters on its line, so
#: one giant tool dump cannot swallow the whole recovery budget.
_MAX_EVENT_LINE = 200

# Columns other than ``text`` are stored-but-not-tokenised: they round-trip and can be
# filtered in WHERE, but they never dilute the full-text index or the BM25 score.
#: ``text`` is what a recovered block shows; ``search`` is what FTS5 matches on. They
#: differ only for CJK, where ``search`` also carries the single characters and 2-character
#: windows that make a short query findable (see :func:`_expand_for_search`). Keeping them
#: apart is what lets the index be searchable without that noise reaching the rendered block.
_SCHEMA = (
    "CREATE VIRTUAL TABLE IF NOT EXISTS session_events USING fts5("
    "  session_id UNINDEXED, kind UNINDEXED, ts UNINDEXED, text UNINDEXED, search,"
    "  tokenize = 'porter unicode61'"
    ")"
)

#: Runs of 2+ characters in scripts that are written without spaces. ``unicode61`` makes
#: one token of such a run, so "中文" cannot match inside "中文文档" — and SQLite's
#: segmenting tokenizer is not an option: ``tokenize='icu'`` raises "no such tokenizer"
#: on the stock build navig ships (verified; no ICU in PRAGMA compile_options).
_CJK_RUN = re.compile(
    r"[぀-ヿ㐀-䶿一-鿿豈-﫿가-힯]{2,}"
)


#: Bumped whenever :func:`_expand_for_search` changes what it emits. It is stored beside
#: the data, so an index expanded by an older navig is REBUILT rather than left quietly
#: unsearchable — the same failure the `search` column was added to fix, one level up.
#: 1 = bigrams only · 2 = unigrams + bigrams.
_EXPANSION_VERSION = 2


def _expand_for_search(text: str) -> str:
    """*text* plus, for each CJK run, every single character and every 2-character window.

    Symmetric: applied to what is stored AND to what is queried. ``unicode61`` makes one
    token of an unspaced run, so without this neither "文" nor "中文" can match inside
    "中文文档" — and a segmenting tokenizer is not available (``tokenize='icu'`` raises
    "no such tokenizer" on the build navig ships; verified against PRAGMA compile_options).

    Single characters matter on their own here: in Chinese many are words (人, 水, 火), so
    stopping at bigrams would leave the shortest real query unanswerable.

    ASCII is returned unchanged and pays nothing — the expansion only fires on a CJK run
    (measured: an ASCII row is byte-identical and its index is 48 pages / 2,000 rows; a CJK
    row expands ~5x and costs 141, which is simply the price of being searchable at all,
    and the retention bounds keep it finite).
    """
    extra: list[str] = []
    for run in _CJK_RUN.findall(text):
        extra.extend(run)                                        # unigrams
        extra.extend(run[i : i + 2] for i in range(len(run) - 1))  # bigrams
    return f"{text} {' '.join(extra)}" if extra else text


@dataclass(frozen=True)
class SessionEvent:
    """One recorded event. ``seq`` is the monotonic insertion id (the FTS5 rowid)."""

    seq: int
    session_id: str
    kind: str
    ts: float
    text: str


def fts5_available(conn: sqlite3.Connection | None = None) -> bool:
    """Whether this SQLite build has the FTS5 extension compiled in.

    Callers that must degrade gracefully (rather than raise on ``ensure_schema``) can probe
    first. A throwaway connection is used when none is supplied.
    """
    own = conn is None
    c = conn or sqlite3.connect(":memory:")
    try:
        c.execute("CREATE VIRTUAL TABLE _fts5_probe USING fts5(x)")
        c.execute("DROP TABLE _fts5_probe")
        return True
    except sqlite3.OperationalError:
        return False
    finally:
        if own:
            c.close()


def _match_query(text: str) -> str | None:
    """Turn arbitrary user text into a safe FTS5 MATCH string, or ``None`` for no terms.

    Each surviving word becomes a quoted phrase and the phrases are OR-ed, so a multi-word
    query recalls any event mentioning *any* of the words (BM25 then ranks the ones that
    mention more of them higher). Quoting neutralises even a word that happens to equal an
    FTS5 keyword like ``or`` / ``near``.

    Works for every space-delimited script (Latin + diacritics, Cyrillic, Greek, …). Scripts
    with no word boundaries (CJK) are one token per unbroken run under the ``unicode61``
    tokenizer, so a full-token query matches but a substring one does not.

    ``tokenize='trigram'`` was measured as the alternative and deliberately NOT adopted:
    it fixes 3+ character CJK substrings (1 hit vs 0) but leaves 2-character queries
    failing either way, **doubles the index** (119 vs 58 pages over the same 2,000 rows),
    and turns every search into substring matching — which costs precision on the dominant
    case (English infra text), and precision is the whole point of a block that gets
    injected back into a model's context. ``CREATE TABLE IF NOT EXISTS`` would also leave
    every existing database silently on the old tokenizer, so the switch would need a
    rebuild-migration to be honest. Full-token CJK search works today; CJK *substring*
    search is a measured limitation, not an oversight.
    """
    tokens = _WORD.findall(_expand_for_search(text).lower())
    if not tokens:
        return None
    return " OR ".join(f'"{tok}"' for tok in tokens)


def render_recovery(events: list[SessionEvent], *, max_chars: int = 2000) -> str:
    """Format recovered events into a compact, size-capped block for re-injection.

    The other half of the compaction use case: :meth:`SessionEventIndex.search` finds the
    relevant events, this turns them into a bounded block the agent can be handed back after
    its context was trimmed. Returns ``""`` when there is nothing to recover, so a caller can
    inject unconditionally without special-casing the empty result.

    Events are taken in the order given (BM25-best first from ``search``) until *max_chars*
    would be exceeded; the rest collapse into a ``(+N more)`` line. The first event is always
    shown even if it alone is large, so the block is never empty when there is something to say.
    """
    if not events or max_chars <= 0:
        return ""
    header = "## Recovered context\n"
    lines: list[str] = []
    used = len(header)
    for i, ev in enumerate(events):
        text = _COLLAPSE.sub(" ", ev.text).strip()
        line = f"- [{ev.kind}] {text}"
        if len(line) > _MAX_EVENT_LINE:
            line = line[: _MAX_EVENT_LINE - 1] + "…"
        # Always include at least one event; after that, stop before overrunning the budget.
        if lines and used + len(line) + 1 > max_chars:
            lines.append(f"- …(+{len(events) - i} more)")
            break
        lines.append(line)
        used += len(line) + 1
    return header + "\n".join(lines) + "\n"


class SessionEventIndex:
    """A searchable, per-session event log backed by one standalone FTS5 table.

    **Safe to share across threads.** navig's daemon is multi-threaded, and Python's
    ``sqlite3`` refuses cross-thread use of a connection by default
    (``check_same_thread=True``) — which raises ``ProgrammingError``, an ``sqlite3.Error``,
    which the best-effort guard below would swallow. The failure mode that produces is the
    worst one available: an index that looks wired, logs a warning nobody reads, and silently
    records nothing. So :meth:`open` builds the connection with ``check_same_thread=False``
    (sound because ``sqlite3.threadsafety == 3``, i.e. SQLite itself serialises), and every
    operation is additionally serialised by an instance lock, so one shared instance behaves
    correctly no matter which thread calls it. **A connection you inject must likewise be
    created with ``check_same_thread=False``** if it will be used from more than one thread.

    **Per-operation calls are best-effort.** Memory is an augmentation, never the source of
    truth, so a transient ``database is locked`` (routine on the Windows/AV-prone deployments
    navig's own history documents) must not crash the turn it happened to land in — the same
    contract ``agent.memory_auto_extractor`` states as "errors never interrupt the
    conversation". A failed read degrades to its empty value (`[]` / `""` / `0`) and a failed
    ``record`` returns ``-1``; both log at WARNING so the failure is visible, never silent.
    ``ensure_schema`` is the one exception — it runs at construction and raises, because an
    index that cannot create its table is a setup error the caller must see, not a transient miss.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._lock = threading.Lock()
        self.ensure_schema()

    # -- construction ---------------------------------------------------------

    @classmethod
    def open(cls, path: str = ":memory:") -> "SessionEventIndex":
        """Open (or create) an index at *path*. ``:memory:`` for an ephemeral one.

        ``check_same_thread=False`` because the daemon shares one instance across threads;
        the instance lock (not the DB-API's thread check) is what keeps that safe.

        ``busy_timeout`` is the *other half* of that, and the lock cannot substitute for it: an
        in-process lock orders this process's threads, while the CLI writing to the same file
        while the daemon writes — or an antivirus/backup agent holding it for a moment — is a
        second **process**. Without the timeout SQLite gives up on the first blip
        (``database is locked``) and the best-effort path would drop a write that a short wait
        would have completed. 5000 ms is the canonical NAVIG default
        (``storage/pragma_profiles.py``, ``store/base.py``).
        """
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.execute("PRAGMA busy_timeout=5000")
        return cls(conn)

    def ensure_schema(self) -> None:
        """Create the FTS5 table if absent, and upgrade one written before ``search``.

        Idempotent; safe to call on every open. The upgrade matters because
        ``CREATE TABLE IF NOT EXISTS`` is silent about a table whose *columns* have
        changed: an index written by an older navig would keep working and quietly never
        match a CJK query, which is the failure mode this whole column exists to fix.
        """
        self._conn.execute(_SCHEMA)
        self._conn.commit()
        self._migrate_search_column()

    def _migrate_search_column(self) -> None:
        """Rebuild the table when its stored expansion is older than this navig.

        Two things go stale and both are silent: a table written before the ``search``
        column existed (no CJK match at all), and one expanded by an older
        :data:`_EXPANSION_VERSION` (matches what that version could, and nothing newer).
        Rebuild rather than drop: these events are an augmentation, but deleting a user's
        history to change an index is not a trade this module makes silently. FTS5 has no
        ALTER ADD COLUMN, so the honest shape is copy -> drop -> recreate -> reinsert.
        """

        def _do() -> None:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS session_index_meta "
                "(key TEXT PRIMARY KEY, value TEXT)"
            )
            cols = [
                r[1]
                for r in self._conn.execute("PRAGMA table_info(session_events)").fetchall()
            ]
            if not cols:
                return
            row = self._conn.execute(
                "SELECT value FROM session_index_meta WHERE key = 'expansion_version'"
            ).fetchone()
            stored = int(row[0]) if row and str(row[0]).isdigit() else (1 if "search" in cols else 0)
            if stored >= _EXPANSION_VERSION and "search" in cols:
                return  # current

            if "search" in cols and not self._conn.execute(
                "SELECT 1 FROM session_events LIMIT 1"
            ).fetchone():
                # A fresh (or emptied) index has nothing to re-expand — stamp it and skip
                # the drop/recreate, so opening a new database costs one INSERT.
                self._conn.execute(
                    "INSERT OR REPLACE INTO session_index_meta(key, value) VALUES "
                    "('expansion_version', ?)",
                    (str(_EXPANSION_VERSION),),
                )
                self._conn.commit()
                return

            logger.info(
                "session index: re-expanding search text (v%s -> v%s)",
                stored,
                _EXPANSION_VERSION,
            )
            rows = self._conn.execute(
                "SELECT session_id, kind, ts, text FROM session_events"
            ).fetchall()
            self._conn.execute("DROP TABLE session_events")
            self._conn.execute(_SCHEMA)
            self._conn.executemany(
                "INSERT INTO session_events(session_id, kind, ts, text, search) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (sid, kind, ts, txt, _expand_for_search(txt or ""))
                    for sid, kind, ts, txt in rows
                ],
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO session_index_meta(key, value) VALUES "
                "('expansion_version', ?)",
                (str(_EXPANSION_VERSION),),
            )
            self._conn.commit()

        self._best_effort("migrate_search_column", None, _do)

    def _best_effort(self, op: str, default: _T, thunk: Callable[[], _T]) -> _T:
        """Run *thunk* under the instance lock; on a SQLite error, log and return *default*.

        The single seam that makes the store both **thread-safe** (one writer at a time, so a
        shared instance is correct from any thread) and **non-fatal** to the conversation.
        Only ``sqlite3.Error`` is caught — a programming bug (wrong types, a bad SQL string)
        is not an ``sqlite3.Error`` and still surfaces loudly in tests.
        """
        try:
            with self._lock:
                return thunk()
        except sqlite3.Error as exc:
            logger.warning("SessionEventIndex.%s degraded to best-effort: %s", op, exc)
            return default

    # -- writes ---------------------------------------------------------------

    def record(self, session_id: str, kind: str, text: str, *, ts: float | None = None) -> int:
        """Append one event and return its ``seq`` (monotonic insertion id).

        ``kind`` is a free-form tag (``tool_result`` / ``file_edit`` / ``decision`` / …);
        it round-trips but is not searched, so callers can filter on it downstream. Returns
        ``-1`` (never raises) if the write is lost to a transient DB error — see the class note.
        """
        stamp = time.time() if ts is None else ts

        def _do() -> int:
            cur = self._conn.execute(
                "INSERT INTO session_events(session_id, kind, ts, text, search) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, kind, stamp, text, _expand_for_search(text)),
            )
            self._conn.commit()
            # ``lastrowid`` is ``int | None``; after a successful INSERT it is set, but guard
            # the None so a surprise can't raise a *TypeError* — which, not being a
            # ``sqlite3.Error``, would slip past ``_best_effort`` and crash the turn.
            rowid = cur.lastrowid
            return int(rowid) if rowid is not None else -1

        return self._best_effort("record", -1, _do)

    def clear(self, session_id: str) -> int:
        """Delete every event for *session_id*; return how many were removed.

        This is the "fresh session = clean slate" primitive — a plain DELETE, which on a
        standalone FTS5 table is exact and leaves no orphaned shadow rows. Returns ``0`` on a
        transient DB error rather than raising.
        """

        def _do() -> int:
            cur = self._conn.execute(
                "DELETE FROM session_events WHERE session_id = ?", (session_id,)
            )
            self._conn.commit()
            return int(cur.rowcount)

        return self._best_effort("clear", 0, _do)

    def prune(self, session_id: str, *, keep: int) -> int:
        """Keep only the newest *keep* events for *session_id*; delete the older ones.

        The bound that stops a long session's log from growing without limit. ``keep <= 0``
        clears the whole session (like :meth:`clear`). Returns how many rows were deleted, or
        ``0`` on a transient DB error (never raises). Scoped to *session_id* — a sibling
        session's events are never touched.
        """
        k = int(keep)

        def _do() -> int:
            if k <= 0:
                cur = self._conn.execute(
                    "DELETE FROM session_events WHERE session_id = ?", (session_id,)
                )
            else:
                # Delete every event for this session except the k highest rowids (= newest).
                cur = self._conn.execute(
                    "DELETE FROM session_events WHERE session_id = ? AND rowid NOT IN "
                    "(SELECT rowid FROM session_events WHERE session_id = ? "
                    "ORDER BY rowid DESC LIMIT ?)",
                    (session_id, session_id, k),
                )
            self._conn.commit()
            return int(cur.rowcount)

        return self._best_effort("prune", 0, _do)

    def purge_older_than(self, *, days: float) -> int:
        """Delete every event older than *days*, across all sessions. Returns the count.

        ``prune`` bounds ONE session; this bounds the file itself. Without it a
        long-lived install accumulates a session's worth of events every day forever,
        which is the growth an opt-in feature left on for months actually produces.
        ``days <= 0`` disables it (keep everything) rather than deleting the table.
        """
        if days <= 0:
            return 0
        cutoff = time.time() - days * 86400.0

        def _do() -> int:
            cur = self._conn.execute(
                "DELETE FROM session_events WHERE ts < ?", (cutoff,)
            )
            self._conn.commit()
            return int(cur.rowcount)

        return self._best_effort("purge_older_than", 0, _do)

    def cap_total(self, *, keep: int) -> int:
        """Keep only the newest *keep* events across ALL sessions. Returns the count deleted.

        The third bound, and the only one that holds when neither of the others bites: a
        machine that starts many short sessions inside the retention window grows without
        any single session being large or any event being old. ``keep <= 0`` disables it.
        """
        if keep <= 0:
            return 0

        def _do() -> int:
            cur = self._conn.execute(
                "DELETE FROM session_events WHERE rowid NOT IN "
                "(SELECT rowid FROM session_events ORDER BY rowid DESC LIMIT ?)",
                (keep,),
            )
            self._conn.commit()
            return int(cur.rowcount)

        return self._best_effort("cap_total", 0, _do)

    # -- reads ----------------------------------------------------------------

    def search(self, session_id: str, query: str, *, limit: int = 10) -> list[SessionEvent]:
        """BM25-ranked events for *session_id* matching *query* (best first).

        A blank / punctuation-only / no-word query returns ``[]`` rather than raising, and
        an adversarial query can never inject FTS5 syntax (see :func:`_match_query`). Reads
        are always scoped to *session_id*, so no session can see another's events.
        """
        match = _match_query(query)
        n = int(limit)
        # A blank/no-word query and a non-positive limit both mean "nothing was asked for".
        # Returning [] beats surprising the caller — and never emits ``LIMIT -1``, which
        # SQLite reads as "no limit / every row".
        if match is None or n <= 0:
            return []

        def _do() -> list[SessionEvent]:
            rows = self._conn.execute(
                "SELECT rowid, session_id, kind, ts, text FROM session_events "
                "WHERE session_events MATCH ? AND session_id = ? ORDER BY rank LIMIT ?",
                (match, session_id, n),
            ).fetchall()
            return [self._row(r) for r in rows]

        return self._best_effort("search", [], _do)

    def recent(self, session_id: str, *, limit: int = 20) -> list[SessionEvent]:
        """The newest events for *session_id*, most-recent first."""
        n = int(limit)
        if n <= 0:
            return []

        def _do() -> list[SessionEvent]:
            rows = self._conn.execute(
                "SELECT rowid, session_id, kind, ts, text FROM session_events "
                "WHERE session_id = ? ORDER BY rowid DESC LIMIT ?",
                (session_id, n),
            ).fetchall()
            return [self._row(r) for r in rows]

        return self._best_effort("recent", [], _do)

    def search_and_render(
        self, session_id: str, query: str, *, limit: int = 10, max_chars: int = 2000
    ) -> str:
        """``search`` then :func:`render_recovery` — the one call a compaction hook makes.

        Returns a bounded, re-injectable markdown block, or ``""`` when nothing relevant was
        found (so a compaction hook can call it unconditionally).
        """
        return render_recovery(self.search(session_id, query, limit=limit), max_chars=max_chars)

    def stats(self, session_id: str | None = None) -> dict[str, int]:
        """``{"events": N}`` for one session, or the whole index when *session_id* is None.

        Returns ``{"events": 0}`` on a transient DB error rather than raising.
        """

        def _do() -> dict[str, int]:
            if session_id is None:
                n = self._conn.execute("SELECT count(*) FROM session_events").fetchone()[0]
            else:
                n = self._conn.execute(
                    "SELECT count(*) FROM session_events WHERE session_id = ?", (session_id,)
                ).fetchone()[0]
            return {"events": int(n)}

        return self._best_effort("stats", {"events": 0}, _do)

    def close(self) -> None:
        """Close the underlying connection. Idempotent, and safe to call while other threads
        are using the index: taking the lock lets an in-flight operation finish rather than
        having its write silently dropped mid-statement. Operations attempted *after* the
        close degrade through the usual best-effort path.
        """
        self._best_effort("close", None, self._conn.close)

    # -- context manager ------------------------------------------------------

    def __enter__(self) -> "SessionEventIndex":
        return self

    def __exit__(self, *exc: object) -> None:
        """Always closes — deterministic cleanup even if the block raised."""
        self.close()

    # -- helpers --------------------------------------------------------------

    @staticmethod
    def _row(r: Iterable[Any]) -> SessionEvent:
        seq, session_id, kind, ts, text = r
        return SessionEvent(seq=int(seq), session_id=session_id, kind=kind, ts=float(ts), text=text)


# ---------------------------------------------------------------------------
# Process-wide accessor
# ---------------------------------------------------------------------------
#
# Consumers should never have to invent a database path — that is how two callers
# end up writing to two different files. This mirrors the accessor the sibling
# memory module exposes (``session_memory.get_session_extractor``): the module owns
# where its data lives, callers just ask for it.
#
# ONE instance per process rather than one per session: the table is keyed by
# ``session_id``, so a single index serves every session, and a single connection
# is what the instance lock is protecting.

_DB_NAME = "session_index.db"
_index: "SessionEventIndex | None" = None
_index_lock = threading.Lock()


def _index_path() -> "pathlib.Path":
    """``data_dir()/session_index.db`` — the canonical home for a navig database.

    ``data_dir()`` honours ``NAVIG_DATA_DIR``, which is how tests isolate a database
    without touching the operator's real one.
    """
    from navig.platform.paths import data_dir

    return data_dir() / _DB_NAME


def get_session_index() -> "SessionEventIndex | None":
    """The shared index, or ``None`` when it cannot be used on this install.

    ``None`` (rather than a raise) for a SQLite build without FTS5 or an unusable
    data dir, because searchable memory is an augmentation: a caller does
    ``ix = get_session_index()`` / ``if ix is not None`` and carries on when it is
    absent, exactly as it carries on when a search returns nothing.
    """
    global _index
    if _index is not None:
        return _index
    with _index_lock:
        if _index is not None:  # another thread won the race
            return _index
        try:
            path = _index_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            if not fts5_available():
                logger.warning(
                    "session index disabled: this SQLite build has no FTS5 "
                    "(searchable session memory will be skipped)"
                )
                return None
            _index = SessionEventIndex.open(str(path))
            # Once per process, at the moment the index is first opened: drop events old
            # enough that no compaction would ever recover them. Cheap (one indexless
            # DELETE on a bounded table) and best-effort, so a slow disk cannot delay the
            # first record.
            _index.purge_older_than(
                days=_int_setting(_RETENTION_DAYS_KEY, _DEFAULT_RETENTION_DAYS)
            )
        except (OSError, sqlite3.Error) as exc:
            logger.warning("session index unavailable (%s); continuing without it", exc)
            return None
    return _index


def reset_session_index() -> None:
    """Drop the cached instance (tests, and after a data-dir change).

    Closes the old connection first so a re-open does not leave a handle dangling —
    on Windows a leaked handle keeps the file locked for the next test.
    """
    global _index
    with _index_lock:
        if _index is not None:
            _index.close()
        _index = None


# ---------------------------------------------------------------------------
# Agent-loop wiring
# ---------------------------------------------------------------------------
#
# The agent loop should not carry policy. These three helpers hold all of it — the
# feature flag, the accessor, the query extraction, and the never-raise contract — so a
# call site is one line and everything below it is unit-testable without an agent.

#: Config toggle. Off by default: recording changes what the model is shown after a
#: compaction, which is a behaviour change an operator should opt into.
_ENABLED_KEY = "memory.session_index.enabled"

#: A recorded event is a pointer back into history, not a transcript. Truncating here
#: (rather than at render time) keeps the database from growing without bound on a
#: session that reads large files.
_MAX_RECORD_CHARS = 4000

# Retention. Truncating each event bounds one ROW; these bound the two ways the FILE
# grows: one session recording forever, and sessions accumulating month after month.
# Both are ints read the way navig's other memory modules read ints — config stores raw
# strings, so `int(...)` with a fallback rather than trusting the value.
_MAX_EVENTS_KEY = "memory.session_index.max_events"
_RETENTION_DAYS_KEY = "memory.session_index.retention_days"
_DEFAULT_MAX_EVENTS = 2000
_DEFAULT_RETENTION_DAYS = 30
_MAX_TOTAL_KEY = "memory.session_index.max_total_events"
_DEFAULT_MAX_TOTAL = 20000

#: Prune a session once every N recorded events rather than on every one — the cap is a
#: bound, not a precise ceiling, and a DELETE per insert would be pure overhead.
_PRUNE_EVERY = 100


def _int_setting(key: str, default: int) -> int:
    """An int from config, falling back to *default* on anything unusable.

    ``navig config set`` stores raw strings, so this is ``int("2000")`` in the normal
    case; a typo must not raise inside the agent loop, and a negative value is taken at
    face value (it means "disabled" for both knobs).
    """
    try:
        from navig.config import get_config_manager

        raw = get_config_manager().get(key, default)
        return int(raw if raw not in (None, "") else default)
    except Exception as exc:  # noqa: BLE001 — a bad setting must not break a turn
        logger.debug("session_index setting %s unreadable (%s); using %s", key, exc, default)
        return default


def session_index_enabled() -> bool:
    """Whether the agent loop should record into / recover from the index.

    Read through ``coerce_bool`` because ``navig config set`` stores raw strings — a bare
    ``if cfg.get(...)`` reads the operator's ``"false"`` as True.
    """
    try:
        from navig.config import get_config_manager
        from navig.core.coerce import coerce_bool

        return coerce_bool(get_config_manager().get(_ENABLED_KEY, False), default=False)
    except Exception as exc:  # noqa: BLE001 — an unreadable config must not enable a feature
        logger.debug("session_index flag unreadable (%s); treating as off", exc)
        return False


def event_kind_for_tool(tool_name: str) -> str:
    """The event kind to record a *tool_name* result under.

    Two kinds, because that is the distinction worth seeing in a recovered block: a read
    ("I looked at this") and an action ("I changed this"). An agent re-reading its own
    history after a compaction cares far more about the second.

    The split reuses ``agent.speculative.READ_ONLY_TOOLS`` — the set the dispatch path
    already classifies with — rather than a second list here that would drift from it.
    An unknown tool is treated as an action, matching the fail-closed reading everywhere
    else in navig: not-known-to-be-a-read is an action.
    """
    try:
        from navig.agent.speculative import READ_ONLY_TOOLS

        return "tool_result" if tool_name in READ_ONLY_TOOLS else "action"
    except Exception:  # noqa: BLE001 — classification must never break a turn
        return "tool_result"


def record_event(session_id: str, kind: str, text: str) -> None:
    """Record one event when the feature is on; a no-op otherwise. Never raises.

    Called from the agent loop for every tool result, so it must be cheap when disabled
    (one config read) and incapable of interrupting a turn when enabled.
    """
    if not text or not session_index_enabled():
        return
    index = get_session_index()
    if index is None:
        return
    seq = index.record(session_id, kind, text[:_MAX_RECORD_CHARS])

    # Bound the session. Amortised: one DELETE per _PRUNE_EVERY inserts, using the
    # monotonic rowid as the tick so no counter has to be kept anywhere.
    if seq > 0 and seq % _PRUNE_EVERY == 0:
        cap = _int_setting(_MAX_EVENTS_KEY, _DEFAULT_MAX_EVENTS)
        if cap > 0:
            index.prune(session_id, keep=cap)
        total = _int_setting(_MAX_TOTAL_KEY, _DEFAULT_MAX_TOTAL)
        if total > 0:
            index.cap_total(keep=total)


def _latest_user_text(messages: Iterable[Any]) -> str:
    """The newest user message's text, which is what a recovery is relevant *to*.

    Accepts the message dicts the compaction path already holds; anything without a
    ``role``/``content`` pair is skipped rather than raising, because this runs inside the
    agent loop and a malformed entry is not worth an exception.
    """
    latest = ""
    for msg in messages:
        try:
            if isinstance(msg, dict):
                role, content = msg.get("role"), msg.get("content")
            else:
                role, content = getattr(msg, "role", None), getattr(msg, "content", None)
            if role == "user" and isinstance(content, str) and content.strip():
                latest = content
        except Exception:  # noqa: BLE001 — one odd message must not stop the scan
            continue
    return latest


def recover_context(
    session_id: str, messages: Iterable[Any], *, max_chars: int = 2000
) -> str:
    """A re-injectable block of events relevant to the newest user message, or ``""``.

    Call this straight after a compaction: the messages the model can still see have been
    trimmed, and this hands back the slice of what was dropped that still matters. Returns
    ``""`` when the feature is off, nothing matched, or anything at all goes wrong — so a
    call site can inject unconditionally.

    ⚠ Pass the messages from BEFORE the compaction. The query is the newest user message,
    and compaction is exactly what removes it — handing in the compacted list makes this
    return ``""`` in the one situation it exists for. The caller in ``conv/agent.py``
    passes ``msg_dicts`` for that reason.
    """
    try:
        if not session_index_enabled():
            return ""
        query = _latest_user_text(messages)
        if not query:
            return ""
        index = get_session_index()
        if index is None:
            return ""
        return index.search_and_render(session_id, query, max_chars=max_chars)
    except Exception as exc:  # noqa: BLE001 — documented never-raise contract
        # The docstring above promised this and the code did not implement it. A raise
        # here lands in the agent loop's broad `except`, which logs "Context compression
        # skipped" — a message describing something that already succeeded, for a failure
        # in the recovery that follows it.
        logger.debug("session index recovery failed (%s); continuing without it", exc)
        return ""
