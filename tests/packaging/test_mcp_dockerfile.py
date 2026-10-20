"""The hosted container must serve the compact surface with remote trust.

Glama (and any MCP directory) builds the repository's Dockerfile; this test is the guard
that the image cannot silently regress to a floating version, the full catalogue, or local
single-operator trust behind a public proxy.
"""

from __future__ import annotations

import re
from pathlib import Path

import tomllib

CORE = Path(__file__).resolve().parents[2]
DOCKERFILE = CORE / "Dockerfile"


def _dockerfile() -> str:
    return DOCKERFILE.read_text(encoding="utf-8")


def test_dockerfile_exists_at_the_mirrored_root() -> None:
    # scripts/mirror-core.mjs publishes core/Dockerfile at the root of navig-run/core,
    # which is the repository Glama builds.
    assert DOCKERFILE.is_file()


def test_image_serves_the_compact_surface() -> None:
    text = _dockerfile()
    assert '"--profile", "default"' in text
    assert "navig mcp serve" in text


def test_image_declares_remote_exposure() -> None:
    text = _dockerfile()
    assert "NAVIG_MCP_EXPOSURE=remote" in text
    assert 'CMD ["--exposure", "remote"]' in text


def test_image_is_pinned_to_the_current_release() -> None:
    dockerfile = _dockerfile()
    pin = re.search(r"ARG NAVIG_VERSION=(\d+\.\d+\.\d+)", dockerfile)
    assert pin, "the image must carry an exact ARG NAVIG_VERSION"
    pyproject = tomllib.loads((CORE / "pyproject.toml").read_text(encoding="utf-8"))
    assert pin.group(1) == pyproject["project"]["version"], (
        "Dockerfile pin and pyproject disagrees — bump both in the release commit"
    )
    assert "navig>=" not in dockerfile, "a floating floor makes hosted rebuilds non-reproducible"


def test_image_runs_as_non_root_with_one_writable_state_dir() -> None:
    text = _dockerfile()
    assert "USER navig" in text
    assert "useradd" in text
    assert 'VOLUME ["/data"]' in text
    assert "NAVIG_CONFIG_DIR=/data" in text and "NAVIG_DATA_DIR=/data" in text
    assert "NAVIG_SKIP_ONBOARDING=1" in text and "NAVIG_NO_TELEMETRY=1" in text
