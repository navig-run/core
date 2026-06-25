"""Tests for inbox promotion (navig.inbox.promotion)."""
from __future__ import annotations

from pathlib import Path

from navig.inbox.promotion import normalize_tier, promote


def _project(tmp_path: Path) -> Path:
    (tmp_path / ".navig" / "plans").mkdir(parents=True)
    return tmp_path


def test_normalize_tier_aliases() -> None:
    assert normalize_tier("roadmap") == "plan/roadmap"
    assert normalize_tier("after-mvp") == "plan/after-mvp"
    assert normalize_tier("later") == "plan/deferred"


def test_promote_to_roadmap_appends_bullet(tmp_path: Path) -> None:
    root = _project(tmp_path)
    idea = root / "idea.md"
    idea.write_text("# Universal inbox\nIngest anything.", encoding="utf-8")
    res = promote(str(idea), to_tier="roadmap", project_root=root)
    assert res["ok"]
    roadmap = Path(res["plan_file"])
    text = roadmap.read_text(encoding="utf-8")
    assert "## Roadmap" in text
    assert "Universal inbox" in text  # H1 used as the bullet title
    assert idea.exists()  # source never deleted


def test_promote_deferred_and_after_mvp_go_to_dev_plan(tmp_path: Path) -> None:
    root = _project(tmp_path)
    idea = root / "x.md"
    idea.write_text("# Feature X\nbody", encoding="utf-8")
    r1 = promote(str(idea), to_tier="deferred", project_root=root)
    r2 = promote(str(idea), to_tier="after-mvp", project_root=root)
    assert Path(r1["plan_file"]).name == "DEV_PLAN.md"
    dev = Path(r1["plan_file"]).read_text(encoding="utf-8")
    assert "## Deferred / Later" in dev
    assert "## After MVP" in dev
    assert Path(r2["plan_file"]) == Path(r1["plan_file"])


def test_promote_logs_decision(tmp_path: Path) -> None:
    from navig.inbox.store import InboxEvent, InboxStore

    root = _project(tmp_path)
    store = InboxStore(db_path=tmp_path / "inbox.db")
    src = root / "note.md"
    src.write_text("# Title\nbody", encoding="utf-8")
    eid = store.insert_event(InboxEvent(source_path=str(src), filename="note.md"))
    res = promote(eid, to_tier="roadmap", project_root=root, store=store)
    assert res["ok"]
    decisions = store.decisions_for_event(eid)
    assert any(d.classifier == "promote" and d.category == "plan/roadmap" for d in decisions)


def test_promote_summary_override(tmp_path: Path) -> None:
    root = _project(tmp_path)
    src = root / "s.md"
    src.write_text("# Ignored\nbody", encoding="utf-8")
    res = promote(str(src), to_tier="roadmap", summary="Custom one-liner", project_root=root)
    assert res["summary"] == "Custom one-liner"
    assert "Custom one-liner" in Path(res["plan_file"]).read_text(encoding="utf-8")


def test_promote_unknown_tier_returns_error(tmp_path: Path) -> None:
    root = _project(tmp_path)
    src = root / "s.md"
    src.write_text("# t\n", encoding="utf-8")
    res = promote(str(src), to_tier="nonsense", project_root=root)
    assert res["ok"] is False
    assert "unknown tier" in res["error"]
