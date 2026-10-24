"""navig installed in OTHER Pythons on this machine — and whether those copies drifted.

``audit_plugin_sources`` answers for the interpreter doctor runs in. On a machine with
several Pythons that is not the whole answer, and here it was not even the interpreter
that mattered most: measured 2026-10-03, ``navig.exe`` and the daemon ran on 3.13 while
the repo guard's Claude hooks (``python scripts/agent-hooks/…``) imported navig under
3.14 — the PATH ``python``. Both copies were editable installs of the same checkout, and
3.14's still carried three plugin records below core's floor after 3.13 had been
repaired. Nothing showed it, because every check looked only at the Python it ran in.

Neither copy can simply be removed: each is load-bearing for a different caller. So the
useful answer is an audit of every interpreter that has navig, with each one's repair
command spelled with its own executable.

Other interpreters are probed in a subprocess (``importlib.metadata`` only — no navig
code is imported there), in parallel, with a short timeout. Not cached, for the same
reason as ``navig.plugins.sources``.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

from navig.plugins.sources import (
    _below_floor,
    _core_requirements,
    _declared_local_plugins,
    _source_version,
    dead_editables,
    with_local_dependencies,
)

__all__ = ["InterpreterAudit", "audit_other_interpreters", "list_interpreters"]

_PROBE_TIMEOUT = 15.0

# Runs inside the OTHER interpreter. Metadata only: importing navig there could execute
# plugin code and would be slow; the dist records are all this needs.
_PROBE = """\
import importlib.metadata as m, json, sys
out = {"__executable__": sys.executable}
for d in m.distributions():
    n = (d.metadata["Name"] or "").lower()
    if n == "navig" or n.startswith("navig-"):
        out[n] = [d.version, d.read_text("direct_url.json")]
print(json.dumps(out))
"""
# The probe reports its own sys.executable because the path it was launched by is not
# an identity: on Windows, WindowsApps/python.EXE and python3.EXE are app-execution
# aliases that start the SAME install as `py -3.14`, and auditing by launch path
# reported that one Python three times.
_EXE_KEY = "__executable__"


@dataclass(frozen=True)
class InterpreterAudit:
    """What one other interpreter's navig install looks like against the source tree."""

    executable: str
    #: ``(dist, installed, source)`` — editable, but recorded at an older version.
    stale: tuple[tuple[str, str, str], ...] = ()
    #: Subset of ``stale`` already below core's floor: the next core install there swaps
    #: them for the PyPI copy.
    below_floor: tuple[str, ...] = ()
    #: Declared editable here, but installed in that interpreter as an ordinary copy.
    shadowed: tuple[str, ...] = ()
    #: Repo-relative paths covering ``stale`` and ``shadowed`` — one remedy, one resolve.
    fix_paths: tuple[str, ...] = ()
    #: ``(dist, path)`` — installed editable from a folder that no longer exists.
    dead: tuple[tuple[str, str], ...] = ()

    @property
    def ok(self) -> bool:
        return not (self.stale or self.shadowed or self.dead)


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def list_interpreters() -> list[str]:
    """Every Python executable this machine offers, the running one included.

    Windows: the ``py`` launcher's registry (``py -0p``) — what ``py -3.x`` resolves
    through — plus whatever ``python`` is on PATH. Elsewhere: PATH only.
    """
    found: list[str] = []
    launcher = shutil.which("py") if os.name == "nt" else None
    if launcher:
        try:
            out = subprocess.run(
                [launcher, "-0p"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            ).stdout
        except (OSError, subprocess.SubprocessError):
            out = ""
        for line in out.splitlines():
            # " -V:3.14 *        C:\...\python.exe"  (the * marks the default)
            parts = line.strip().split(None, 1)
            if len(parts) == 2 and parts[0].startswith("-V:"):
                exe = parts[1].lstrip("* ").strip()
                if exe:
                    found.append(exe)
    for name in ("python", "python3"):
        exe = shutil.which(name)
        if exe:
            found.append(exe)

    seen: set[str] = set()
    unique: list[str] = []
    for exe in found:
        k = _key(exe)
        if k not in seen and _is_readable_file(exe):
            seen.add(k)
            unique.append(exe)
    return unique


def _is_current(exe: str) -> bool:
    """Same install as the running interpreter (python.exe and pythonw.exe alike)."""
    here = Path(sys.executable).resolve().parent
    return _key(str(Path(exe).resolve().parent)) == _key(str(here))


def _probe(exe: str) -> dict[str, list] | None:
    try:
        proc = subprocess.run(
            [exe, "-c", _PROBE],
            capture_output=True,
            text=True,
            # The probe prints json.dumps output, which is ASCII by construction; naming
            # the codec keeps the locale code page out of it all the same.
            encoding="utf-8",
            errors="replace",
            timeout=_PROBE_TIMEOUT,
            check=False,
        )
        return json.loads(proc.stdout or "null") if proc.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def _is_editable(direct_url: str | None) -> bool:
    if not direct_url:
        return False
    try:
        info = json.loads(direct_url)
    except ValueError:
        return False
    return bool(info.get("dir_info", {}).get("editable"))


def _judge(
    exe: str,
    installed: dict[str, list],
    sources: dict[str, Path],
    floors: dict[str, list],
    root: Path,
) -> InterpreterAudit:
    stale: list[tuple[str, str, str]] = []
    below: list[str] = []
    shadowed: list[str] = []
    fix: list[str] = []
    for dist, source in sorted(sources.items()):
        record = installed.get(dist.lower())
        if record is None:
            continue  # not installed there — not this audit's business
        version, direct_url = record
        if not _is_editable(direct_url):
            shadowed.append(dist)
        else:
            declared = _source_version(source)
            if not declared or declared == version:
                continue
            stale.append((dist, version, declared))
            if _below_floor(version, floors.get(dist.lower(), [])):
                below.append(dist)
        try:
            fix.append(source.relative_to(root).as_posix())
        except ValueError:
            fix.append(str(source))
    return InterpreterAudit(
        executable=exe,
        stale=tuple(stale),
        below_floor=tuple(below),
        shadowed=tuple(shadowed),
        fix_paths=with_local_dependencies(fix, root) if fix else (),
        dead=dead_editables(installed),
    )


def audit_other_interpreters(
    root: Path | None = None, interpreters: list[str] | None = None
) -> list[InterpreterAudit] | None:
    """One audit per OTHER interpreter that has navig installed.

    None outside a development checkout (nothing to compare against). Interpreters
    without navig, or that fail to answer the probe, are left out — absence is not drift.
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
    if declared is None:
        return None

    sources = {dist: (pyproject.parent / rel).resolve() for dist, rel in declared.items()}
    sources["navig"] = pyproject.parent.resolve()
    floors = _core_requirements(pyproject)

    candidates = interpreters if interpreters is not None else list_interpreters()
    others = [exe for exe in candidates if not _is_current(exe)]
    if not others:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(others))) as pool:
        probes = list(pool.map(_probe, others))

    audits: list[InterpreterAudit] = []
    seen: set[str] = set()
    for launched, data in zip(others, probes):
        if not data or "navig" not in data:
            continue
        real = str(data.pop(_EXE_KEY, None) or launched)
        if _is_current(real) or _key(real) in seen:
            continue
        seen.add(_key(real))
        audits.append(_judge(real, data, sources, floors, root))
    return audits



def _is_readable_file(exe: str) -> bool:
    r"""Is ``exe`` a file we may stat?

    A candidate under an admin-only directory is a normal case on a locked-down
    machine (``C:\ProgramData\navig\tools\...`` raises WinError 5 for a standard
    user). "Cannot stat it" means "not a usable interpreter here", never a reason to
    fail the whole audit — the doctor must keep reporting the others.
    """
    try:
        return Path(exe).is_file()
    except OSError:
        return False

