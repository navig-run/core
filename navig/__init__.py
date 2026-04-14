"""
NAVIG - No Admin Visible In Graveyard

Keep your servers alive. Forever.
"""

from __future__ import annotations

import warnings

# Suppress spurious urllib3/charset-normalizer version mismatch warnings that
# fire when those packages are newer than what the installed requests wheel was
# built against. The warnings are harmless — requests still works correctly.
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
    """Resolve the package version.

    Preference order:
    1. Source-checkout ``pyproject.toml`` adjacent to this file.
    2. Installed package metadata (fast path — used in wheel/site-packages installs).
    3. Safe semver fallback so CLI version output never goes blank.
    """
    # Source checkout path: prefer pyproject so local version stays in sync,
    # even when an older editable-install metadata entry is present.
    project_file = Path(__file__).resolve().parents[1] / "pyproject.toml"
    if project_file.exists():
        try:
            try:
                import tomllib
            except ModuleNotFoundError:
                import tomli as tomllib  # type: ignore[no-redef]

            data = tomllib.loads(project_file.read_text(encoding="utf-8"))
            version_str = data.get("project", {}).get("version")
            if isinstance(version_str, str) and version_str.strip():
                return version_str.strip()
        except Exception:
            pass  # pyproject.toml unreadable or missing version field

    # Installed wheel / editable install metadata fallback
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("navig")
    except PackageNotFoundError:
        pass

    return "0.0.0"


__version__ = _resolve_version()
__author__ = "NAVIG Development Team"
