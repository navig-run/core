"""One SOUL chain, shared by every surface.

There used to be three: ``agent/conv/soul.py`` (3 levels, LIVE),
``personas/soul_loader.py`` (7 levels, dead) and ``agent/soul.py`` (2 levels).
The live path used the weakest one, so persona souls, space souls,
``cwd/.navig/SOUL.md`` and ``IDENTITY.md`` were all implemented, tested, and
unreachable in production.

The highest-risk test here is ``TestPackageDefaultPersonaGuard``: without it,
wiring personas in swaps every operator's rich identity for a 558-char stub.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import navig.agent.conv.soul as convsoul
import navig.personas.soul_loader as soulmod
from navig.personas.soul_loader import (
    SOURCE_ORDER,
    load_soul,
    resolve_soul,
    soul_candidates,
)


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """An isolated config dir for the whole chain."""
    root = tmp_path / "navig"
    root.mkdir()
    monkeypatch.setattr(soulmod, "config_dir", lambda: root)
    return root


class TestPriorityLadder:
    def test_identity_beats_workspace(self, cfg):
        _write(cfg / "workspace" / "IDENTITY.md", "identity wins")
        _write(cfg / "workspace" / "SOUL.md", "workspace loses")
        assert resolve_soul().source == "identity"
        assert load_soul() == "identity wins"

    def test_space_beats_identity(self, cfg):
        _write(cfg / "spaces" / "ops" / "SOUL.md", "space wins")
        _write(cfg / "workspace" / "IDENTITY.md", "identity loses")
        assert resolve_soul(active_space="ops").source == "space"

    def test_folder_space_beats_identity(self, cfg, tmp_path):
        proj = tmp_path / "proj"
        _write(proj / ".navig" / "SOUL.md", "folder space wins")
        _write(cfg / "workspace" / "IDENTITY.md", "identity loses")
        res = resolve_soul(cwd=proj)
        assert res.source == "folder-space"
        assert res.raw == "folder space wins"

    def test_folder_space_skipped_when_cwd_is_none(self, cfg, tmp_path):
        """Never guess a project root — no cwd means no folder-space probe."""
        _write(tmp_path / ".navig" / "SOUL.md", "should not be seen")
        _write(cfg / "workspace" / "IDENTITY.md", "identity wins")
        assert resolve_soul().source == "identity"

    def test_persona_beats_everything(self, cfg, monkeypatch):
        persona_dir = cfg / "personas" / "techie"
        _write(persona_dir / "soul.md", "persona wins")
        _write(cfg / "spaces" / "ops" / "SOUL.md", "space loses")
        monkeypatch.setattr(
            "navig.personas.resolver.resolve_persona", lambda name, cwd=None: persona_dir
        )
        res = resolve_soul(persona_name="techie", active_space="ops")
        assert res.source == "persona"
        assert res.persona == "techie"

    def test_empty_file_falls_through(self, cfg):
        _write(cfg / "workspace" / "IDENTITY.md", "   \n  ")
        _write(cfg / "workspace" / "SOUL.md", "real content")
        assert resolve_soul().source == "workspace"

    def test_source_order_is_the_declared_contract(self):
        assert SOURCE_ORDER == (
            "persona",
            "space",
            "folder-space",
            "identity",
            "workspace",
            "resources",
            "context",
        )


class TestPackageDefaultPersonaGuard:
    """``get_active_persona()`` returns "default" for every un-chosen install.

    The package ships ``resources/personas/default/soul.md`` — a ~550-char stub
    whose entire content says the real identity lives in ``SOUL.default.md``.
    Honouring it would replace the rich identity with a summary of itself, on
    every install that never picked a persona.
    """

    def test_package_default_persona_is_ignored(self, cfg):
        _write(cfg / "workspace" / "SOUL.md", "the operator's real soul")
        res = resolve_soul(persona_name="default")
        assert res.source == "workspace"
        assert res.raw == "the operator's real soul"

    def test_package_default_does_not_shadow_the_shipped_identity(self, cfg):
        res = resolve_soul(persona_name="default")
        assert res.source == "resources"
        assert len(res.raw) > 2_000, "should be the full SOUL.default.md, not the stub"

    def test_user_authored_default_persona_still_wins(self, cfg, monkeypatch):
        """A default the OPERATOR wrote is a deliberate choice — honour it."""
        persona_dir = cfg / "personas" / "default"
        _write(persona_dir / "soul.md", "my own default persona")
        monkeypatch.setattr(
            "navig.personas.resolver.resolve_persona", lambda name, cwd=None: persona_dir
        )
        res = resolve_soul(persona_name="default")
        assert res.source == "persona"
        assert res.raw == "my own default persona"

    def test_non_default_package_persona_is_honoured(self, cfg):
        res = resolve_soul(persona_name="tyler")
        assert res.source == "persona", "only 'default' is guarded, not every package persona"


class TestShadowReporting:
    def test_shadowed_lists_existing_losers_only(self, cfg):
        _write(cfg / "workspace" / "IDENTITY.md", "winner")
        _write(cfg / "workspace" / "SOUL.md", "loser")
        tags = [s.tag for s in resolve_soul().shadowed]
        assert "workspace" in tags
        assert "space" not in tags  # never existed

    def test_shadowed_carries_path_and_size(self, cfg):
        _write(cfg / "workspace" / "IDENTITY.md", "winner")
        _write(cfg / "workspace" / "SOUL.md", "12345")
        shadow = next(s for s in resolve_soul().shadowed if s.tag == "workspace")
        assert shadow.chars == 5
        assert shadow.path.name == "SOUL.md"

    def test_revision_changes_with_content(self, cfg):
        p = _write(cfg / "workspace" / "SOUL.md", "one")
        first = resolve_soul().revision
        p.write_text("two", encoding="utf-8")
        assert resolve_soul().revision != first

    def test_nothing_found_is_an_empty_resolution(self, cfg, monkeypatch):
        monkeypatch.setattr(soulmod, "soul_candidates", lambda *a, **k: [])
        monkeypatch.setattr(soulmod, "_persona_soul_yaml", lambda *a, **k: "")
        res = resolve_soul()
        assert res.raw == "" and res.source == "" and res.found is False


class TestOneChainNotTwo:
    def test_conv_soul_delegates_to_the_shared_chain(self, cfg):
        """The regression guard: conv/soul.py must not regrow a private chain."""
        assert convsoul._soul_candidates() == soul_candidates(None, None, None)

    def test_conv_soul_sees_identity_md(self, cfg):
        _write(cfg / "workspace" / "IDENTITY.md", "identity through the live path")
        raw, has_rich, source = convsoul._scan_soul_files()
        assert source == "identity"
        assert has_rich is True
        assert raw == "identity through the live path"

    def test_identity_is_injected_verbatim(self, cfg):
        _write(cfg / "workspace" / "IDENTITY.md", "verbatim identity text")
        assert convsoul.load_soul_content() == "verbatim identity text"

    def test_shipped_default_still_condenses_to_rich_identity(self, cfg):
        """Back-compat: an install with no user files behaves exactly as before."""
        assert convsoul.load_soul_content() == convsoul._RICH_IDENTITY

    def test_workspace_soul_still_wins_verbatim(self, cfg):
        _write(cfg / "workspace" / "SOUL.md", "legacy operator soul")
        assert convsoul.load_soul_content() == "legacy operator soul"

    def test_context_fallback_is_not_rich(self, cfg, monkeypatch):
        ctx_file = _write(cfg / "ctx" / "SOUL.md", "z" * 3000)
        monkeypatch.setattr(convsoul, "_soul_candidates", lambda *a, **k: [(ctx_file, "context")])
        raw, has_rich, source = convsoul._scan_soul_files()
        assert (has_rich, source) == (False, "context")
        assert convsoul._condense_soul(raw, has_rich, source) == raw[:2000]
