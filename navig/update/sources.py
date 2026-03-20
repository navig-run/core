"""Update source adapters for navig update.

Each source knows how to discover the latest available version.
The factory ``build_source()`` constructs the right source from config.
"""
from __future__ import annotations

import re
import subprocess
from typing import Any, Dict, Optional


class SourceError(Exception):
    """Raised when a source cannot determine the latest version."""


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class _BaseSource:
    @property
    def label(self) -> str:
        return self.__class__.__name__

    def latest_version(self) -> str:  # pragma: no cover
        raise NotImplementedError


# ---------------------------------------------------------------------------
# PyPI
# ---------------------------------------------------------------------------

class PyPISource(_BaseSource):
    """Fetch latest version from PyPI."""

    def __init__(self, package: str = "navig", channel: str = "stable"):
        self._package = package
        self._channel = channel

    @property
    def label(self) -> str:
        suffix = f" ({self._channel})" if self._channel != "stable" else ""
        return f"PyPI{suffix}"

    def latest_version(self) -> str:
        import json
        import urllib.request
        url = f"https://pypi.org/pypi/{self._package}/json"
        try:
            with urllib.request.urlopen(url, timeout=10) as resp:
                data = json.loads(resp.read())
        except Exception as exc:
            raise SourceError(f"PyPI request failed: {exc}") from exc

        if self._channel == "stable":
            v = data.get("info", {}).get("version")
            if not v:
                raise SourceError("PyPI response missing 'version' field")
            return v

        # beta / nightly — pick latest pre-release
        try:
            from packaging.version import Version  # type: ignore
        except ImportError:
            # Fallback: return latest stable
            return data.get("info", {}).get("version", "unknown")

        releases = data.get("releases", {})
        candidates = []
        for tag in releases:
            try:
                v = Version(tag)
                if v.is_prerelease:
                    candidates.append(v)
            except Exception:  # noqa: BLE001
                pass  # best-effort; failure is non-critical
        if not candidates:
            raise SourceError("No pre-release versions found on PyPI")
        return str(max(candidates))


# ---------------------------------------------------------------------------
# GitHub Releases
# ---------------------------------------------------------------------------

class GitHubSource(_BaseSource):
    """Fetch latest release tag from GitHub."""

    def __init__(self, repo: str = "navig-os/navig-core", token: Optional[str] = None):
        self._repo = repo
        self._token = token

    @property
    def label(self) -> str:
        return f"GitHub ({self._repo})"

    def latest_version(self) -> str:
        import json
        import urllib.request
        url = f"https://api.github.com/repos/{self._repo}/releases/latest"
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                                    "User-Agent": "navig-update/1.0"})
        if self._token:
            req.add_header("Authorization", f"Bearer {self._token}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
        except Exception as exc:
            raise SourceError(f"GitHub request failed: {exc}") from exc

        tag = data.get("tag_name", "")
        if not tag:
            raise SourceError("GitHub response missing 'tag_name'")
        return tag.lstrip("v")


# ---------------------------------------------------------------------------
# Git Repo (local or remote)
# ---------------------------------------------------------------------------

class GitRepoSource(_BaseSource):
    """Inspect git tags in a local or remote repo to find the latest version."""

    def __init__(self, repo_path: str = ".", remote: Optional[str] = None):
        self._path = repo_path
        self._remote = remote

    @property
    def label(self) -> str:
        return f"git ({self._path})"

    def latest_version(self) -> str:
        if self._remote:
            cmd = ["git", "ls-remote", "--tags", "--sort=version:refname", self._remote]
        else:
            cmd = ["git", "-C", self._path, "tag", "--sort=version:refname"]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if r.returncode != 0:
                raise SourceError(f"git command failed: {r.stderr.strip()[:120]}")
        except FileNotFoundError as _exc:
            raise SourceError("git not found in PATH") from _exc
        except subprocess.TimeoutExpired as _exc:
            raise SourceError("git command timed out") from _exc

        lines = r.stdout.strip().splitlines()
        version_tags = []
        for line in lines:
            tag = line.split("/")[-1].strip()
            tag = tag.lstrip("v")
            if re.match(r"^\d+\.\d+", tag):
                version_tags.append(tag)
        if not version_tags:
            raise SourceError("No version tags found in git repo")
        return version_tags[-1]


# ---------------------------------------------------------------------------
# Artifact URL
# ---------------------------------------------------------------------------

class ArtifactURLSource(_BaseSource):
    """Fetch version string from an arbitrary URL (JSON or plain text)."""

    def __init__(self, url: str, json_key: str = "version"):
        self._url = url
        self._json_key = json_key

    @property
    def label(self) -> str:
        return f"artifact ({self._url[:40]}...)"

    def latest_version(self) -> str:
        import json as _json
        import urllib.request
        try:
            with urllib.request.urlopen(self._url, timeout=10) as resp:
                body = resp.read().decode("utf-8", errors="replace")
        except Exception as exc:
            raise SourceError(f"URL request failed: {exc}") from exc

        try:
            data = _json.loads(body)
            v = data.get(self._json_key)
            if v:
                return str(v).lstrip("v")
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical

        # Plain text / regex fallback
        match = re.search(r"\d+\.\d+\.\d+", body)
        if match:
            return match.group(0)
        raise SourceError(f"Could not parse version from URL response: {body[:80]}")


# ---------------------------------------------------------------------------
# Local File
# ---------------------------------------------------------------------------

class LocalFileSource(_BaseSource):
    """Read version from a local plain-text file."""

    def __init__(self, path: str):
        self._path = path

    @property
    def label(self) -> str:
        return f"file ({self._path})"

    def latest_version(self) -> str:
        try:
            from pathlib import Path
            return Path(self._path).read_text(encoding="utf-8").strip().lstrip("v")
        except Exception as exc:
            raise SourceError(f"Cannot read version file '{self._path}': {exc}") from exc


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def build_source(cfg: Dict[str, Any], channel: str = "stable") -> _BaseSource:
    """Construct a source from a config dict (from defaults.yaml ``update.source``)."""
    src_type = (cfg.get("type") or "pypi").lower()

    if src_type == "pypi":
        return PyPISource(package=cfg.get("package", "navig"), channel=channel)

    if src_type == "github":
        return GitHubSource(repo=cfg.get("repo", "navig-os/navig-core"),
                             token=cfg.get("token"))

    if src_type == "git-repo":
        return GitRepoSource(repo_path=cfg.get("path", "."),
                              remote=cfg.get("remote"))

    if src_type == "artifact-url":
        return ArtifactURLSource(url=cfg["url"],
                                  json_key=cfg.get("json_key", "version"))

    if src_type == "local-file":
        return LocalFileSource(path=cfg["path"])

    raise ValueError(f"Unknown update source type: '{src_type}'. "
                     "Valid: pypi, github, git-repo, artifact-url, local-file")
