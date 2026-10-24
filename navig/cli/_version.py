"""The CLI's version, resolved WITHOUT importing the package ROOT.

Why this exists (measured 2026-10-19): a directory literally named ``navig`` in the
working directory (``C:\\studio\\navig``, a space's asset folder) makes Python build a
**namespace** package for the root — ``navig.__file__`` is ``None`` and it has no
attributes — while every submodule still resolves through the editable install. So
``python -m navig …`` launched from that cwd died on ``from navig import __version__``
alone: ``AttributeError``-free, one import, every command. NAVIG's own spawns
(``skills`` / ``suggest`` / ``triggers`` / ``history``) inherit the caller's cwd, and a
console-script run is unaffected because its ``sys.path[0]`` is the Scripts directory.

The root's ``_resolve_version()`` cannot be imported in that state — it lives in the
module that failed to import. This mirrors its preference order instead, from a file
that resolves either way.
"""

from __future__ import annotations

from pathlib import Path


def resolve_version() -> str:
    """pyproject beside the checkout → installed metadata → a literal fallback.

    Same order as ``navig.__init__._resolve_version`` (that one stays canonical for
    the package attribute; this one exists only for the shadowed-root case).
    """
    project_file = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if project_file.exists():
        try:
            try:
                import tomllib
            except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
                import tomli as tomllib  # type: ignore[no-redef]

            version_str = tomllib.loads(project_file.read_text(encoding="utf-8")).get(
                "project", {}
            ).get("version")
            if isinstance(version_str, str) and version_str.strip():
                return version_str.strip()
        except Exception:  # noqa: BLE001 — never fail startup over the version string
            pass

    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("navig")
    except PackageNotFoundError:
        return "0.0.0"
