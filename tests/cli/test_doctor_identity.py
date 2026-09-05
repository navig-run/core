"""`navig doctor` → Identity: the pull-side view of what reaches the model.

Three things an operator cannot otherwise see: which of seven identity sources
won, whether the guardrail floor is intact, and whether the system prefix is
byte-stable (an unstable prefix is a silent cost multiple, never an error).

Per the doctor's house rule, a row that could not verify something must be ⚠, not
a green tick — ``tests/cli/test_doctor_honesty.py`` enforces that repo-wide; these
tests pin this section's behaviour.
"""

from __future__ import annotations

import pytest

import navig.personas.soul_loader as soulmod
from navig.commands.doctor import check_identity


def _rows(results):
    return {r.label: r for r in results}


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(soulmod, "config_dir", lambda: tmp_path)
    from navig.agent.conv.soul import SoulLoader

    SoulLoader._instance = None
    SoulLoader._initialized = False
    yield tmp_path
    SoulLoader._instance = None
    SoulLoader._initialized = False


class TestRowsExist:
    def test_three_rows(self, cfg):
        rows = _rows(check_identity())
        assert set(rows) == {"Identity source", "Guardrail floor", "Prompt prefix"}

    def test_all_green_on_a_healthy_install(self, cfg):
        assert all(r[1] for r in check_identity())


class TestIdentitySourceRow:
    def test_names_the_winning_source(self, cfg):
        rows = _rows(check_identity())
        # A bare tmp config dir resolves to the shipped package default.
        assert "package default" in rows["Identity source"].detail

    def test_reports_shadowed_sources(self, cfg):
        ws = cfg / "workspace"
        ws.mkdir(parents=True)
        (ws / "IDENTITY.md").write_text("winner", encoding="utf-8")
        (ws / "SOUL.md").write_text("loser", encoding="utf-8")

        detail = _rows(check_identity())["Identity source"].detail
        assert "workspace IDENTITY.md" in detail
        assert "shadows" in detail and "workspace SOUL.md" in detail

    def test_no_shadow_clause_when_nothing_is_shadowed(self, cfg, monkeypatch):
        monkeypatch.setattr(soulmod, "soul_candidates", lambda *a, **k: [])
        monkeypatch.setattr(soulmod, "_persona_soul_yaml", lambda *a, **k: "")
        row = _rows(check_identity())["Identity source"]
        assert row[1] is False, "nothing resolved must not read as healthy"

    def test_resolution_failure_warns_rather_than_lying(self, cfg, monkeypatch):
        def _boom(*a, **k):
            raise RuntimeError("store is locked")

        monkeypatch.setattr(soulmod, "resolve_soul", _boom)
        row = _rows(check_identity())["Identity source"]
        assert row[1] is False and "locked" in row.detail


class TestGuardrailFloorRow:
    def test_reports_the_floor_version_and_size(self, cfg):
        detail = _rows(check_identity())["Guardrail floor"].detail
        assert detail.startswith("v")
        assert "chars" in detail

    def test_counts_operator_supplied_files(self, cfg):
        ws = cfg / "workspace"
        ws.mkdir(parents=True)
        (ws / "GUARDRAILS.md").write_text("No prod deploys on Friday.", encoding="utf-8")

        import navig.platform.paths as paths

        # guardrails_paths() resolves through platform.paths, not soul_loader.
        original = paths.config_dir
        paths.config_dir = lambda: cfg
        try:
            detail = _rows(check_identity())["Guardrail floor"].detail
        finally:
            paths.config_dir = original
        assert "operator file" in detail


class TestPromptPrefixRow:
    def test_prefix_is_stable_on_a_healthy_install(self, cfg):
        row = _rows(check_identity())["Prompt prefix"]
        assert row[1] is True
        assert "tok" in row.detail

    def test_an_unstable_prefix_is_reported_not_hidden(self, cfg, monkeypatch):
        """The failure mode this row exists for: a mutating system block."""
        import itertools

        from navig.agent.conv.soul import SoulLoader

        counter = itertools.count()
        monkeypatch.setattr(
            SoulLoader,
            "build_system_prompt",
            lambda self, *a, **k: f"drifting {next(counter)}",
        )
        row = _rows(check_identity())["Prompt prefix"]
        assert row[1] is False
        assert "NOT byte-stable" in row.detail
