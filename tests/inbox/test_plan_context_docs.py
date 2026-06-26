"""PlanContext reads VISION/ROADMAP + deferred/after-mvp into context (WS3)."""
from __future__ import annotations

from pathlib import Path

from navig.plans.context import PlanContext, _extract_md_section


def test_extract_md_section() -> None:
    text = "# Dev\n\n## After MVP\n- a\n- b\n\n## Deferred / Later\n- c\n"
    assert _extract_md_section(text, "## After MVP") == "- a\n- b"
    assert _extract_md_section(text, "## Deferred / Later") == "- c"
    assert _extract_md_section(text, "## Missing") == ""


def test_gather_includes_project_docs(tmp_path: Path) -> None:
    (tmp_path / ".navig" / "plans").mkdir(parents=True)
    (tmp_path / "VISION.md").write_text("# Vision\nUnify the inbox.", encoding="utf-8")
    (tmp_path / "ROADMAP.md").write_text("# Roadmap\n\n## Roadmap\n- [ ] ship it\n", encoding="utf-8")
    (tmp_path / ".navig" / "plans" / "DEV_PLAN.md").write_text(
        "# Dev\n\n## After MVP\n- [ ] echo UI\n\n## Deferred / Later\n- [ ] mini\n", encoding="utf-8"
    )

    pc = PlanContext(cwd=tmp_path)
    snap = pc.gather(space="default")
    pdocs = snap.get("project_docs") or {}
    assert "Unify the inbox" in (pdocs.get("vision") or "")
    assert "ship it" in (pdocs.get("roadmap") or "")
    assert "echo UI" in (pdocs.get("after_mvp") or "")
    assert "mini" in (pdocs.get("deferred") or "")

    prompt = pc.format_for_prompt(snap)
    assert "## Vision & Roadmap" in prompt
    assert "ship it" in prompt


def test_promoted_bullet_reaches_context(tmp_path: Path) -> None:
    from navig.inbox.promotion import promote

    (tmp_path / ".navig" / "plans").mkdir(parents=True)
    idea = tmp_path / "idea.md"
    idea.write_text("# Telegram downloader\nText a link.", encoding="utf-8")
    promote(str(idea), to_tier="roadmap", project_root=tmp_path)

    pc = PlanContext(cwd=tmp_path)
    prompt = pc.format_for_prompt(pc.gather(space="default"))
    assert "Telegram downloader" in prompt
