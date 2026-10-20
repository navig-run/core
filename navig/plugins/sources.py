"""Which editable-declared plugins actually resolve to your source tree.

``core/pyproject.toml``'s ``[tool.uv.sources]`` declares a set of plugins as
``editable = true`` local paths. When one of them resolves to a NON-editable copy in
site-packages instead, the CLI executes code that exists nowhere in the repo — while the
test suites keep reading the repo, because the gate runs ``python -m pytest`` with the
plugin directory as cwd and ``-m`` puts the source on ``sys.path`` first.

Measured 2026-09-06: 4 of 12 declared plugins were shadowed, and the mobile plugin's
``ui`` sub-app answered "No such command" for a group registered unconditionally in the
source — while that plugin's own 39 tests passed and ``navig plugin list`` printed a green
tick over it.

This module is the single source of that answer. ``navig doctor`` renders it as a health
section; ``navig plugin list`` renders it as a per-row state. Two surfaces, one rule — a
second copy of this logic is how they would come to disagree.

⚠ Deliberately NOT cached. A ``@lru_cache`` here would be read once per process and then
be wrong for the rest of it, and the identical pattern in
``adapters/automation/powershell`` leaked a fake binary between tests until it was fixed.
The audit is a handful of ``find_spec`` calls against top-level packages, which resolve
from the file tree without executing any plugin's module body.
"""

from __future__ import annotations

import importlib.metadata
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "PluginSourceAudit",
    "audit_plugin_sources",
    "editable_install_command",
    "is_plugin_source_tree",
    "dead_editables",
    "installed_navig_records",
    "uninstall_command",
    "with_local_dependencies",
]


@dataclass(frozen=True)
class PluginSourceAudit:
    """The outcome of comparing declared plugin sources against what imports resolve to."""

    declared: int
    verified: tuple[str, ...] = ()
    shadowed: tuple[str, ...] = ()
    #: ``(dist, reason)`` — counted, never guessed at. "Not installed" is not a failure
    #: (an optional extra may simply be absent) but it is also not something to tick.
    unresolved: tuple[tuple[str, str], ...] = ()
    #: Repo-relative paths for the ``pip install -e`` remedy, in ``shadowed`` order.
    fix_paths: tuple[str, ...] = field(default=())
    #: ``(dist, installed, source)`` — editable, resolves to your source, but the version
    #: pip recorded at install time is not the one the source declares now.
    stale: tuple[tuple[str, str, str], ...] = ()
    #: The subset of ``stale`` whose recorded version no longer satisfies core's own
    #: requirement on it — the next ``pip install -e core`` replaces it from PyPI.
    below_floor: tuple[str, ...] = ()
    #: Repo-relative paths for every stale plugin, in ``stale`` order, followed by the
    #: locally-declared packages they depend on. They are one remedy, not several: see
    #: :func:`audit_plugin_sources` and :func:`with_local_dependencies`.
    stale_fix_paths: tuple[str, ...] = ()
    #: ``(dist, path)`` — any navig package installed editable from a folder that no
    #: longer exists. Not limited to declared sources: a retired package is exactly
    #: the one nothing declares any more.
    dead: tuple[tuple[str, str], ...] = ()

    @property
    def ok(self) -> bool:
        """True when every declared plugin resolves to a source tree."""
        return not self.shadowed


def editable_install_command(
    paths: list[str] | tuple[str, ...], executable: str | None = None
) -> str:
    """The pip command that installs every path in ``paths`` editable, in ONE resolve.

    ``-e`` binds to the single argument after it: ``pip install -e a b`` installs ``a``
    editable and ``b`` as an ordinary copy — which is precisely the shadowing this module
    reports. Every path gets its own ``-e``.

    Spelled with the running interpreter rather than a bare ``pip``: with two Pythons on
    PATH, a bare ``pip`` repairs whichever one it happens to belong to, which need not be
    the one ``navig`` runs under. ``executable`` names a different interpreter when the
    repair is for one other than the running Python.
    """
    import sys  # noqa: PLC0415

    exe = executable or sys.executable or "python"
    if " " in exe:
        exe = f'"{exe}"'
    return f"{exe} -m pip install " + " ".join(f"-e {path}" for path in paths)


def installed_navig_records() -> dict[str, list]:
    """``{dist: [version, direct_url_json]}`` for every navig package in THIS Python.

    The same shape the other-interpreter probe reports, so one judge serves both.
    """
    out: dict[str, list] = {}
    for d in importlib.metadata.distributions():
        name = (d.metadata["Name"] or "").lower()
        if name == "navig" or name.startswith("navig-"):
            out[name] = [d.version, d.read_text("direct_url.json")]
    return out


def _editable_dir(direct_url: str | None) -> Path | None:
    """The folder an editable install points at, or None when it is not editable."""
    import json  # noqa: PLC0415
    from urllib.parse import unquote, urlparse  # noqa: PLC0415

    if not direct_url:
        return None
    try:
        info = json.loads(direct_url)
    except ValueError:
        return None
    if not (info.get("dir_info") or {}).get("editable"):
        return None
    url = urlparse(info.get("url") or "")
    if url.scheme != "file":
        return None
    raw = unquote(url.path)
    # file:///E:/x parses to "/E:/x" — drop the slash before a drive letter.
    if len(raw) > 2 and raw[0] == "/" and raw[2] == ":":
        raw = raw[1:]
    return Path(raw)


def dead_editables(records: dict[str, list]) -> tuple[tuple[str, str], ...]:
    """``(dist, path)`` for every editable install whose source folder is gone.

    Measured 2026-10-09: navig-paperwork — folded into navig-cabinet weeks earlier —
    was still installed in the 3.14 interpreter, editable from a deleted
    ``.dev/worktrees/paperwork`` checkout. It kept registering a ``paperwork`` command
    whose module could not be imported, under the same name cabinet registers. No row
    showed it: every check compared DECLARED sources, and a retired package is
    precisely the one nothing declares any more.
    """
    dead: list[tuple[str, str]] = []
    for dist, record in sorted(records.items()):
        where = _editable_dir(record[1] if len(record) > 1 else None)
        if where is not None and not where.exists():
            dead.append((dist, str(where)))
    return tuple(dead)


def uninstall_command(dists: list[str] | tuple[str, ...], executable: str | None = None) -> str:
    """``<python> -m pip uninstall -y <dists>`` for the given interpreter."""
    import sys  # noqa: PLC0415

    exe = executable or sys.executable or "python"
    if " " in exe:
        exe = f'"{exe}"'
    return f"{exe} -m pip uninstall -y " + " ".join(dists)


def with_local_dependencies(
    paths: list[str] | tuple[str, ...], root: Path | None = None
) -> tuple[str, ...]:
    """``paths`` plus every locally-declared package they depend on, transitively.

    A remedy that lists only the packages being repaired can still reopen the trap it
    repairs: measured 2026-10-03, refreshing navig-download pulled in its new dependency
    navig-browser — not yet installed there — from PyPI, as an ordinary copy, because
    pip only prefers a local path for what it is resolving in the same call. Any
    dependency ``[tool.uv.sources]`` declares local therefore joins the command.
    Re-installing one that is already current is a harmless refresh.

    Order: the given paths first, as given; dependencies after, in discovery order.
    """
    if root is None:
        from navig.commands.repo import resolve_repo_root  # noqa: PLC0415

        root = resolve_repo_root()
    if root is None:
        return tuple(paths)
    pyproject = root / "core" / "pyproject.toml"
    declared = _declared_local_plugins(pyproject) if pyproject.is_file() else None
    if not declared:
        return tuple(paths)

    def _rel(source: Path) -> str:
        try:
            return source.relative_to(root).as_posix()
        except ValueError:
            return str(source)

    local = {
        dist.lower(): _rel((pyproject.parent / rel).resolve()) for dist, rel in declared.items()
    }
    result = list(dict.fromkeys(paths))
    queue = list(result)
    while queue:
        for name in _hard_dependency_names(root / queue.pop(0)):
            path = local.get(name)
            if path and path not in result:
                result.append(path)
                queue.append(path)
    return tuple(result)


def _hard_dependency_names(source: Path) -> list[str]:
    """Normalised names of a source tree's ``[project].dependencies`` (extras excluded)."""
    try:
        import tomllib  # noqa: PLC0415
        from packaging.requirements import InvalidRequirement, Requirement  # noqa: PLC0415

        project = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8")).get(
            "project", {}
        )
    except (ImportError, OSError, ValueError):
        return []
    names: list[str] = []
    for line in project.get("dependencies", []) or []:
        try:
            names.append(Requirement(line).name.lower())
        except InvalidRequirement:
            continue
    return names


def is_plugin_source_tree(
    origin: Path, source_dir_name: str, container: str = "plugins"
) -> bool:
    """Does ``origin`` live inside a ``plugins/<source_dir_name>/`` checkout?

    Deliberately root-AGNOSTIC. An editable install points at ONE checkout, and a command
    may run from a ``.dev/worktrees/<slug>`` copy of the same repo — comparing against the
    current root reported 11 of 12 plugins shadowed on a machine where the real answer was
    4, because seven correct installs pointed at the main checkout. The question worth
    answering is source-tree vs installed-copy, not which checkout.

    ``container`` is the directory the source sits in. It is ``plugins`` for a plugin,
    but not for every declared source: navig-sdk lives at ``registry/sdk/python``, and
    hard-coding ``plugins`` reported that editable install as shadowed — and so left it
    out of the stale-install remedy, the one place it has to be.
    """
    for parent in origin.parents:
        if parent.name == source_dir_name and parent.parent.name == container:
            return True
    return False


def _declared_local_plugins(pyproject: Path) -> dict[str, str] | None:
    """``{dist: relative path}`` from ``[tool.uv.sources]``; None if unreadable."""
    try:
        import tomllib  # noqa: PLC0415 - py3.11+
    except ImportError:
        # No TOML reader: report NOTHING rather than an answer we cannot back.
        return None
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    sources = (data.get("tool", {}).get("uv", {}).get("sources", {}) or {}).items()
    return {
        dist: spec["path"]
        for dist, spec in sources
        if isinstance(spec, dict) and isinstance(spec.get("path"), str)
    }


def _source_version(source: Path) -> str | None:
    """The static ``[project].version`` a plugin's source declares, or None."""
    try:
        import tomllib  # noqa: PLC0415 - py3.11+

        data = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
    except (ImportError, OSError, ValueError):
        return None
    version = data.get("project", {}).get("version")
    return version if isinstance(version, str) else None


def _core_requirements(pyproject: Path) -> dict[str, list]:
    """``{dist: [Requirement, ...]}`` for every first-party package core asks for.

    Hard dependencies AND extras: ``pip install -e core[github]`` re-resolves an extra's
    floor exactly as a hard dependency's. Empty when ``packaging`` is unavailable — no
    floor verdict is better than a guessed one.
    """
    try:
        import tomllib  # noqa: PLC0415
        from packaging.requirements import InvalidRequirement, Requirement  # noqa: PLC0415

        project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {})
    except (ImportError, OSError, ValueError):
        return {}
    raw = list(project.get("dependencies", []) or [])
    for extra in (project.get("optional-dependencies", {}) or {}).values():
        raw.extend(extra or [])
    out: dict[str, list] = {}
    for line in raw:
        try:
            req = Requirement(line)
        except InvalidRequirement:
            continue
        out.setdefault(req.name.lower(), []).append(req)
    return out


def _below_floor(installed: str, requirements: list) -> bool:
    try:
        return any(
            not req.specifier.contains(installed, prereleases=True) for req in requirements
        )
    except Exception:  # noqa: BLE001 - an unparsable version is not a verdict
        return False


def _package_dir_name(source: Path) -> str | None:
    """The ``navig_*`` package directory inside a plugin's source checkout."""
    try:
        return next(
            (
                child.name
                for child in sorted(source.iterdir())
                if child.is_dir()
                and child.name.startswith("navig")
                and (child / "__init__.py").is_file()
            ),
            None,
        )
    except OSError:
        return None


def audit_plugin_sources(root: Path | None = None) -> PluginSourceAudit | None:
    """Compare declared editable plugin sources against what imports resolve to.

    It also finds the quieter drift: an editable install that DOES point at your source
    but still carries the version pip recorded when it was made. Bumping a plugin's
    version and core's floor on it together — the normal release move — leaves that
    record behind, and the next ``pip install -e core`` "upgrades" the plugin with the
    PyPI wheel, silently replacing your source with a published copy. Measured
    2026-09-30: 19 of 24 first-party installs were stale, two already under core's floor.

    Stale plugins get ONE remedy covering all of them. pip only prefers a local path
    over PyPI for the requirements it is resolving in the same call, so refreshing them
    one at a time lets a still-stale sibling (``navig-sdk``, say) be fetched from PyPI
    mid-repair.

    Returns None when there is nothing to compare against — no repo, no
    ``core/pyproject.toml``, an unreadable one, or no local sources declared. An end user
    who installed from PyPI is in that case, so every caller renders nothing rather than a
    verdict it cannot support.
    """
    if root is None:
        from navig.commands.repo import resolve_repo_root  # noqa: PLC0415

        root = resolve_repo_root()
    if root is None:
        return None

    pyproject = root / "core" / "pyproject.toml"
    if not pyproject.is_file():
        return None

    declared = _declared_local_plugins(pyproject)
    if not declared:
        return None

    verified: list[str] = []
    shadowed: list[str] = []
    unresolved: list[tuple[str, str]] = []
    fix_paths: list[str] = []
    stale: list[tuple[str, str, str]] = []
    below_floor: list[str] = []
    stale_fix_paths: list[str] = []
    floors = _core_requirements(pyproject)

    def _rel(source: Path) -> str:
        try:
            return source.relative_to(root).as_posix()
        except ValueError:
            return str(source)

    for dist, rel in sorted(declared.items()):
        source = (pyproject.parent / rel).resolve()
        if not source.is_dir():
            unresolved.append((dist, "declared path missing"))
            continue
        package = _package_dir_name(source)
        if package is None:
            unresolved.append((dist, "no package dir"))
            continue
        try:
            spec = importlib.util.find_spec(package)
        except (ImportError, ValueError):
            spec = None
        origin = getattr(spec, "origin", None) if spec is not None else None
        if not origin:
            unresolved.append((dist, "not installed"))
            continue
        try:
            inside = is_plugin_source_tree(
                Path(origin).resolve(), source.name, source.parent.name
            )
        except (OSError, ValueError):
            unresolved.append((dist, "unreadable origin"))
            continue
        if inside:
            verified.append(dist)
            try:
                installed = importlib.metadata.version(dist)
            except importlib.metadata.PackageNotFoundError:
                continue
            declared_version = _source_version(source)
            if declared_version and installed != declared_version:
                stale.append((dist, installed, declared_version))
                stale_fix_paths.append(_rel(source))
                if _below_floor(installed, floors.get(dist.lower(), [])):
                    below_floor.append(dist)
        else:
            shadowed.append(dist)
            fix_paths.append(_rel(source))

    return PluginSourceAudit(
        declared=len(declared),
        verified=tuple(verified),
        shadowed=tuple(shadowed),
        unresolved=tuple(unresolved),
        fix_paths=tuple(fix_paths),
        stale=tuple(stale),
        below_floor=tuple(below_floor),
        stale_fix_paths=with_local_dependencies(stale_fix_paths, root)
        if stale_fix_paths
        else (),
        dead=dead_editables(installed_navig_records()),
    )
