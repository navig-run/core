"""``navig agent context`` — the audit surface.

An ``IDENTITY.md`` that is being outranked by a persona looks exactly like an
``IDENTITY.md`` that is broken, until you can see the shadow table. This suite
pins that the report tells the truth about what won, what lost, and whether the
cached prefix is actually stable.
"""

from __future__ import annotations

import json

import pytest
from typer.testing import CliRunner

import navig.personas.soul_loader as soulmod
from navig.commands.agent import _build_context_report, agent_app

runner = CliRunner()


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    root = tmp_path / "navig"
    (root / "workspace").mkdir(parents=True)
    monkeypatch.setattr(soulmod, "config_dir", lambda: root)
    from navig.agent.conv.soul import SoulLoader

    SoulLoader._instance = None
    SoulLoader._initialized = False
    yield root
    SoulLoader._instance = None
    SoulLoader._initialized = False


class TestReportPayload:
    def test_reports_the_winning_source(self, cfg):
        (cfg / "workspace" / "IDENTITY.md").write_text("me", encoding="utf-8")
        report = _build_context_report("", "", "")
        assert report["identity"]["source"] == "identity"

    def test_lists_shadowed_sources(self, cfg):
        (cfg / "workspace" / "IDENTITY.md").write_text("winner", encoding="utf-8")
        (cfg / "workspace" / "SOUL.md").write_text("loser", encoding="utf-8")
        states = {s["tag"]: s["state"] for s in _build_context_report("", "", "")["sources"]}
        assert states["identity"] == "active"
        assert "shadowed" in states["workspace"]

    def test_absent_sources_are_marked_absent(self, cfg):
        states = {s["tag"]: s["state"] for s in _build_context_report("", "", "")["sources"]}
        assert states["workspace"] == "absent"

    def test_prefix_is_stable(self, cfg):
        report = _build_context_report("", "", "")
        assert report["prefix"]["stable"] is True
        assert report["prefix"]["first_divergence"] is None

    def test_sections_are_sized_not_split_mid_body(self, cfg):
        """Re-splitting the assembled prompt on blank lines cut inside identity."""
        headers = [s["header"] for s in _build_context_report("", "", "")["system_sections"]]
        assert headers[0] == "## Operating Rules"
        assert "## Who You Are" in headers
        assert all(h.startswith("##") or h == "<language>" for h in headers)

    def test_guardrail_floor_is_reported(self, cfg):
        gr = _build_context_report("", "", "")["guardrails"]
        assert gr["floor_version"] >= 1 and gr["chars"] > 0

    def test_no_turn_recorded_is_none_not_a_crash(self, cfg, monkeypatch):
        monkeypatch.setattr("navig.agent.usage_tracker.read_last_turn", lambda: None)
        assert _build_context_report("", "", "")["last_turn"] is None


class TestCliRendering:
    def test_human_output_renders(self, cfg):
        result = runner.invoke(agent_app, ["context"])
        assert result.exit_code == 0
        assert "Identity sources" in result.stdout
        assert "Prompt sections" in result.stdout

    def test_json_is_exactly_one_document(self, cfg):
        result = runner.invoke(agent_app, ["context", "--json"])
        assert result.exit_code == 0
        payload = json.loads(result.stdout)
        assert payload["prefix"]["stable"] is True
        assert isinstance(payload["sources"], list)

    def test_json_omits_the_raw_prompt(self, cfg):
        """``_prompt`` is a render helper, not part of the machine contract."""
        payload = json.loads(runner.invoke(agent_app, ["context", "--json"]).stdout)
        assert "_prompt" not in payload

    def test_show_prompt_prints_the_system_block(self, cfg):
        result = runner.invoke(agent_app, ["context", "--show-prompt"])
        assert result.exit_code == 0
        assert "## Operating Rules" in result.stdout
