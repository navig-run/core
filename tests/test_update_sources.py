"""Tests for navig.update.sources — update source adapters."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from navig.update.sources import (
    ArtifactURLSource,
    GitHubSource,
    GitRepoSource,
    LocalFileSource,
    PyPISource,
    SourceError,
    build_source,
)

# ---------------------------------------------------------------------------
# PyPISource
# ---------------------------------------------------------------------------


class TestPyPISource:
    def _mock_urlopen(self, body: dict):
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cm)
        cm.__exit__ = MagicMock(return_value=False)
        cm.read.return_value = json.dumps(body).encode()
        return cm

    def test_stable_version(self):
        source = PyPISource(package="navig", channel="stable")
        body = {"info": {"version": "2.5.0"}, "releases": {}}
        with patch("urllib.request.urlopen", return_value=self._mock_urlopen(body)):
            assert source.latest_version() == "2.5.0"

    def test_missing_version_raises(self):
        source = PyPISource(package="navig", channel="stable")
        body = {"info": {}, "releases": {}}
        with patch("urllib.request.urlopen", return_value=self._mock_urlopen(body)):
            with pytest.raises(SourceError, match="missing"):
                source.latest_version()

    def test_network_error_raises(self):
        source = PyPISource()
        with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
            with pytest.raises(SourceError, match="PyPI request failed"):
                source.latest_version()

    def test_label_stable(self):
        assert "PyPI" in PyPISource().label

    def test_label_beta(self):
        assert "beta" in PyPISource(channel="beta").label


# ---------------------------------------------------------------------------
# GitHubSource
# ---------------------------------------------------------------------------


class TestGitHubSource:
    def _mock_urlopen(self, tag: str):
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cm)
        cm.__exit__ = MagicMock(return_value=False)
        cm.read.return_value = json.dumps({"tag_name": tag}).encode()
        return cm

    def test_strips_v_prefix(self):
        source = GitHubSource(repo="org/repo")
        with patch("urllib.request.urlopen", return_value=self._mock_urlopen("v2.6.0")):
            assert source.latest_version() == "2.6.0"

    def test_no_tag_raises(self):
        source = GitHubSource(repo="org/repo")
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cm)
        cm.__exit__ = MagicMock(return_value=False)
        cm.read.return_value = json.dumps({}).encode()
        with patch("urllib.request.urlopen", return_value=cm):
            with pytest.raises(SourceError, match="tag_name"):
                source.latest_version()

    def test_network_error_raises(self):
        source = GitHubSource()
        with patch("urllib.request.urlopen", side_effect=OSError("network")):
            with pytest.raises(SourceError):
                source.latest_version()


# ---------------------------------------------------------------------------
# ArtifactURLSource
# ---------------------------------------------------------------------------


class TestArtifactURLSource:
    def _mock_urlopen(self, body: str):
        cm = MagicMock()
        cm.__enter__ = MagicMock(return_value=cm)
        cm.__exit__ = MagicMock(return_value=False)
        cm.read.return_value = body.encode()
        return cm

    def test_json_response(self):
        source = ArtifactURLSource(url="http://example.com/version.json")
        with patch(
            "urllib.request.urlopen",
            return_value=self._mock_urlopen('{"version": "3.0.0"}'),
        ):
            assert source.latest_version() == "3.0.0"

    def test_plain_text_fallback(self):
        source = ArtifactURLSource(url="http://example.com/version.txt")
        with patch("urllib.request.urlopen", return_value=self._mock_urlopen("3.1.2")):
            assert source.latest_version() == "3.1.2"

    def test_unparseable_raises(self):
        source = ArtifactURLSource(url="http://example.com/bad")
        with patch("urllib.request.urlopen", return_value=self._mock_urlopen("no version here")):
            with pytest.raises(SourceError):
                source.latest_version()


# ---------------------------------------------------------------------------
# LocalFileSource
# ---------------------------------------------------------------------------


class TestLocalFileSource:
    def test_reads_version(self, tmp_path):
        p = tmp_path / "version.txt"
        p.write_text("2.9.1\n")
        assert LocalFileSource(str(p)).latest_version() == "2.9.1"

    def test_strips_v_prefix(self, tmp_path):
        p = tmp_path / "version.txt"
        p.write_text("v2.9.2")
        assert LocalFileSource(str(p)).latest_version() == "2.9.2"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(SourceError, match="Cannot read"):
            LocalFileSource(str(tmp_path / "missing.txt")).latest_version()


# ---------------------------------------------------------------------------
# build_source factory
# ---------------------------------------------------------------------------


class TestBuildSource:
    def test_pypi(self):
        s = build_source({"type": "pypi", "package": "navig"}, channel="stable")
        assert isinstance(s, PyPISource)

    def test_github(self):
        s = build_source({"type": "github", "repo": "org/repo"})
        assert isinstance(s, GitHubSource)

    def test_git_repo(self):
        s = build_source({"type": "git-repo", "path": "."})
        assert isinstance(s, GitRepoSource)

    def test_artifact_url(self):
        s = build_source({"type": "artifact-url", "url": "http://example.com/v.json"})
        assert isinstance(s, ArtifactURLSource)

    def test_local_file(self):
        s = build_source({"type": "local-file", "path": "/tmp/v.txt"})
        assert isinstance(s, LocalFileSource)

    def test_unknown_type_raises(self):
        with pytest.raises(ValueError, match="Unknown update source type"):
            build_source({"type": "ftp"})
