"""knowledge.db: one canonical resolver + a safe, dry-run-by-default migration.

Live symptom this closes: the same logical knowledge store lived at ``<config_dir>/
knowledge.db`` (gateway/deck) and ``<config_dir>/memory/knowledge.db`` (CLI + agent context
reader) — two files, both opened by the running daemon, so a row added on one surface was
invisible on the other. ``paths.knowledge_db_path()`` is now the authority
(``<data_dir>/knowledge.db``) and ``migrate_knowledge_dbs`` moves the legacy copies.

These tests pin the properties that matter for data safety:

* the resolver honours ``NAVIG_DATA_DIR`` (per-store isolation, like every other store);
* DRY RUN writes nothing at all — no canonical DB, no backup dir, no renames;
* apply backs EVERY existing copy up before writing, moves the single populated one, and
  retires (never deletes) the source;
* two populated copies are a CONFLICT — reported, nothing merged, nothing moved;
* a copy that cannot be read BLOCKS the migration (no data judged on a failed read);
* a gateway constructed without ``__init__`` resolves its knowledge DB through the resolver;
* **B1** — canonical absent + populated legacy copy is READ (rows visible), with an incident
  and a warning, so the single-authority switch can never present an empty Knowledge view;
* **B2** — installed-but-could-not-retire is ``applied-partial``: the CLI says so plainly and
  records an incident, because a live legacy file keeps diverging (tested with an open SQLite
  handle on Windows, and with a blocked rename everywhere).
"""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

import pytest

from navig.memory.knowledge_migration import migrate_knowledge_dbs
from navig.platform import paths

_SCHEMA = """
CREATE TABLE IF NOT EXISTS knowledge (
    id TEXT PRIMARY KEY,
    key TEXT UNIQUE NOT NULL,
    content TEXT NOT NULL,
    summary TEXT,
    tags TEXT DEFAULT '[]',
    source TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    expires_at TEXT,
    metadata TEXT DEFAULT '{}',
    embedding TEXT
)
"""


def _make_db(path: Path, rows: int = 0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA)
        for index in range(rows):
            conn.execute(
                "INSERT INTO knowledge (id, key, content, created_at) VALUES (?, ?, ?, ?)",
                (f"id{index}", f"key{index}", f"content{index}", "2026-01-01T00:00:00"),
            )
        conn.commit()
    finally:
        conn.close()


def _rows(path: Path) -> int:
    with sqlite3.connect(path) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM knowledge").fetchone()[0])


@pytest.fixture
def brain(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Isolated config + data roots (both env vars — never the real home)."""
    for var in (
        "NAVIG_STORE_DIR",
        "NAVIG_LOG_DIR",
        "NAVIG_CACHE_DIR",
        "NAVIG_PACKAGES_DIR",
        "NAVIG_STACK_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    config_root = tmp_path / "brain" / ".navig"
    data_root = tmp_path / "brain" / "data"
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(config_root))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(data_root))
    config_root.mkdir(parents=True)
    data_root.mkdir(parents=True)
    return config_root, data_root


@pytest.fixture(autouse=True)
def _reset_legacy_read_warning():
    """The one-warning-per-process flag must not leak between tests."""
    from navig.memory import knowledge_migration as km

    km._legacy_read_warned = False
    yield
    km._legacy_read_warned = False


def test_resolver_is_one_path_under_data_dir(brain, monkeypatch: pytest.MonkeyPatch) -> None:
    config_root, data_root = brain
    assert paths.knowledge_db_path() == data_root / "knowledge.db"

    # NAVIG_DATA_DIR is authoritative — the canonical path follows it, per brain.
    other = data_root.parent / "other-data"
    monkeypatch.setenv("NAVIG_DATA_DIR", str(other))
    assert paths.knowledge_db_path() == other / "knowledge.db"
    assert not str(paths.knowledge_db_path()).startswith(str(config_root))


def test_nothing_to_do_reports_cleanly(brain) -> None:
    report = migrate_knowledge_dbs()
    assert report["status"] == "nothing-to-do"
    assert report["canonical"] == str(paths.knowledge_db_path())


def test_dry_run_writes_nothing(brain) -> None:
    config_root, data_root = brain
    legacy = config_root / "knowledge.db"
    _make_db(legacy, rows=3)

    report = migrate_knowledge_dbs()  # dry-run is the default

    assert report["status"] == "dry-run"
    assert report["candidate"] == str(legacy)
    assert not (data_root / "knowledge.db").exists(), "dry run must not create the canonical DB"
    assert not (config_root / ".backup").exists(), "dry run must not create backups"
    assert legacy.exists(), "dry run must not move the source"
    assert not (config_root / "knowledge.db.migrated").exists()


def test_apply_moves_the_single_populated_copy_and_backs_everything_up(brain) -> None:
    config_root, data_root = brain
    legacy_root = config_root / "knowledge.db"
    _make_db(legacy_root, rows=2)
    empty_memory = config_root / "memory" / "knowledge.db"
    _make_db(empty_memory, rows=0)

    report = migrate_knowledge_dbs(apply=True)

    assert report["status"] == "applied", report
    canonical = data_root / "knowledge.db"
    assert canonical.exists()
    assert _rows(canonical) == 2

    # Both EXISTING copies were backed up before the write; the backup dir is under config.
    assert str(report["backup_dir"]).startswith(str(config_root / ".backup"))
    assert len(report["backups"]) == 2
    for backup in report["backups"]:
        assert Path(backup).exists(), f"missing backup {backup}"
        assert _rows(Path(backup)) in (0, 2)

    # The moved source is retired (rename), never deleted; the empty one stays put.
    assert not legacy_root.exists()
    assert (config_root / "knowledge.db.migrated").exists()
    assert empty_memory.exists()


def test_two_populated_copies_are_a_conflict_and_nothing_moves(brain) -> None:
    config_root, data_root = brain
    root_db = config_root / "knowledge.db"
    memory_db = config_root / "memory" / "knowledge.db"
    _make_db(root_db, rows=2)
    _make_db(memory_db, rows=5)

    report = migrate_knowledge_dbs(apply=True)

    assert report["status"] == "conflict", report
    assert not (data_root / "knowledge.db").exists()
    assert not (config_root / ".backup").exists()
    assert _rows(root_db) == 2 and _rows(memory_db) == 5
    assert all(copy["path"] for copy in report["copies"])


def test_already_canonical_with_one_leftover_converges_on_apply(brain) -> None:
    """A re-run after a partial retire must FINISH the job, not report no-op.

    Shape: canonical has rows (the install succeeded) and exactly one populated legacy copy
    is left behind. The re-run keeps the canonical data (never reinstalls over it), backs
    everything up, and retires the leftover source.
    """
    config_root, data_root = brain
    _make_db(data_root / "knowledge.db", rows=7)
    legacy = config_root / "knowledge.db"
    _make_db(legacy, rows=2)

    report = migrate_knowledge_dbs(apply=True)

    assert report["status"] == "retired-now", report
    assert _rows(data_root / "knowledge.db") == 7, "canonical rows must never be replaced"
    assert not legacy.exists()
    assert (config_root / "knowledge.db.migrated").exists()
    assert report["backups"], "the retire-only path must back up before moving"
    for backup in report["backups"]:
        assert Path(backup).exists()


def test_retire_only_dry_run_writes_nothing(brain) -> None:
    config_root, data_root = brain
    _make_db(data_root / "knowledge.db", rows=7)
    legacy = config_root / "knowledge.db"
    _make_db(legacy, rows=2)

    report = migrate_knowledge_dbs()  # dry run

    assert report["status"] == "retire-pending", report
    assert report["candidate"] == str(legacy)
    assert legacy.exists()
    assert not (config_root / ".backup").exists()
    assert not (config_root / "knowledge.db.migrated").exists()
    assert _rows(data_root / "knowledge.db") == 7


def test_two_leftover_legacies_under_a_populated_canonical_are_report_only(brain) -> None:
    config_root, data_root = brain
    _make_db(data_root / "knowledge.db", rows=5)
    root_legacy = config_root / "knowledge.db"
    memory_legacy = config_root / "memory" / "knowledge.db"
    _make_db(root_legacy, rows=2)
    _make_db(memory_legacy, rows=3)

    report = migrate_knowledge_dbs(apply=True)

    assert report["status"] == "already-canonical", report
    assert not (config_root / ".backup").exists()
    assert root_legacy.exists() and memory_legacy.exists()
    assert _rows(data_root / "knowledge.db") == 5


def test_rerun_after_a_partial_retire_converges(brain, monkeypatch: pytest.MonkeyPatch) -> None:
    """Blocked retire → close the writer → re-run: the legacy source is finally retired."""
    config_root, data_root = brain
    legacy = config_root / "knowledge.db"
    _make_db(legacy, rows=2)

    real_rename = Path.rename

    def _blocked_rename(self: Path, target):
        if self.name == "knowledge.db" and str(target).endswith(".migrated"):
            raise PermissionError(32, "The process cannot access the file")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", _blocked_rename)
    first = migrate_knowledge_dbs(apply=True)
    assert first["status"] == "applied-partial", first
    assert legacy.exists()

    monkeypatch.setattr(Path, "rename", real_rename)  # service stopped; lock released
    second = migrate_knowledge_dbs(apply=True)

    assert second["status"] == "retired-now", second
    assert not legacy.exists()
    assert (config_root / "knowledge.db.migrated").exists()
    assert _rows(data_root / "knowledge.db") == 2, "the re-run must not reinstall or wipe"


@pytest.mark.skipif(os.name != "nt", reason="only Windows refuses to rename an open file")
def test_rerun_after_the_daemon_closes_the_handle(brain) -> None:
    """The verifier's scenario end-to-end: open handle → partial → close → re-run converges."""
    config_root, data_root = brain
    legacy = config_root / "knowledge.db"
    _make_db(legacy, rows=2)

    connection = sqlite3.connect(legacy)
    try:
        connection.execute("SELECT COUNT(*) FROM knowledge").fetchone()
        first = migrate_knowledge_dbs(apply=True)
    finally:
        connection.close()

    assert first["status"] == "applied-partial", first
    assert legacy.exists()

    second = migrate_knowledge_dbs(apply=True)
    assert second["status"] == "retired-now", second
    assert not legacy.exists()
    assert _rows(data_root / "knowledge.db") == 2


def test_legacy_read_incident_is_recorded_once_per_process(
    brain, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """INCIDENT SPAM fix: 5 resolver calls → exactly ONE JSONL line (not 5).

    `resolve_readable_knowledge_db_path()` runs on every memory-context build; recording an
    incident per call appended a line to perf/config_incidents.jsonl each time (measured 5
    calls → 5 lines), which is unbounded growth until migration.
    """
    from navig.memory import knowledge_migration as km

    config_root, _data_root = brain
    _make_db(config_root / "knowledge.db", rows=3)
    monkeypatch.setattr(km, "_legacy_read_warned", False)

    with caplog.at_level("WARNING", logger="navig.memory.knowledge_migration"):
        for _ in range(5):
            assert km.resolve_readable_knowledge_db_path() == config_root / "knowledge.db"

    incident_log = config_root / "perf" / "config_incidents.jsonl"
    lines = [
        line for line in incident_log.read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    assert len(lines) == 1, f"5 resolver calls must append ONE incident line, got {len(lines)}"
    entry = json.loads(lines[0])
    assert entry["event"] == "knowledge_db_legacy_read"

    warnings = [m for m in caplog.messages if "LEGACY copy" in m]
    assert len(warnings) == 1, "the warning is once per process too"


def test_cli_reports_the_convergence_outcomes(capsys: pytest.CaptureFixture[str]) -> None:
    from navig.commands.db_local import _print_knowledge_report

    _print_knowledge_report(
        {
            "status": "retired-now",
            "canonical": "C:/data/knowledge.db",
            "backup_dir": "C:/cfg/.backup/knowledge-db-20261022-140000",
            "retired": ["C:/cfg/knowledge.db.migrated"],
            "notes": [],
        }
    )
    out = capsys.readouterr().out
    assert "leftover legacy source retired" in out
    assert "✓" in out

    _print_knowledge_report(
        {
            "status": "retire-pending",
            "canonical": "C:/data/knowledge.db",
            "candidate": "C:/cfg/knowledge.db",
            "backup_dir": "C:/cfg/.backup/knowledge-db-20261022-140000",
            "notes": [],
        }
    )
    pending_out = capsys.readouterr().out
    assert "would be retired" in pending_out
    assert "re-run without --dry-run" in pending_out


def test_unreadable_copy_blocks_instead_of_guessing(brain) -> None:
    config_root, data_root = brain
    broken = config_root / "knowledge.db"
    broken.write_bytes(b"this is not a sqlite database" * 40)
    before = broken.read_bytes()

    report = migrate_knowledge_dbs(apply=True)

    assert report["status"] == "blocked", report
    assert "cannot be read" in " ".join(report["notes"])
    assert not (data_root / "knowledge.db").exists()
    assert not (config_root / ".backup").exists()
    assert broken.read_bytes() == before


def test_empty_copies_choose_the_newest_and_move_it(brain) -> None:
    config_root, data_root = brain
    older = config_root / "knowledge.db"
    newer = config_root / "memory" / "knowledge.db"
    _make_db(older, rows=0)
    _make_db(newer, rows=0)
    old_stamp = time.time() - 90 * 86_400
    os.utime(older, (old_stamp, old_stamp))

    report = migrate_knowledge_dbs(apply=True)

    assert report["status"] == "applied", report
    assert report["candidate"] == str(newer)
    assert (data_root / "knowledge.db").exists()
    assert _rows(data_root / "knowledge.db") == 0
    assert older.exists(), "only the candidate is retired; the other copy is reported, not moved"


def test_gateway_knowledge_base_uses_the_resolver(brain, monkeypatch: pytest.MonkeyPatch) -> None:
    """The gateway's KnowledgeBase is opened at the canonical path, not storage_dir."""
    from navig.gateway import server as gateway_server

    captured: dict[str, object] = {}

    class _FakeKB:
        def __init__(self, db_path, embedding_provider=None):
            captured["db_path"] = db_path

    monkeypatch.setattr("navig.memory.KnowledgeBase", _FakeKB)

    gateway = gateway_server.NavigGateway.__new__(gateway_server.NavigGateway)
    gateway._get_knowledge_base()

    assert captured["db_path"] == paths.knowledge_db_path()


# ── B1: visibility fallback — rows in a legacy copy must never look like an empty DB ──


def test_populated_legacy_copy_is_read_with_incident_and_warning(
    brain, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """The exact state the verifier proved: canonical absent, 3 rows in a legacy copy."""
    from navig.core import incidents as incidents_mod
    from navig.memory import knowledge_migration as km
    from navig.memory.knowledge_base import KnowledgeBase

    config_root, _data_root = brain
    legacy = config_root / "knowledge.db"
    _make_db(legacy, rows=3)

    monkeypatch.setattr(km, "_legacy_read_warned", False)
    recorded: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        incidents_mod, "record", lambda event, **data: recorded.append((event, data))
    )

    with caplog.at_level("WARNING", logger="navig.memory.knowledge_migration"):
        resolved = km.resolve_readable_knowledge_db_path()

    assert resolved == legacy, "reads must be served from the populated legacy copy"
    kb = KnowledgeBase(resolved, embedding_provider=None, auto_expire=False)
    try:
        assert kb.count() == 3, "the Knowledge view must NOT be empty while rows exist"
    finally:
        kb.close()

    assert any(event == incidents_mod.KNOWLEDGE_DB_LEGACY_READ for event, _ in recorded)
    assert any("navig db local migrate" in message for message in caplog.messages), (
        "the operator must be told how to consolidate"
    )


def test_canonical_wins_once_it_has_rows(brain) -> None:
    from navig.memory.knowledge_migration import resolve_readable_knowledge_db_path

    config_root, data_root = brain
    _make_db(config_root / "knowledge.db", rows=2)
    _make_db(data_root / "knowledge.db", rows=5)

    assert resolve_readable_knowledge_db_path() == data_root / "knowledge.db"


def test_resolver_returns_canonical_when_nothing_is_populated(brain) -> None:
    from navig.memory.knowledge_migration import resolve_readable_knowledge_db_path

    config_root, _data_root = brain
    _make_db(config_root / "knowledge.db", rows=0)

    assert resolve_readable_knowledge_db_path() == paths.knowledge_db_path()


# ── B2: a live legacy source is a distinct NON-success, never "consolidated" ──


def test_retire_failure_is_reported_as_applied_partial(
    brain, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The canonical install succeeded, but the source could not be moved aside."""
    config_root, data_root = brain
    legacy = config_root / "knowledge.db"
    _make_db(legacy, rows=2)

    real_rename = Path.rename

    def _blocked_rename(self: Path, target):
        if self.name == "knowledge.db" and str(target).endswith(".migrated"):
            raise PermissionError(32, "The process cannot access the file")
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", _blocked_rename)

    from navig.core import incidents as incidents_mod

    recorded: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        incidents_mod, "record", lambda event, **data: recorded.append((event, data))
    )

    report = migrate_knowledge_dbs(apply=True)

    assert report["status"] == "applied-partial", report
    assert report["status"] != "applied", "a live legacy source must never look like success"
    assert report["errors"]
    assert (data_root / "knowledge.db").exists()
    assert _rows(data_root / "knowledge.db") == 2
    assert legacy.exists(), "a source we could not retire must stay exactly where it was"
    notes = " ".join(report["notes"])
    assert "could not be retired" in notes
    assert "navig service stop" in notes and "re-run" in notes
    assert any(event == incidents_mod.KNOWLEDGE_DB_RETIRE_FAILED for event, _ in recorded)


@pytest.mark.skipif(os.name != "nt", reason="only Windows refuses to rename an open file")
def test_retire_failure_with_a_live_sqlite_handle(brain) -> None:
    """The real-world shape: the daemon holds the legacy DB open while migrate runs."""
    config_root, data_root = brain
    legacy = config_root / "knowledge.db"
    _make_db(legacy, rows=2)

    connection = sqlite3.connect(legacy)
    try:
        connection.execute("SELECT COUNT(*) FROM knowledge").fetchone()  # keep the handle live
        report = migrate_knowledge_dbs(apply=True)
    finally:
        connection.close()

    assert report["status"] == "applied-partial", report
    assert _rows(data_root / "knowledge.db") == 2
    assert legacy.exists()


def test_cli_report_never_claims_success_on_partial(capsys: pytest.CaptureFixture[str]) -> None:
    from navig.commands.db_local import _print_knowledge_report

    _print_knowledge_report(
        {
            "status": "applied-partial",
            "canonical": "C:/data/knowledge.db",
            "errors": ["C:/cfg/knowledge.db: [WinError 32] in use"],
            "notes": [],
        }
    )
    out = capsys.readouterr().out

    assert "could NOT be retired" in out
    assert "navig service stop" in out
    assert "consolidated" not in out and "✓" not in out


def test_cli_dry_run_states_the_apply_step(capsys: pytest.CaptureFixture[str]) -> None:
    from navig.commands.db_local import _print_knowledge_report

    _print_knowledge_report(
        {
            "status": "dry-run",
            "canonical": "C:/data/knowledge.db",
            "candidate": "C:/cfg/knowledge.db",
            "backup_dir": "C:/cfg/.backup/knowledge-db-20261022-120000",
            "notes": [],
        }
    )

    assert "re-run without --dry-run" in capsys.readouterr().out
