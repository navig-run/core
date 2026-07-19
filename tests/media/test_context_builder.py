"""context_builder — palette + style injection, graceful degradation."""

from __future__ import annotations

from navig.media import context_builder as cb


def test_degrades_gracefully_with_no_project_context(tmp_path):
    out = cb.build_context("pixel octopus", space_dir=tmp_path)
    assert out["enriched_prompt"].startswith("pixel octopus")
    assert out["context"]["raw_prompt"] == "pixel octopus"
    assert out["context"]["palette"] == []
    assert out["context"]["style_note"] is None


def test_placement_and_explicit_palette_are_injected(tmp_path):
    out = cb.build_context(
        "octopus", placement="left box of the login card",
        palette=["#2C8BB7", "#22d3ee"], reference_ref="ref123", space_dir=tmp_path,
    )
    prompt = out["enriched_prompt"]
    assert "Placement: left box of the login card." in prompt
    assert "#2C8BB7" in prompt and "#22d3ee" in prompt
    assert out["context"]["reference"] == "ref123"


def test_reads_palette_from_tokens_css_fallback(tmp_path):
    css = tmp_path / "packages" / "shared" / "navig-tokens"
    css.mkdir(parents=True)
    (css / "index.css").write_text(
        ":root{ --color-primary: #2C8BB7; --color-accent: #22d3ee; }", encoding="utf-8"
    )
    out = cb.build_context("octopus", space_dir=tmp_path)
    assert "#2C8BB7" in out["context"]["palette"]
    assert "#22d3ee" in out["context"]["palette"]


def test_picks_highest_fit_style_note(tmp_path):
    style = tmp_path / "docs" / "inspiration" / "style"
    style.mkdir(parents=True)
    (style / "low.md").write_text(
        "---\nfit: low\n---\n## Reusable prompt / spec\n> low-fit direction\n", encoding="utf-8"
    )
    (style / "high.md").write_text(
        "---\nfit: high\n---\n## Reusable prompt / spec\n> glowing blue compass sigil\n",
        encoding="utf-8",
    )
    out = cb.build_context("octopus", space_dir=tmp_path)
    assert out["context"]["style_note"] == "high"
    assert "glowing blue compass sigil" in out["enriched_prompt"]
