"""Unit tests for the community-asset install spec parser (no network)."""

import pytest

from navig.commands.install import _parse_spec

pytestmark = pytest.mark.integration


def test_community_skill_path():
    info = _parse_spec("github:navig-run/registry/cli-skills/developer/git-ops")
    assert info["type"] == "skill"
    assert info["owner"] == "navig-run"
    assert info["repo"] == "registry"
    assert info["subpath"] == ["cli-skills", "developer", "git-ops"]
    assert info["id"] == "git-ops"
    assert info["ref"] == "main"


def test_community_space_path_infers_space_type():
    info = _parse_spec("github:navig-run/registry/spaces/homelab-space")
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


def test_the_deleted_community_repo_resolves_to_the_registry():
    """`navig-run/community` was deleted (no GitHub redirect) and replaced by
    `navig-run/registry` on 2026-10-16; specs naming the old repo still install."""
    for spec in (
        "github:navig-run/community/spaces/homelab-space",
        "block:navig-run/community/blocks/hello",
        "github:Navig-Run/Community/cli-skills/developer/git-ops@v1",
    ):
        info = _parse_spec(spec)
        assert info["repo"] == "registry", spec


def test_a_third_party_repo_named_community_is_left_alone():
    info = _parse_spec("github:someone/community/spaces/x")
    assert info["owner"] == "someone"
    assert info["repo"] == "community"
