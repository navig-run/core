"""Mission acceptance — the Phase-14 checks that are NOT already pinned elsewhere
(per-file parser/pipeline tests live in test_recall_changelog.py, provenance rules in
test_recall_provenance.py, retrieval quality/latency/plan shape in test_recall_bench.py).

Covered here: concurrent writes, double-rebuild stability after a migration, import
attribution, migrate project isolation, privacy across every read surface, zero
inference across EVERY new path (including migrate + session_brief), and the
no-auto-resume static scan.
"""

from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from navig.recall import query
from navig.recall.archive import Archive
from navig.recall.ingest import Ingestor
from navig.recall.lifecycle import refresh as refresh_lifecycle
from navig.recall.model import Evidence, Record, Turn
from navig.recall.projects import ProjectResolver
from navig.recall.reconcile import reconcile
from navig.recall.store import RecallStore
from navig.recall.vault import Vault

_MIGRATE_DOC = """# Ledger

## 2026-05-01: First entry
- the first thing we did

## 2026-05-02: Second entry
- the second thing we did
"""


@pytest.fixture
def ing(tmp_path: Path) -> Ingestor:
    root = tmp_path / "recall"
    return Ingestor(store=RecallStore(root / "index.db"), archive=Archive(root / "archive"),
                    vault=Vault(root / "vault"),
                    resolver=ProjectResolver(entries=[("alpha", r"E:\proj\alpha")],
                                             home=r"C:\Users\someone"))


def test_concurrent_writes_do_not_corrupt_or_lock(tmp_path: Path) -> None:
    st = RecallStore(tmp_path / "conc.db")

    def _writer(k: int) -> int:
        for i in range(25):
            st.upsert_record(Record(id=f"rk-{k}-{i}", type="lore", title=f"w{k}-{i}",
                                    project="alpha"))
            st.add_links([(f"cc:{k:08d}-0000-0000-0000-{i:012d}", f"file:alpha/f{i}", "touched")])
        return k

    with ThreadPoolExecutor(max_workers=4) as ex:
        done = list(ex.map(_writer, range(4)))
    assert sorted(done) == [0, 1, 2, 3]
    assert st.stats()["records"] == 100
    assert st.stats()["links"] == 100


def test_double_rebuild_is_stable_after_a_migration(ing, tmp_path: Path) -> None:
    p = tmp_path / "CHANGELOG.md"
    p.write_text(_MIGRATE_DOC, encoding="utf-8")
    ing.import_changelog("alpha", p, root=tmp_path, apply=True)
    # a lifecycle the vault must carry across rebuilds
    rid = ing.store.list_records(rtype="lore", projects=["alpha"], limit=1)[0].id
    ing.store.set_lifecycle(rid, "committed", "observed")
    from navig.recall.vault import parse, render  # noqa: PLC0415

    vpath = Path(ing.store.record_path(rid))
    rec = parse(vpath.read_text(encoding="utf-8"))
    rec.lifecycle, rec.attrs = "committed", {**rec.attrs, "lifecycle_basis": "observed"}
    vpath.write_text(render(rec), encoding="utf-8")

    ing.reindex()
    first = (ing.store.stats()["records"], ing.store.get_record(rid).lifecycle)  # type: ignore[union-attr]
    ing.reindex()
    second = (ing.store.stats()["records"], ing.store.get_record(rid).lifecycle)  # type: ignore[union-attr]
    assert first == second == (2, "committed")


def test_migration_attribution_is_audit_not_assistant(ing, tmp_path: Path) -> None:
    p = tmp_path / "CHANGELOG.md"
    p.write_text(_MIGRATE_DOC, encoding="utf-8")
    out = ing.import_changelog("alpha", p, root=tmp_path, apply=True)
    for rid in out["record_ids"]:
        rec = ing.store.get_record(rid)
        assert rec is not None
        assert rec.status_basis == "audit"          # imported document, not a model claim
        assert rec.impl == "unknown"                # nothing verified by importing
        assert rec.lifecycle == "unknown"           # never fabricated
        assert rec.evidence[0].speaker == "imported_doc"
        assert json_locator_ok(rec.evidence[0].locator)


def json_locator_ok(locator: str) -> bool:
    import json  # noqa: PLC0415

    try:
        data = json.loads(locator)
    except ValueError:
        return False
    return isinstance(data.get("lines"), list) and len(data["lines"]) == 2


def test_migrate_keeps_projects_isolated(ing, tmp_path: Path) -> None:
    pa, pb = tmp_path / "a", tmp_path / "b"
    pa.mkdir(), pb.mkdir()
    (pa / "CHANGELOG.md").write_text(_MIGRATE_DOC, encoding="utf-8")
    (pb / "CHANGELOG.md").write_text(_MIGRATE_DOC.replace("first", "gamma"), encoding="utf-8")
    out_a = ing.import_changelog("alpha", pa / "CHANGELOG.md", root=pa, apply=True)
    out_b = ing.import_changelog("beta", pb / "CHANGELOG.md", root=pb, apply=True)
    assert set(out_a["record_ids"]).isdisjoint(out_b["record_ids"])
    a_ids = {r.id for r in ing.store.list_records(rtype="lore", projects=["alpha"], limit=50)}
    b_ids = {r.id for r in ing.store.list_records(rtype="lore", projects=["beta"], limit=50)}
    assert a_ids == set(out_a["record_ids"]) and b_ids == set(out_b["record_ids"])
    assert a_ids.isdisjoint(b_ids)


def test_privacy_holds_on_every_read_surface(tmp_path: Path) -> None:
    st = RecallStore(tmp_path / "priv.db")
    st.upsert_source({"source_id": "cc:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                      "adapter": "claude_code", "project": "alpha", "title": "open session",
                      "cwd": r"E:\proj\alpha", "meta": {}})
    st.upsert_source({"source_id": "chats:chatgpt:secret", "adapter": "chats", "project": "alpha",
                      "title": "secret chat", "meta": {"brain_level": "local"}})
    st.add_turns("chats:chatgpt:secret",
                 [Turn(msg_id="c1", speaker="user_typed", text="nightjar budget is secret")])
    st.add_links([("cc:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "file:alpha/x.py", "touched"),
                  ("cc:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "commit:abc1234", "committed")])
    st.upsert_record(Record(id="rk-secret", type="decision", title="nightjar secret", project="alpha",
                            status="confirmed", status_basis="user",
                            evidence=[Evidence(source_id="chats:chatgpt:secret", msg_id="c1",
                                               speaker="user_typed", excerpt="nightjar budget is secret")]))
    st.upsert_record(Record(id="rk-open", type="decision", title="nightjar open", project="alpha",
                            status="confirmed", status_basis="user",
                            evidence=[Evidence(source_id="cc:aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                                               msg_id="m1", speaker="user_typed", excerpt="x")]))
    st.upsert_record(Record(id="rk-plan", type="plan", title="nightjar plan secret", project="alpha",
                            lifecycle="proposed",
                            evidence=[Evidence(source_id="chats:chatgpt:secret", msg_id="c1",
                                               speaker="user_typed", excerpt="nightjar budget is secret")]))

    def ids(rows):
        return {r["id"] if isinstance(r, dict) else r.id for r in rows}

    assert "rk-secret" in ids(query.search("nightjar", store=st)["records"])
    assert "rk-secret" not in ids(query.search("nightjar", for_model=True, store=st)["records"])
    assert "rk-secret" not in ids(query.history("file:alpha/x.py", for_model=True, store=st)["records"])
    assert "rk-secret" not in ids(query.trace("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                                              for_model=True, store=st)["records"])
    assert "rk-secret" not in ids(query.unfinished("alpha", for_model=True, store=st)["records"])
    brief = query.session_brief("alpha", store=st)
    assert "nightjar secret" not in brief and "nightjar open" in brief  # filter, not blanket mute
    # local surfaces still see everything
    assert "rk-secret" in ids(query.search("nightjar", store=st)["records"])


def test_zero_inference_across_every_new_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import navig.recall.extract as extract  # noqa: PLC0415

    def boom(*_a, **_k):  # noqa: ANN002, ANN202
        raise AssertionError("an LLM call ran on a deterministic path")

    monkeypatch.setattr(extract, "run_extraction", boom)
    st = RecallStore(tmp_path / "zero.db")
    ing = Ingestor(store=st, archive=Archive(tmp_path / "arc"), vault=Vault(tmp_path / "vault"),
                   resolver=ProjectResolver(entries=[], home=r"C:\Users\x"))
    p = tmp_path / "CHANGELOG.md"
    p.write_text(_MIGRATE_DOC, encoding="utf-8")
    ing.import_changelog("alpha", p, root=tmp_path, apply=True)
    refresh_lifecycle(st, project="alpha", root=None, vault=ing.vault)
    reconcile(st, project="alpha")
    query.session_brief("alpha", store=st)
    query.history("commit:abc1234", store=st)
    query.changes("alpha", store=st)
    query.unfinished("alpha", store=st)
    query.conflicts("alpha", store=st)
    runs = st._read_one("SELECT COUNT(*) FROM extract_runs")  # noqa: SLF001
    assert runs[0] == 0
    assert st.stats()["fts"] is True  # the search layer never degrades silently here


def test_no_recall_module_can_auto_resume_or_spawn_tasks() -> None:
    """Static scan: deterministic memory paths never spawn agents or resume sessions."""
    root = Path(__file__).resolve().parents[1] / "navig" / "recall"
    banned = re.compile(r"(--resume|task_scheduler|claude\s+--|\bos\.system\(|subprocess\.Popen)")
    for f in sorted(root.glob("*.py")):
        src = f.read_text(encoding="utf-8")
        m = banned.search(src)
        assert m is None, (
            f"{f.name}:{src[:m.start()].count(chr(10)) + 1} references {m.group(0)!r} — "
            "memory reads are deterministic and spawn nothing")
