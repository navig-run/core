"""Runtime inventory — what the DAEMON's Python has, and what loaded.

Every other plugin diagnostic answers a narrower question: ``sources`` judges the few
plugins ``[tool.uv.sources]`` declares, ``interpreters`` probes OTHER Pythons, ``host`` /
``wire_state`` describe packages already discovered in the interpreter that asked, and the
entry-point seam check LOADS entry points (it imports them). None of them can say "41
first-party source plugins; the gateway's interpreter has 13; 7 loaded in this boot" —
which is exactly how ``navig-msstore`` sat missing from the live brain: measured 2026-10-20
the runtime venv held 13 navig dists while the dev interpreter held 46,
``/api/deck/msstore/*`` answered 404 for days, and every existing row stayed green because
no check asked the daemon's environment anything.

**The daemon's environment is primary.** The gateway runs its own interpreter (here
``~/.navig/runtime/venv``) while ``navig doctor`` runs in whatever Python the caller used;
measuring only the caller's would report ``navig-games`` as loaded when the daemon cannot
even discover it. The daemon's site-packages is read as **metadata**
(``importlib.metadata.distributions(path=[...])``) — no subprocess, and no import of
plugin code, because a plugin's ``import`` runs its module-level code: a diagnostic that
imports can be the thing that breaks the plugin it measures. Presence in the daemon is
answered by package files being where they would be, never by importing.

Activation comes from the gateway log (``navig plugin loaded: <name>`` /
``navig plugin '<name>' failed to load: <reason>``) **bounded to the current boot** by
``gateway.pid``'s mtime. Without that bound there is no evidence, and every record says
``activation="unknown"`` instead of claiming health it cannot support.

States — the four every report must distinguish, plus what a broken install adds:

===========================  ==============================================================
``not_installed``            source exists, no distribution in the daemon's interpreter
``undiscoverable``           installed there, but its declared entry point is missing, its
                             package files are absent, or an editable copy points at a
                             folder that is gone
``activation_failed``        discovered, and the gateway logged it failing to load — reason kept
``inactive``                 discovered (``navig.plugins``), no activation evidence this boot
``healthy``                  installed, discoverable, present and loaded
===========================  ==============================================================

``stale_editable`` / ``version_drift`` are orthogonal flags: loads today, but from the wrong
folder or an older version is drift, not absence. A package that declares no
``navig.plugins`` entry point (``navig-vault``, ``navig-media`` — libraries core imports —
and commands-only plugins) is judged on presence alone: the gateway never logs those, so a
missing "loaded:" line is not an outage.
"""

from __future__ import annotations

import configparser
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Collection, Iterable

PLUGIN_GROUP = "navig.plugins"
COMMAND_GROUP = "navig.commands"
GROUPS = (PLUGIN_GROUP, COMMAND_GROUP)

LOADED_RE = re.compile(r"navig plugin loaded: (\S+)")
FAILED_RE = re.compile(r"navig plugin '([^']+)' failed to load: (.+)$")
# Log lines are ``HH:MM:SS  LEVEL  target  message`` — no date, hence the pid-file bound.
STAMP_RE = re.compile(r"^(\d{2}:\d{2}:\d{2})\b")


@dataclass
class PluginRuntimeRecord:
    """One first-party plugin, measured against the environment the GATEWAY runs."""

    package: str
    plugin_id: str
    source_path: str
    state: str
    activation: str = "unknown"  # verified | failed | unknown | not_applicable
    #: The interpreter this state describes; None when it could not be read (then the
    #: caller's interpreter stood in and the inventory reports ``daemon_known: false``).
    daemon_interpreter: str | None = None
    daemon_installed: bool = False
    daemon_version: str | None = None
    daemon_editable: bool = False
    daemon_location: str | None = None
    #: group -> whether the daemon's interpreter carries an entry point for this plugin.
    entry_points: dict[str, bool] = field(default_factory=dict)
    declares_entry_point: bool = False
    package_files_present: bool = False
    #: The interpreter ``navig doctor`` itself ran in — repair target and the
    #: "installed here, absent there" class that made msstore vanish.
    this_interpreter: str = ""
    this_installed: bool = False
    this_version: str | None = None
    this_editable: bool = False
    expected_version: str | None = None
    #: Flags orthogonal to the state: loads, but from the wrong place / an older version.
    stale_editable: bool = False
    dead_editable: bool = False
    version_drift: bool = False
    #: ``{lower dist: executable}`` for copies in other Pythons, when a caller supplies them.
    installed_elsewhere: str | None = None
    activation_detail: str | None = None
    repair: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


# ── metadata, read twice: the caller's interpreter, then the daemon's ────────────────


def _dist_map(path: list[str] | None = None) -> dict[str, dict[str, Any]]:
    """``{lower dist: {version, editable, location, entry_points}}`` from metadata only.

    ``path`` picks the environment: ``None`` means this interpreter, otherwise a
    site-packages directory (the daemon's). Entry points come from each dist's own
    ``entry_points.txt`` — the file, never the module it names.
    """
    from importlib.metadata import distributions  # noqa: PLC0415 — lazy, keeps `navig help` fast

    from navig.plugins.sources import _editable_dir  # noqa: PLC0415 — reuse, never re-derive

    out: dict[str, dict[str, Any]] = {}
    try:
        found = list(distributions(path=path)) if path is not None else list(distributions())
    except Exception:  # noqa: BLE001 — an unreadable tree must not take the doctor down
        return out
    for d in found:
        name = (d.metadata.get("Name") or "").lower()
        if name != "navig" and not name.startswith("navig-"):
            continue
        editable = _editable_dir(d.read_text("direct_url.json"))
        try:
            location = str(Path(d.locate_file("")).resolve())
        except Exception:  # noqa: BLE001 — metadata with no resolvable base
            location = ""
        eps: dict[str, list[str]] = {}
        try:
            text = d.read_text("entry_points.txt")
        except Exception:  # noqa: BLE001
            text = None
        if text:
            parser = configparser.ConfigParser()
            try:
                parser.read_string(text)
                eps = {section: list(parser[section]) for section in parser.sections()}
            except (configparser.Error, ValueError):
                eps = {}
        out[name] = {
            "version": d.version,
            "editable": str(editable) if editable else None,
            "location": location,
            "entry_points": eps,
        }
    return out


def declared_groups(source: Path) -> tuple[str, dict[str, list[str]]]:
    """``(dist, {entry-point group: [names]})`` from the source pyproject — read only."""
    try:
        import tomllib  # noqa: PLC0415 — py3.11+

        data = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
    except (ImportError, OSError, ValueError):
        return source.name.replace("_", "-"), {}
    project = data.get("project", {}) or {}
    raw = project.get("entry-points") or {}
    groups = {g: list(names) for g, names in raw.items() if isinstance(names, dict) and g in GROUPS}
    return str(project.get("name") or source.name.replace("_", "-")), groups


def source_version(source: Path) -> str | None:
    """The static ``[project].version`` a plugin's source declares, or None."""
    try:
        import tomllib  # noqa: PLC0415

        data = tomllib.loads((source / "pyproject.toml").read_text(encoding="utf-8"))
    except (ImportError, OSError, ValueError):
        return None
    version = (data.get("project") or {}).get("version")
    return version if isinstance(version, str) else None


def source_plugins(root: Path) -> list[tuple[str, Path]]:
    """``[(dist, source dir)]`` for every first-party source tree.

    ``plugins/navig-*`` plus ``private/harbor`` — the same shape CI's ``pluginDirs()``
    treats as first-party — and only where a ``pyproject.toml`` marks the folder a package.
    """
    out: list[tuple[str, Path]] = []
    for base in (root / "plugins", root / "private"):
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if child.is_dir() and (child / "pyproject.toml").is_file():
                dist, _groups = declared_groups(child)
                out.append((dist, child))
    return out


# ── the daemon's environment ─────────────────────────────────────────────────────────


def _site_packages_for(exe: str, cmdline: Iterable[str] = ()) -> list[str] | None:
    """The site-packages a Python at ``exe`` reads — directory probes, never a run.

    The **cmdline** comes first: on Windows a venv's ``Scripts\\pythonw.exe`` launches the
    base interpreter, so ``psutil.exe()`` returns ``runtime\\cpython\\pythonw.exe`` while the
    process actually runs out of ``runtime\\venv\\Lib\\site-packages`` — deriving from the exe
    alone points at a directory that does not exist (measured 2026-10-20, which is how the
    daemon's Python first read as unknown).
    """
    candidates: list[Path] = []
    for arg in cmdline:
        name = Path(str(arg)).name.lower()
        if name.startswith("python") and name.endswith((".exe", ".bat", "")):
            candidates.append(Path(str(arg)))
    candidates.append(Path(exe))
    out: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            base = candidate.parent.parent  # <venv>/Scripts or <prefix>/bin
            for sub in (base / "Lib" / "site-packages",):  # Windows
                if sub.is_dir() and str(sub) not in seen:
                    seen.add(str(sub))
                    out.append(str(sub))
            for sub in sorted((base / "lib").glob("python*/site-packages")):  # POSIX
                if str(sub) not in seen:
                    seen.add(str(sub))
                    out.append(str(sub))
        except (OSError, ValueError):
            continue
    return out or None


def _is_gateway_cmdline(cmd: list[str]) -> bool:
    """Whether a process is a NAVIG gateway: python + ``-m navig … gateway``.

    Structural, never a substring of somebody's shell command — ``bash -c "…gateway start…"``
    is not a gateway, and a browser that inherited a recycled pid is not one either.
    """
    if not cmd:
        return False
    head = Path(cmd[0]).name.lower()
    if not (head.startswith("python") or head.startswith("navig")):
        return False
    args = cmd[1:]
    return "-m" in args and "navig" in args and "gateway" in args


def gateway_environment() -> dict[str, Any] | None:
    """``{pid, exe, site_packages, gateways, ambiguous}`` for the RUNNING gateway.

    The pid comes from ``gateway.pid`` (the gateway writes it as it starts) and the
    process details from psutil — no subprocess, no import. Other gateways are counted
    because several can be alive at once (a daemon child plus a stray ``gateway start``):
    when they run on the SAME site-packages the log still attributes to one environment,
    when they do not, activation cannot be attributed and the caller must say so.

    Anything unreadable returns None: the caller must then report the daemon's Python as
    unknown instead of substituting its own silently.
    """
    try:
        from navig.platform import paths  # noqa: PLC0415

        pid_file = paths.config_dir() / "gateway.pid"
        if not pid_file.is_file():
            return None
        pid = int(pid_file.read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return None
    try:
        import psutil  # noqa: PLC0415

        proc = psutil.Process(pid)
        exe = proc.exe()
        cmdline = [str(a) for a in proc.cmdline()]
    except Exception:  # noqa: BLE001 — process gone or access denied: unknown, not guessed
        return None
    if not _is_gateway_cmdline(cmdline):
        # `gateway.pid` outlives the gateway and Windows hands the number to somebody else
        # (measured 2026-10-25: a file written Oct 22 named a Chrome renderer). Trusting it
        # reported that process's interpreter as the daemon's and pointed a repair command
        # at a browser — unknown, never a substitution.
        return None

    site_packages = _site_packages_for(exe, cmdline)
    gateways = 1
    ambiguous = False
    try:
        for other in psutil.process_iter(["pid", "cmdline"]):
            cmd = [str(a) for a in (other.info.get("cmdline") or [])]
            if other.info.get("pid") == pid or not cmd:
                continue
            if not _is_gateway_cmdline(cmd):
                continue
            others = _site_packages_for(other.exe(), cmd)
            gateways += 1
            if others and site_packages and others != site_packages:
                ambiguous = True
    except Exception:  # noqa: BLE001 — a failed census must not fail the doctor
        pass

    return {
        "pid": pid,
        "exe": exe,
        "site_packages": site_packages,
        "gateways": gateways,
        "ambiguous": ambiguous,
    }


def gateway_pid_mtime() -> float | None:
    """When ``gateway.pid`` was written — the bound that makes log evidence current."""
    try:
        from navig.platform import paths  # noqa: PLC0415

        pid = paths.config_dir() / "gateway.pid"
        return pid.stat().st_mtime if pid.is_file() else None
    except Exception:  # noqa: BLE001
        return None


def gateway_activation(
    log_path: Path | None = None, *, since: float | None = None
) -> tuple[set[str], dict[str, str], bool]:
    """``(loaded, failed, bounded)`` from the gateway log for the current boot.

    ``bounded`` is False when the log or the bound is missing: the caller must then report
    ``activation="unknown"`` for everything instead of a green tick over an unknown. The
    last 1.5 MB is read, which covers any single boot and keeps a 10 MB log cheap.
    """
    if log_path is None:
        try:
            from navig.platform import paths  # noqa: PLC0415

            log_path = paths.log_dir() / "gateway.log"
        except Exception:  # noqa: BLE001
            return set(), {}, False
    if not log_path.is_file():
        return set(), {}, False
    try:
        with log_path.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 1_500_000))
            text = fh.read().decode("utf-8", errors="replace")
    except OSError:
        return set(), {}, False

    start_hms: str | None = None
    if since:
        import datetime as _dt  # noqa: PLC0415

        start_hms = _dt.datetime.fromtimestamp(since).strftime("%H:%M:%S")

    loaded: set[str] = set()
    failed: dict[str, str] = {}
    for line in text.splitlines():
        m = STAMP_RE.match(line)
        if start_hms and (m is None or m.group(1) < start_hms):
            continue
        hit = LOADED_RE.search(line)
        if hit:
            loaded.add(hit.group(1))
            continue
        hit = FAILED_RE.search(line)
        if hit:
            failed[hit.group(1)] = hit.group(2).strip()[:200]
    return loaded, failed, start_hms is not None


# ── inventory ────────────────────────────────────────────────────────────────────────


def _package_files_in(package: str | None, dist: dict[str, Any], paths: list[str] | None) -> bool:
    """Are this package's files where the daemon would find them? A path check, not an import."""
    if not package:
        return False
    if dist.get("editable"):
        try:
            return (Path(dist["editable"]) / package).is_dir()
        except (OSError, ValueError):
            return False
    for root in paths or []:
        try:
            if (Path(root) / package / "__init__.py").is_file():
                return True
        except OSError:
            continue
    return False


def runtime_inventory(
    root: Path | None = None,
    *,
    elsewhere: dict[str, str] | None = None,
    loaded: Collection[str] | None = None,
    failed: dict[str, str] | None = None,
    bounded: bool | None = None,
    daemon: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Inventory every first-party source plugin against the DAEMON's interpreter.

    Returns ``None`` outside a development checkout — an end user installed from PyPI and
    the neighbouring rows describe that install; a verdict there would have no source tree
    to compare against.

    ``daemon`` injects ``gateway_environment()`` (tests), ``loaded``/``failed``/``bounded``
    inject gateway-log evidence (read live and bounded by ``gateway.pid`` when omitted),
    and ``elsewhere`` is ``{lower dist: executable}`` for copies in other Pythons when the
    caller already has them — this never spawns an interpreter.
    """
    if root is None:
        from navig.commands.repo import resolve_repo_root  # noqa: PLC0415

        root = resolve_repo_root()
    if root is None or not (root / "plugins").is_dir():
        return None

    from navig.plugins.sources import _package_dir_name  # noqa: PLC0415

    sources = source_plugins(root)
    if not sources:
        return None

    if loaded is None and failed is None and bounded is None:
        loaded, failed, bounded = gateway_activation(since=gateway_pid_mtime())
    loaded_set = set(loaded or ())
    failed_map = dict(failed or {})
    evidence = bool(bounded)
    ambiguity: str | None = None

    if daemon is None:
        daemon = gateway_environment()
    daemon_exe = (daemon or {}).get("exe")
    daemon_paths = (daemon or {}).get("site_packages")
    # Unreadable daemon Python = UNKNOWN, never a silent substitution: the caller's
    # interpreter stands in and every record says which one it measured.
    daemon_known = bool(daemon and daemon_paths)
    if daemon and daemon.get("ambiguous"):
        # Two gateways on different Pythons write one log: a "loaded: x" line cannot be
        # attributed to either. Unknown is the honest state.
        evidence = False
        ambiguity = (
            f"{daemon.get('gateways')} gateways on different Pythons — the log cannot "
            "attribute a load to one of them"
        )
    here_map = _dist_map(None)
    daemon_map = _dist_map(daemon_paths) if daemon_known else here_map
    elsewhere_map = dict(elsewhere or {})

    records: list[PluginRuntimeRecord] = []
    for dist, source in sources:
        package = _package_dir_name(source)
        declared_name, groups = declared_groups(source)
        gateway_ep = (groups.get(PLUGIN_GROUP) or [None])[0]
        expected = source_version(source)
        daemon_dist = daemon_map.get(dist.lower())
        here_dist = here_map.get(dist.lower())
        # The repair command may only name the daemon's interpreter — otherwise it names no
        # interpreter at all. Falling back to sys.executable claimed the CALLER's Python was
        # the daemon's, the substitution this module's docstring forbids.
        fix = f"python -m pip install -e {source}"
        if daemon_exe:
            fix += f"   # into {daemon_exe}"
        ep_present = {g: bool((daemon_dist or {}).get("entry_points", {}).get(g)) for g in GROUPS}
        declares_gateway = bool(gateway_ep)
        has_declared = bool(
            daemon_dist and gateway_ep and gateway_ep in ((daemon_dist or {}).get("entry_points", {}).get(PLUGIN_GROUP) or [])
        )
        files = _package_files_in(package, daemon_dist or {}, daemon_paths if daemon_known else None)

        rec = PluginRuntimeRecord(
            package=declared_name,
            plugin_id=gateway_ep or (groups.get(COMMAND_GROUP) or [""])[0] or declared_name,
            source_path=str(source),
            state="not_installed",
            daemon_interpreter=daemon_exe if daemon_known else None,
            daemon_installed=bool(daemon_dist),
            daemon_version=(daemon_dist or {}).get("version"),
            daemon_editable=bool((daemon_dist or {}).get("editable")),
            daemon_location=(daemon_dist or {}).get("editable") or (daemon_dist or {}).get("location") or None,
            entry_points=ep_present,
            declares_entry_point=bool(groups),
            package_files_present=files,
            this_interpreter=sys.executable,
            this_installed=bool(here_dist),
            this_version=(here_dist or {}).get("version"),
            this_editable=bool((here_dist or {}).get("editable")),
            expected_version=expected,
            installed_elsewhere=elsewhere_map.get(dist.lower()),
        )

        d_editable = (daemon_dist or {}).get("editable")
        if d_editable:
            rec.stale_editable = Path(d_editable).resolve() != source.resolve()
            rec.dead_editable = not Path(d_editable).is_dir()
        if expected and rec.daemon_version and expected != rec.daemon_version:
            rec.version_drift = True
        if not rec.daemon_installed and rec.this_installed and not rec.installed_elsewhere:
            # The msstore shape: installed for this CLI, absent from the daemon's Python.
            rec.installed_elsewhere = sys.executable

        if not rec.daemon_installed:
            rec.state = "not_installed"
            rec.repair = fix
            if rec.installed_elsewhere:
                rec.activation_detail = (
                    f"installed in {rec.installed_elsewhere}, not the daemon's Python"
                )
            records.append(rec)
            continue

        if rec.dead_editable:
            rec.state = "undiscoverable"
            rec.activation_detail = f"editable copy points at a missing folder: {d_editable}"
            rec.repair = fix
        elif declares_gateway and not has_declared:
            rec.state = "undiscoverable"
            rec.activation_detail = (
                f"installed, but entry point {gateway_ep!r} is missing in "
                f"{daemon_exe or 'the daemon'}"
            )
            rec.repair = fix
        elif not files:
            rec.state = "undiscoverable"
            rec.activation_detail = "package files are not in the daemon's environment"
            rec.repair = fix
        elif not declares_gateway:
            # A library core imports (navig-vault, navig-media, …) or a commands-only
            # plugin: the gateway never loads those through navig.plugins, so the absence
            # of a "loaded:" line is not an outage — presence is the whole verdict.
            rec.state = "healthy"
            rec.activation = "not_applicable"
            rec.activation_detail = "no navig.plugins entry point (imported by core, or commands only)"
        elif not evidence:
            rec.state = "inactive"
            rec.activation = "unknown"
            rec.activation_detail = ambiguity or (
                "no gateway log for the current boot (gateway.pid missing?)"
            )
        elif gateway_ep in loaded_set:
            rec.state = "healthy"
            rec.activation = "verified"
        elif gateway_ep in failed_map:
            rec.state = "activation_failed"
            rec.activation = "failed"
            rec.activation_detail = failed_map[gateway_ep]
            rec.repair = "fix the load error above, then: navig service restart"
        else:
            rec.state = "inactive"
            rec.activation = "unknown"
            rec.activation_detail = "installed but not loaded by the running gateway"
            rec.repair = "navig service restart   # entry points load at gateway start"

        if rec.state in ("healthy", "inactive") and (rec.stale_editable or rec.version_drift):
            rec.repair = fix  # loads, but from the wrong folder or an older version
        records.append(rec)

    counts: dict[str, int] = {}
    for rec in records:
        counts[rec.state] = counts.get(rec.state, 0) + 1
    return {
        "interpreter": sys.executable,
        "daemon_interpreter": daemon_exe,
        "daemon_known": daemon_known,
        "gateways": (daemon or {}).get("gateways"),
        "ambiguous": ambiguity,
        "root": str(root),
        "activation_evidence": evidence,
        "expected": len(records),
        "counts": counts,
        "records": [r.as_dict() for r in records],
    }


def inventory_rows(inventory: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Doctor rows as plain dicts ``{label, ok, detail, data}`` ([] outside a checkout).

    The summary never hides what is absent: it counts, names the first four packages, and
    carries every record (state, both interpreters, entry points, activation, repair) as
    ``data`` for ``--json``. Only genuinely broken installs get their own console line — a
    screen of optional plugins each carrying a red cross is a report nobody reads.
    """
    if not inventory:
        return []
    records: list[dict[str, Any]] = list(inventory["records"])
    counts = inventory.get("counts") or {}
    broken = [r for r in records if r["state"] in ("undiscoverable", "activation_failed")]
    inactive = [r for r in records if r["state"] == "inactive"]
    missing = [r for r in records if r["state"] == "not_installed"]
    elsewhere = [r for r in missing if r.get("installed_elsewhere")]
    drift = [r for r in records if r.get("stale_editable") or r.get("version_drift")]
    evidence = bool(inventory.get("activation_evidence"))
    daemon = inventory.get("daemon_interpreter")
    known = bool(inventory.get("daemon_known"))

    rows: list[dict[str, Any]] = []
    # Per-plugin rows are for BROKEN installs only: absent-elsewhere is real and
    # counted in the summary, but 33 red crosses would be a report nobody reads.
    problems = bool(broken)
    unknown = not known or not evidence
    warns = not problems and (bool(inactive) or bool(missing) or bool(drift) or unknown)
    where = ""
    if known and daemon:
        where = f" · daemon: {Path(daemon).name}"
    elif not known:
        where = " · daemon Python unknown (no gateway.pid, or its pid is not a gateway)"
        # Nothing about the daemon's site-packages was measured, so a per-plugin verdict
        # would be a claim about an environment we could not read: on a client that runs no
        # daemon at all, 36 sources were reported "package files are not in the daemon's
        # environment". One honest warning, and every record stays in --json.
        return [
            {
                "label": "Runtime plugins",
                "ok": False,
                "warn": True,
                "detail": (
                    f"{inventory.get('expected', 0)} plugin source(s) — activation unverified"
                    f"{where}; its site-packages was not measured"
                ),
                "data": {
                    "interpreter": inventory.get("interpreter"),
                    "daemon_interpreter": daemon,
                    "daemon_known": known,
                    "counts": counts,
                    "activation_evidence": evidence,
                    "records": records,
                },
            }
        ]
    verified = len([r for r in records if r["activation"] == "verified"])
    libraries = len([r for r in records if r["activation"] == "not_applicable"])
    installed = int(counts.get("healthy", 0)) + len(inactive) + len(broken)
    # Say exactly what was observed: verified loads, libraries that are never logged as
    # loads, and installs whose activation could not be checked — not one blended number.
    detail = (
        f"{verified}/{inventory['expected']} loaded by the running gateway"
        + (f" · {libraries} library/commands-only" if libraries else "")
        + where
        if evidence and known
        else f"{installed}/{inventory['expected']} installed — activation unverified{where}"
    )
    if missing:
        names = ", ".join(r["package"] for r in missing[:4])
        ellipsis = "…" if len(missing) > 4 else ""
        detail += f" · {len(missing)} not installed: {names}{ellipsis}"
        only_elsewhere = len([r for r in missing if r.get("installed_elsewhere")])
        if only_elsewhere:
            detail += f" ({only_elsewhere} are installed in another Python, not the daemon's)"
    if drift:
        detail += f" · {len(drift)} stale"
    rows.append(
        {
            "label": "Runtime plugins",
            "ok": not (problems or warns),
            "warn": warns and not problems,
            "detail": detail,
            "data": {
                "interpreter": inventory.get("interpreter"),
                "daemon_interpreter": daemon,
                "daemon_known": known,
                "counts": counts,
                "activation_evidence": evidence,
                "records": records,
            },
        }
    )
    for rec in broken:
        rows.append(
            {
                "label": f"runtime plugin {rec['package']}",
                "ok": False,
                "warn": False,
                "detail": (
                    f"{rec['state']}: {rec.get('activation_detail') or ''}"
                    f" → {rec.get('repair') or 'navig service restart'}"
                ),
                "data": None,
            }
        )
    soft = [r for r in (missing + inactive) if r not in elsewhere]
    if len(soft) > 4:
        rows.append(
            {
                "label": "runtime plugins",
                "ok": False,
                "warn": True,
                "detail": (
                    f"+{len(soft) - 4} more not installed or not loaded — full list, with "
                    "repairs, in --json"
                ),
                "data": None,
            }
        )
    return rows
