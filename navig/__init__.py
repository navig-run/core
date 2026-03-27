"""
NAVIG - No Admin Visible In Graveyard

Keep your servers alive. Forever.
"""

from __future__ import annotations

from pathlib import Path


def _resolve_version() -> str:
    """Resolve the package version from the active project metadata.

    Preference order:
    1. Local ``pyproject.toml`` when running from a source checkout.
    2. Installed package metadata when running from a wheel/site-packages.
    3. A safe semver fallback so CLI version output never goes blank.
    """

    project_file = Path(__file__).resolve().parents[1] / "pyproject.toml"
    if project_file.exists():
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
            pass

    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("navig")
    except Exception:
        return "0.0.0"


__version__ = _resolve_version()
__author__ = "NAVIG Development Team"
