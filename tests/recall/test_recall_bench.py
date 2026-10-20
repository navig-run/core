"""The retrieval benchmark and the guard rails behind the provenance views.

Three things are pinned here (mission Phase 6/14):

1. **Quality** — real questions from ``benchmark/queries.jsonl`` against a fixture bank
   with gold ids: recall@10 and mean reciprocal rank (BM25 over ``any_term`` retrieval,
   the designed similarity path — plain ``query.search`` is AND-exact by contract).
2. **Latency at scale** — 5,000 records / 200,000 links: a p95 budget small enough that
   an index-vs-scan regression FAILS it (measured: commit-prefix lookup 0.01 ms indexed
   vs 10.8 ms scanned at 50k rows — a scan at 200k blows the budget by 40×).
3. **Zero inference** — no view path may enter an LLM call: the extractor entry point is
   monkeypatched to raise, ``extract_runs`` stays empty, and the four new modules are
   scanned for agent/LLM imports.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from navig.recall import query
from navig.recall.history import changes, history, trace, unfinished
from navig.recall.lifecycle import refresh as refresh_lifecycle
from navig.recall.model import Record
from navig.recall.reconcile import reconcile
from navig.recall.store import RecallStore

QUERIES = Path(__file__).parent / "benchmark" / "queries.jsonl"

# (id, title, summary, type) — question terms live in title+summary; distractors share
# one or two words on purpose so precision means something.
_BANK = [
    ("rk-bench-obs", "OBS WebSocket transport", "the controller speaks websocket to obs", "decision"),
    ("rk-bench-pg", "Postgres metadata store", "postgres backs the metadata store", "architecture"),
    ("rk-bench-ocr", "Telegram catalog OCR", "the telegram catalog reads images with ocr", "feature"),
    ("rk-bench-edge", "Lighthouse edge deploy", "lighthouse deploys the edge worker", "feature"),
    ("rk-bench-budget", "Extraction daily budget", "extraction stops at the daily budget", "decision"),
    ("rk-bench-installer", "Anchor installer branding", "windows installer branding for anchor", "feature"),
    ("rk-bench-pricing", "Bay pricing parity", "bay pricing parity against stripe", "finding"),
    ("rk-bench-roster", "FleetView agent roster", "the roster of agents in fleetview", "table"),
    ("rk-bench-fonts", "TikTok pipeline fonts", "fonts used by the tiktok pipeline", "lore"),
    ("rk-bench-mesh", "Mesh network phase", "the mesh lan phase plan", "plan"),
    ("rk-bench-alt1", "WebSocket fallback poll", "http polling as a websocket fallback", "idea"),
    ("rk-bench-alt2", "Pricing page draft", "draft copy for the pricing page", "snippet"),
    ("rk-bench-alt3", "Telegram stickers pack", "stickers for the telegram catalog", "idea"),
    ("rk-bench-alt4", "Obs scene collection", "a scene collection preset for obs", "snippet"),
]


def _quality_store(tmp_path: Path) -> RecallStore:
    st = RecallStore(tmp_path / "bench-quality.db")
    for rid, title, summary, rtype in _BANK:
        st.upsert_record(Record(id=rid, type=rtype, title=title, summary=summary, project="bench",
                                status="confirmed", status_basis="user"))
    return st


def _load_queries() -> list[dict]:
    lines = [ln for ln in QUERIES.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return [json.loads(ln) for ln in lines]


def test_benchmark_questions_have_gold() -> None:
    qs = _load_queries()
    assert len(qs) == 10
    ids = {b[0] for b in _BANK}
    for q in qs:
        assert q["gold"] and set(q["gold"]) <= ids, q["id"]
        assert q["question"] and q["search"]


def test_retrieval_quality_recall_and_rank(tmp_path: Path) -> None:
    st = _quality_store(tmp_path)
    recalls: list[float] = []
    rrs: list[float] = []
    for q in _load_queries():
        hits = st.search_records(q["search"], any_term=True, limit=10, projects=["bench"])
        ids = [h["id"] for h in hits]
        gold = list(q["gold"])
        got = [g for g in gold if g in ids]
        recalls.append(len(got) / len(gold))
        first = next((ids.index(g) + 1 for g in gold if g in ids), 0)
        rrs.append(1.0 / first if first else 0.0)
    assert len(recalls) == 10
    # fixture thresholds, measured: distinctive terms put their record in the top 3
    assert sum(recalls) / len(recalls) >= 0.8, f"mean recall@10 too low: {recalls}"
    assert sum(rrs) / len(rrs) >= 0.6, f"mean reciprocal rank too low: {rrs}"


# ── latency at scale (index-vs-scan discriminator) ──────────


def _bulk_seed(st: RecallStore, n_records: int = 5_000, n_links: int = 200_000) -> None:
    """Raw-inserted fixture rows (public APIs would take minutes at this size)."""
    rec_rows = [
        (f"rk-seed-{i:06d}", ("plan", "task", "feature")[i % 3], f"seed topic {i % 97} alpha beta gamma",
         "", "", "bench", "proposed", "assistant", "none", "unknown", 0.5, f"seed-{i % 97}",
         "[]", "[]", "{}", "", "", f"2026-01-{(i % 28) + 1:02d}", f"2026-01-{(i % 28) + 1:02d}",
         ("proposed", "implemented_uncommitted", "unknown")[i % 3])
        for i in range(n_records)
    ]
    fts_rows = [(f"seed topic {i % 97} alpha beta gamma", "", "", "", f"rk-seed-{i:06d}")
                for i in range(n_records)]
    link_rows = [
        (f"cc:{i % 997:08d}-0000-0000-0000-{i:012d}", f"commit:{i % 99991:07x}{i % 100000:05x}", "committed")
        for i in range(n_links)
    ]

    def _fill(conn) -> None:
        conn.executemany(
            "INSERT INTO records (id, type, title, summary, body, project, status, status_basis, "
            "endorsement, impl, confidence, canonical_key, aliases_json, tags_json, attrs_json, "
            "ext_id, path, created, updated, lifecycle) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rec_rows)
        conn.executemany(
            "INSERT INTO records_fts (title, aliases, summary, body, rid) VALUES (?,?,?,?,?)", fts_rows)
        conn.executemany("INSERT INTO links (a, b, rel) VALUES (?,?,?)", link_rows)

    st._tx(_fill)  # noqa: SLF001 — bulk fixture seeding only


def _p95(samples_ms: list[float]) -> float:
    ordered = sorted(samples_ms)
    return ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))]


def test_latency_budgets_at_scale(tmp_path: Path) -> None:
    st = RecallStore(tmp_path / "bench-scale.db")
    _bulk_seed(st)

    def _time(fn, runs: int) -> float:
        fn()  # warm-up
        out = []
        for _ in range(runs):
            t0 = time.perf_counter()
            fn()
            out.append((time.perf_counter() - t0) * 1000)
        return _p95(out)

    p_commit = _time(lambda: st.sessions_for("commit:0013ab456789"), 30)
    assert p_commit < 5.0, f"commit-prefix lookup p95 {p_commit:.2f} ms — index regression (a scan at 200k links takes ~40 ms)"

    p_search = _time(lambda: st.search_records("seed topic", any_term=True, limit=10), 20)
    assert p_search < 100.0, f"search p95 {p_search:.2f} ms"

    p_life = _time(lambda: st.records_by_lifecycle(
        ("proposed", "implemented_uncommitted", "committed", "unknown"),
        rtypes=("plan", "task"), projects=["bench"], limit=30, oldest_first=True), 10)
    assert p_life < 50.0, f"unfinished lookup p95 {p_life:.2f} ms"


def test_plan_shapes_use_the_indexes(tmp_path: Path) -> None:
    """EXPLAIN QUERY PLAN on the exact statements store.py executes (keep literals in
    sync with store.sessions_for / links_of / records_by_lifecycle). Bound parameters
    are not needed for plan generation."""
    st = RecallStore(tmp_path / "bench-plan.db")
    _bulk_seed(st)

    def plan(sql: str, params: tuple) -> str:
        # Realistic bound values are REQUIRED: the planner constant-folds `b GLOB NULL`
        # to a SCAN, while the real prefix the code binds gets the index seek.
        return " | ".join(str(r[3]) for r in st._read_all(  # noqa: SLF001
            "EXPLAIN QUERY PLAN " + sql, params))

    commit_plan = plan("SELECT a, rel, b FROM links WHERE b GLOB ? AND a LIKE 'cc:%'",
                       ("commit:0001388*",))
    assert "USING INDEX idx_links_b" in commit_plan, commit_plan

    links_plan = plan("SELECT b, rel FROM links WHERE a = ? ORDER BY rel, b",
                      ("cc:00000001-0000-0000-0000-000000000001",))
    # the UNIQUE(a,b,rel) autoindex leads with `a` — an indexed seek, no table scan
    assert "SEARCH links USING" in links_plan and "(a=?)" in links_plan, links_plan
    assert "SCAN links" not in links_plan, links_plan

    life_plan = plan(
        "SELECT * FROM records WHERE lifecycle IN (?,?,?,?) AND type IN (?,?) AND project = ? "
        "ORDER BY updated ASC, id LIMIT ?",
        ("proposed", "implemented_uncommitted", "committed", "unknown", "plan", "task", "bench", 30))
    # whichever index the optimizer picks (stats decide: type vs lifecycle), the invariant
    # is an indexed SEARCH — never a full table scan.
    assert "SEARCH records USING" in life_plan, life_plan
    assert "SCAN records" not in life_plan, life_plan


# ── zero inference ──────────────────────────────────────────


def test_view_modules_import_no_llm_code() -> None:
    """Import-line scan (word-form bans self-trip on prose like 'no embeddings')."""
    import re

    import navig.recall.gitprov as gitprov  # noqa: PLC0415
    import navig.recall.history as history  # noqa: PLC0415
    import navig.recall.lifecycle as lifecycle  # noqa: PLC0415
    import navig.recall.reconcile as reconcile  # noqa: PLC0415

    banned = ("navig.agent", "navig.recall.extract", "navig.memory", "anthropic", "openai", "embeddings")
    import_line = re.compile(r"^\s*(?:from|import)\s+[\w.]+", re.M)
    for mod in (history, reconcile, gitprov, lifecycle):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        for m in import_line.finditer(src):
            line = m.group(0)
            assert not any(b in line for b in banned), f"{mod.__name__} imports from {line.strip()!r}"


def test_no_view_path_enters_the_extractor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import navig.recall.extract as extract  # noqa: PLC0415

    def boom(*_a, **_k):  # noqa: ANN002, ANN202
        raise AssertionError("an LLM extraction ran during a read path")

    monkeypatch.setattr(extract, "run_extraction", boom)
    st = _quality_store(tmp_path)
    st.upsert_record(Record(id="rk-t", type="plan", title="t", project="bench", lifecycle="proposed",
                            status="proposed"))
    query.search("obs", store=st)
    query.ask("obs", store=st)
    history("commit:0123456789ab", store=st)
    trace("0123456789ab", store=st)
    changes("bench", store=st)
    unfinished("bench", store=st)
    reconcile(st, project="bench")
    refresh_lifecycle(st, project="bench", root=None)
    runs = st._read_one("SELECT COUNT(*) FROM extract_runs")  # noqa: SLF001
    assert runs[0] == 0
