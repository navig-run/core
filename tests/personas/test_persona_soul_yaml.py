"""Regression: community personas ship a rich ``soul.yaml`` (registry schema)
instead of ``persona.yaml`` + ``soul.md``. The loader must read soul.yaml so
``navig persona use <community-persona>`` loads real identity / tone / soul
instead of an empty default shell.

Before the fix, load_persona() on a soul.yaml-only persona returned a default
PersonaConfig (display_name capitalized from the slug, tone="warm") and an empty
soul string — the community persona's content was silently dropped.
"""
from __future__ import annotations

import pytest

import navig.personas.loader as loader
import navig.personas.resolver as resolver
from navig.personas.loader import _normalize_tone, load_persona, read_soul_yaml

# A representative registry persona (rich soul.yaml, no persona.yaml/soul.md).
SENIOR_ENGINEER_SOUL_YAML = (
    'id: senior-engineer\n'
    'version: "1.0.0"\n'
    'archetype: specialist\n'
    'extends: null\n'
    'identity:\n'
    '  name: "Senior Engineer"\n'
    '  tagline: "Precision-driven systems thinker"\n'
    '  description: |\n'
    '    Battle-tested engineer. Root-cause analysis, clean architecture.\n'
    'tone:\n'
    '  primary: technical\n'
    '  formality: medium\n'
    '  format: |\n'
    '    Concise, direct. Code blocks with language tags.\n'
    'capabilities:\n'
    '  - code_review\n'
)


def _make_persona(tmp_path, name, files):
    d = tmp_path / name
    d.mkdir()
    for filename, text in files.items():
        (d / filename).write_text(text, encoding="utf-8")
    return d


@pytest.fixture
def patch_resolver(monkeypatch):
    """Point persona resolution at an in-test mapping.

    load_persona() re-imports resolve_persona from the resolver module, while
    _load_raw_chain uses loader's module-level binding — patch both.
    """
    mapping: dict = {}

    def _resolve(name, cwd=None):
        return mapping.get(name)

    monkeypatch.setattr(resolver, "resolve_persona", _resolve)
    monkeypatch.setattr(loader, "resolve_persona", _resolve)
    return mapping


class TestReadSoulYaml:
    def test_returns_none_when_absent(self, tmp_path):
        d = _make_persona(tmp_path, "x", {"persona.yaml": "display_name: X"})
        assert read_soul_yaml(d) is None

    def test_maps_identity_and_tone(self, tmp_path):
        d = _make_persona(tmp_path, "se", {"soul.yaml": SENIOR_ENGINEER_SOUL_YAML})
        data, soul = read_soul_yaml(d)
        assert data["display_name"] == "Senior Engineer"
        assert data["tone"] == "direct"        # technical -> direct
        assert data["soul_extends"] == ""      # extends: null must not become "None"
        assert "Senior Engineer" in soul
        assert "Precision-driven systems thinker" in soul  # tagline
        assert "Battle-tested" in soul                     # description

    def test_extends_null_is_empty_not_none_string(self, tmp_path):
        d = _make_persona(tmp_path, "se", {"soul.yaml": SENIOR_ENGINEER_SOUL_YAML})
        data, _ = read_soul_yaml(d)
        assert data["soul_extends"] != "None"

    def test_invalid_yaml_raises(self, tmp_path):
        d = _make_persona(tmp_path, "bad", {"soul.yaml": "identity: [unclosed"})
        with pytest.raises(ValueError):
            read_soul_yaml(d)


class TestNormalizeTone:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("technical", "direct"),
            ("analytical", "direct"),
            ("professional", "formal"),
            ("friendly", "warm"),
            ("witty", "playful"),
            ("philosophical", "philosophical"),
            ("direct", "direct"),
            ("warm", "warm"),
            ("something-unknown", "warm"),
            ("", "warm"),
            (None, "warm"),
        ],
    )
    def test_tone_mapping(self, raw, expected):
        result = _normalize_tone(raw)
        assert result in {"direct", "warm", "playful", "formal", "philosophical"}
        assert result == expected


class TestLoadPersonaCommunityFormat:
    def test_soul_yaml_only_loads_real_identity(self, tmp_path, patch_resolver):
        # The reported break: this used to return an empty default shell.
        d = _make_persona(tmp_path, "senior-engineer", {"soul.yaml": SENIOR_ENGINEER_SOUL_YAML})
        patch_resolver["senior-engineer"] = d
        cfg, soul = load_persona("senior-engineer")
        assert cfg.display_name == "Senior Engineer"
        assert cfg.tone == "direct"
        assert len(soul) > 0
        assert "Senior Engineer" in soul

    def test_soul_md_wins_over_soul_yaml_for_content(self, tmp_path, patch_resolver):
        d = _make_persona(
            tmp_path,
            "nav",
            {"soul.yaml": SENIOR_ENGINEER_SOUL_YAML, "soul.md": "# Hand-written soul body"},
        )
        patch_resolver["nav"] = d
        cfg, soul = load_persona("nav")
        assert cfg.display_name == "Senior Engineer"   # config still from soul.yaml
        assert soul == "# Hand-written soul body"       # content from soul.md

    def test_persona_yaml_still_supported(self, tmp_path, patch_resolver):
        d = _make_persona(
            tmp_path,
            "legacy",
            {"persona.yaml": "display_name: Legacy Bot\ntone: playful\n", "soul.md": "# Legacy soul"},
        )
        patch_resolver["legacy"] = d
        cfg, soul = load_persona("legacy")
        assert cfg.display_name == "Legacy Bot"
        assert cfg.tone == "playful"
        assert soul == "# Legacy soul"


class TestMalformedSoulYaml:
    """A malformed community soul.yaml must degrade, never crash the loader."""

    def test_scalar_identity_does_not_raise(self, tmp_path):
        # `identity:` authored as a scalar instead of a mapping — must not AttributeError.
        d = _make_persona(tmp_path, "bad", {"soul.yaml": 'id: bad\nidentity: "a string"\ntone: warm\n'})
        result = read_soul_yaml(d)
        assert result is not None
        data, _soul = result
        assert data["display_name"] == "bad"   # falls back to id
        assert data["tone"] == "warm"

    def test_scalar_tone_does_not_raise(self, tmp_path):
        # `tone:` authored as a scalar instead of a block.
        d = _make_persona(tmp_path, "bad2", {"soul.yaml": "id: bad2\nidentity:\n  name: X\ntone: friendly\n"})
        data, _soul = read_soul_yaml(d)
        assert data["display_name"] == "X"
        assert data["tone"] == "warm"          # no tone.primary -> default

    def test_list_top_level_returns_none(self, tmp_path):
        d = _make_persona(tmp_path, "listy", {"soul.yaml": "- a\n- b\n"})
        assert read_soul_yaml(d) is None
