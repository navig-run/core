"""Unit tests for the community-asset install spec parser (no network)."""

import pytest

from navig.commands.install import _parse_spec

pytestmark = pytest.mark.integration


def test_community_skill_path():
    info = _parse_spec("github:navig-run/community/cli-skills/developer/git-ops")
    assert info["type"] == "skill"
    assert info["owner"] == "navig-run"
    assert info["repo"] == "community"
    assert info["subpath"] == ["cli-skills", "developer", "git-ops"]
    assert info["id"] == "git-ops"
    assert info["ref"] == "main"


def test_community_space_path_infers_space_type():
    info = _parse_spec("github:navig-run/community/spaces/homelab-space")
    assert info["type"] == "space"
    assert info["id"] == "homelab-space"


def test_typed_whole_repo_spec():
    info = _parse_spec("skill:myuser/my-skill")
    assert info["type"] == "skill"
    assert info["owner"] == "myuser"
    assert info["repo"] == "my-skill"
    assert info["subpath"] == []
    assert info["id"] == "my-skill"


def test_ref_is_parsed():
    info = _parse_spec("space:owner/repo@v1.2.0")
    assert info["type"] == "space"
    assert info["ref"] == "v1.2.0"
    assert info["id"] == "repo"


def test_default_type_override():
    info = _parse_spec("github:owner/repo", default_type="space")
    assert info["type"] == "space"


def test_invalid_spec_raises():
    with pytest.raises(ValueError):
        _parse_spec("not-a-valid-spec")
