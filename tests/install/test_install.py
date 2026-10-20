"""
Installation smoke tests — verify the navig CLI entry point works correctly
after any install method (pipx, pip, editable).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True)


def test_version_flag():
    """navig --version must exit 0 and produce non-empty output."""
    result = run([sys.executable, "-m", "navig", "--version"])
    assert result.returncode == 0, f"Non-zero exit: {result.stderr}"
    assert result.stdout.strip() != "", "Empty version output"


def test_version_format():
    """navig --version output must end with a valid semver string X.Y.Z."""
    result = run([sys.executable, "-m", "navig", "--version"])
    output = result.stdout.strip()
    # Accept both bare "2.4.14" and prefixed "navig 2.4.14"
    version = output.split()[-1]
    parts = version.split(".")
    assert len(parts) >= 2, f"Expected at least X.Y in version, got: {output}"
    assert all(p.isdigit() for p in parts), f"Non-numeric version component: {output}"


def test_help_flag():
    """navig --help must exit 0 and include usage or the package name."""
    result = run([sys.executable, "-m", "navig", "--help"])
    assert result.returncode == 0, f"Non-zero exit: {result.stderr}"
    combined = (result.stdout + result.stderr).lower()
    assert "usage" in combined or "navig" in combined, (
        f"Expected 'usage' or 'navig' in help output, got:\n{combined[:500]}"
    )


def test_package_version_matches_pyproject():
    """navig.__version__ should stay in sync with pyproject.toml."""
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib

    from navig import __version__

    pyproject = Path(__file__).resolve().parent.parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert __version__ == data["project"]["version"]


def test_install_seeds_live_state_from_published_examples(tmp_path):
    """Published spaces ship state only as `*.example.*` (2026-10-16). Install seeds the
    live file from it so routing works at once; a user's live file is never replaced."""
    from navig.commands.install import _merge_additive

    src = tmp_path / "src"
    (src / ".navig" / "inbox").mkdir(parents=True)
    (src / ".navig" / "memory").mkdir(parents=True)
    (src / ".navig" / "inbox" / "routes.example.yaml").write_text("routes: []\n", encoding="utf-8")
    (src / ".navig" / "memory" / "goals.example.json").write_text("{}", encoding="utf-8")
    (src / "docs").mkdir()
    (src / "docs" / "guide.example.md").write_text("doc", encoding="utf-8")  # not state

    dest = tmp_path / "dest"
    (dest / ".navig" / "memory").mkdir(parents=True)
    (dest / ".navig" / "memory" / "goals.json").write_text('{"mine": 1}', encoding="utf-8")

    _merge_additive(src, dest)

    assert (dest / ".navig" / "inbox" / "routes.yaml").read_text(encoding="utf-8") == "routes: []\n"
    assert (dest / ".navig" / "inbox" / "routes.example.yaml").is_file()
    assert (dest / ".navig" / "memory" / "goals.json").read_text(encoding="utf-8") == '{"mine": 1}'
    assert not (dest / "docs" / "guide.md").exists(), "only state examples are seeded"


def test_an_upgrade_seeds_nothing(tmp_path):
    from navig.commands.install import _merge_additive

    src = tmp_path / "src"
    (src / ".navig" / "inbox").mkdir(parents=True)
    (src / ".navig" / "inbox" / "routes.example.yaml").write_text("routes: []\n", encoding="utf-8")
    dest = tmp_path / "dest"
    dest.mkdir()

    _merge_additive(src, dest, capabilities_only=True)

    assert not (dest / ".navig" / "inbox").exists()
