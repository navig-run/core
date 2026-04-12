from __future__ import annotations

from pathlib import Path

import pytest

from navig.agent.tools.wiki_tools import WikiReadTool, WikiWriteTool

pytestmark = pytest.mark.integration


async def test_wiki_read_resolves_parent_project_wiki_from_nested_cwd(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    nested_dir = project_root / "src" / "pkg"
    page_path = project_root / ".navig" / "wiki" / "technical" / "api" / "auth.md"
    page_path.parent.mkdir(parents=True, exist_ok=True)
    page_path.write_text("# Auth API\n\nToken endpoint docs.", encoding="utf-8")
    nested_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.chdir(nested_dir)

    tool = WikiReadTool()
    result = await tool.run({"page": "technical/api/auth"})

    assert result.success is True
    assert "Auth API" in result.output["content"]
    assert Path(result.output["path"]).resolve() == page_path.resolve()


async def test_wiki_write_targets_parent_project_inbox_from_nested_cwd(tmp_path, monkeypatch):
    project_root = tmp_path / "project"
    nested_dir = project_root / "services" / "bot"
    project_wiki = project_root / ".navig" / "wiki"
    project_wiki.mkdir(parents=True, exist_ok=True)
    nested_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.chdir(nested_dir)

    tool = WikiWriteTool()
    result = await tool.run({"title": "Deploy Runbook", "content": "Use canary rollout."})

    assert result.success is True
    inbox_path = Path(result.output["inbox_path"]).resolve()
    assert inbox_path.parent.resolve() == (project_wiki / "inbox").resolve()
    assert inbox_path.exists()
