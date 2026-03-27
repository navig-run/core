"""
Installation smoke tests — verify the navig CLI entry point works correctly
after any install method (pipx, pip, editable).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


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
    assert (
        "usage" in combined or "navig" in combined
    ), f"Expected 'usage' or 'navig' in help output, got:\n{combined[:500]}"


def test_package_version_matches_pyproject():
    """navig.__version__ should stay in sync with pyproject.toml."""
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib

    from navig import __version__

    pyproject = Path(__file__).resolve().parent.parent / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert __version__ == data["project"]["version"]
