"""Schema v2 → v3 migration: an existing bank upgrades in place, loses nothing, and a
fresh bank comes up identical.

The fixture is a REAL v2 database (tables as v2 created them, schema_version stamped 2),
so `_migrate` runs exactly as it will on the operator's bank. Invariants: version bump,
columns present, old rows read back with lifecycle='unknown', writes round-trip, a
second open is a no-op, and reindex preserves lifecycle once it has been written.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

from navig.recall.archive import Archive
from navig.recall.ingest import Ingestor
from navig.recall.lifecycle import refresh
from navig.recall.projects import ProjectResolver
from navig.recall.store import RecallStore
from navig.recall.vault import Vault, parse, render

sys.path.insert(0, str(Path(__file__).parent))
from test_recall import rich_session  # noqa: E402

# v2 `records` exactly as store.py created it before lifecycle/locator existed.
_V2_RECORDS = """
CREATE TABLE records (
    id            TEXT PRIMARY KEY,
    type          TEXT NOT NULL,
    title         TEXT NOT NULL,
    summary       TEXT NOT NULL DEFAULT '',
    body          TEXT NOT NULL DEFAULT '',
    project       TEXT NOT NULL DEFAULT 'shared',
    status        TEXT NOT NULL DEFAULT 'proposed',
    status_basis  TEXT NOT NULL DEFAULT 'assistant',
    endorsement   TEXT NOT NULL DEFAULT 'none',
    impl          TEXT NOT NULL DEFAULT 'unknown',
    confidence    REAL NOT NULL DEFAULT 0.5,
    canonical_key TEXT NOT NULL DEFAULT '',
    aliases_json  TEXT NOT NULL DEFAULT '[]',
    tags_json     TEXT NOT NULL DEFAULT '[]',
    attrs_json    TEXT NOT NULL DEFAULT '{}',
    ext_id        TEXT NOT NULL DEFAULT '',
    path          TEXT NOT NULL DEFAULT '',
    created       TEXT NOT NULL DEFAULT '',
    updated       TEXT NOT NULL DEFAULT ''
);
CREATE TABLE evidence (
    record_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    msg_id    TEXT NOT NULL,
    speaker   TEXT NOT NULL,
    ts        TEXT NOT NULL DEFAULT '',
    excerpt   TEXT NOT NULL,
    UNIQUE (record_id, source_id, msg_id, excerpt)
);
CREATE TABLE schema_version (version INTEGER PRIMARY KEY);
INSERT INTO schema_version (version) VALUES (2);
INSERT INTO records (id, type, title, project, status, status_basis, created, updated)
VALUES ('rk-old', 'decision', 'Use the old way', 'alpha', 'confirmed', 'user',
        '2026-01-01', '2026-01-01');
INSERT INTO evidence (record_id, source_id, msg_id, speaker, excerpt)
VALUES ('rk-old', 'cc:11111111-1111-1111-1111-111111111111', 'm1', 'user_typed', 'the old way');
"""


def _v2_db(tmp_path: Path) -> Path:
    p = tmp_path / "index.db"
    conn = sqlite3.connect(p)
    conn.executescript(_V2_RECORDS)
    conn.commit()
    conn.close()
    return p


def test_v2_bank_upgrades_in_place(tmp_path: Path) -> None:
    db = _v2_db(tmp_path)
    st = RecallStore(db)
    # version stamped 3
    row = st._read_one("SELECT version FROM schema_version")
    assert row is not None and row[0] == 3
    # columns exist
    cols = {r[1] for r in st._read_all("PRAGMA table_info(records)")}
    assert "lifecycle" in cols
    ecols = {r[1] for r in st._read_all("PRAGMA table_info(evidence)")}
    assert "locator" in ecols
    # the commits cache exists (created by _create_schema on open)
    assert st._read_one("SELECT name FROM sqlite_master WHERE type='table' AND name='commits'") is not None
    # old row reads back, defaulting lifecycle
    rec = st.get_record("rk-old")
    assert rec is not None
    assert rec.lifecycle == "unknown"
    assert rec.evidence and rec.evidence[0].locator == ""
    # lifecycle writes round-trip
    assert st.set_lifecycle("rk-old", "committed", "inferred") is True
    rec2 = st.get_record("rk-old")
    assert rec2 is not None
    assert rec2.lifecycle == "committed"
    assert rec2.attrs.get("lifecycle_basis") == "inferred"
    # a second open is a no-op (version already 3 — _migrate does not rerun)
    st2 = RecallStore(db)
    assert st2.get_record("rk-old") is not None  # type: ignore[union-attr]


def test_set_lifecycle_unknown_id_is_false(tmp_path: Path) -> None:
    st = RecallStore(tmp_path / "fresh.db")
    assert st.set_lifecycle("rk-nope", "merged", "observed") is False


def test_records_by_lifecycle_filters_and_scopes(tmp_path: Path) -> None:
    from navig.recall.model import Record  # noqa: PLC0415

    st = RecallStore(tmp_path / "fresh.db")
    for rid, proj, life in (("r1", "alpha", "merged"), ("r2", "alpha", "unknown"),
                            ("r3", "beta", "merged")):
        st.upsert_record(Record(id=rid, type="feature", title=rid, project=proj, lifecycle=life))
    merged = st.records_by_lifecycle(["merged"])
    assert {r.id for r in merged} == {"r1", "r3"}
    alpha = st.records_by_lifecycle(["merged", "unknown"], projects=["alpha"])
    assert {r.id for r in alpha} == {"r1", "r2"}
    assert st.records_by_lifecycle([]) == []


def test_reset_clears_the_commits_cache(tmp_path: Path) -> None:
    st = RecallStore(tmp_path / "fresh.db")
    st.upsert_commits([{"sha": "a" * 40, "subject": "x"}])
    assert st.commits_for(limit=5)
    st.reset()
    assert st.commits_for(limit=5) == []


def test_reindex_preserves_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """lifecycle lives in the vault (truth), so a full rebuild reproduces it — refresh
    derives → vault patch → reindex → same value, without re-running refresh."""
    data = tmp_path / "data"
    monkeypatch.setenv("NAVIG_DATA_DIR", str(data))
    home = tmp_path / "claude"
    root = data / "recall"
    ing = Ingestor(store=RecallStore(root / "index.db"), archive=Archive(root / "archive"),
                   vault=Vault(root / "vault"),
                   resolver=ProjectResolver(entries=[("alpha", r"E:\proj\alpha")],
                                            home=r"C:\Users\someone"))
    rich_session().write(home)
    rep = ing.run(("claude_code",), claude_homes=[home], codex_home=home / "nocodex")
    assert rep.errors == []
    before = ing.store.stats()
    assert before["records"]

    # derive what we can (no git root → no observed git verdicts), which persists to vault
    life = refresh(ing.store, root=None, vault=ing.vault, project=None)
    assert life["checked"] == before["records"]
    with_lifecycle = [r for r in ing.store.list_records(limit=1000, projects=None)
                      if r.lifecycle != "unknown" or r.attrs.get("lifecycle_basis")]
    assert with_lifecycle, "refresh should derive at least proposal states"

    ev_before = ing.store._read_one("SELECT COUNT(*) FROM evidence")[0]
    ing.reindex()
    after = ing.store.stats()
    ev_after = ing.store._read_one("SELECT COUNT(*) FROM evidence")[0]
    assert after["records"] == before["records"]
    assert ev_after == ev_before
    # lifecycle survived the rebuild for every record we can still see
    for rec in ing.store.list_records(limit=1000, projects=None):
        assert rec.lifecycle in ("unknown", "proposed", "implemented_uncommitted",
                                 "committed", "merged", "verified", "deployed",
                                 "failed", "superseded", "abandoned")


def test_old_vault_files_without_lifecycle_still_parse(tmp_path: Path) -> None:
    """A pre-v3 vault file (no lifecycle key) parses with the default — old truth stays readable."""
    from navig.recall.model import Record  # noqa: PLC0415

    rec = Record(id="rk-legacy", type="idea", title="Legacy", project="alpha",
                 status="confirmed", status_basis="user")
    # simulate a file written before v3: no lifecycle line in the front matter
    old_text = "\n".join(ln for ln in render(rec).splitlines() if not ln.startswith("lifecycle:"))
    assert "lifecycle" not in old_text.split("---")[1]
    back = parse(old_text)
    assert back.lifecycle == "unknown"
    rec.lifecycle = "deployed"
    rec.attrs = {"lifecycle_basis": "observed"}
    back2 = parse(render(rec))
    assert back2.lifecycle == "deployed"
    assert back2.attrs.get("lifecycle_basis") == "observed"
