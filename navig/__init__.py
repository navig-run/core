"""
NAVIG - No Admin Visible In Graveyard

Keep your servers alive. Forever.
"""

from __future__ import annotations

import warnings

# Suppress spurious requests-version mismatch warning that fires when urllib3
# or charset_normalizer is newer than what the installed requests wheel was
# tested against.  This is harmless – requests still works correctly.
# Two complementary filters:
#   1. Match by message prefix (covers all known message formats).
#   2. Match by the unique message suffix so any future prefix rewording is
#      also caught without requiring a filter update.
warnings.filterwarnings(
    "ignore",
    message=r"urllib3.*|chardet.*|charset_normalizer.*",
    category=Warning,
    module=r"requests",
)
warnings.filterwarnings(
    "ignore",
    message=r".*doesn't match a supported version",
    category=Warning,
    module=r"requests",
)

from pathlib import Path


def _resolve_version() -> str:
    """Resolve the package version from the active project metadata.

    Preference order:
    1. Local ``pyproject.toml`` when running from a source checkout.
    2. Installed package metadata when running from a wheel/site-packages.
    3. A safe semver fallback so CLI version output never goes blank.
    """

    project_candidates = [
        Path.cwd() / "pyproject.toml",
        Path(__file__).resolve().parents[1] / "pyproject.toml",
    ]
    for project_file in project_candidates:
        if not project_file.exists():
            continue
        try:
            try:
                import tomllib
            except ModuleNotFoundError:
                import tomli as tomllib

            data = tomllib.loads(project_file.read_text(encoding="utf-8"))
            version = data.get("project", {}).get("version")
            if isinstance(version, str) and version.strip():
                return version.strip()
        except Exception:
            pass  # best-effort: pyproject.toml unreadable or missing version field

    try:
        from importlib.metadata import PackageNotFoundError, version  # noqa: F401

        return version("navig")
    except Exception:
        return "0.0.0"


__version__ = _resolve_version()
__author__ = "NAVIG Development Team"
