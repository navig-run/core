"""No unscoped, machine-wide process killer that targets navig's OWN processes.

Three separate bugs shipped the same catastrophe: a helper enumerated navig
processes by **command-line pattern** (``navig gateway start`` / ``navig.daemon.*`` /
``telegram_worker``) and then force-killed **every** match — with no ``NAVIG_CONFIG_DIR``
scoping. So an operation run under a *different* config dir (a second venv, a
temp-config shell, another brain on the same box) silently force-killed the operator's
LIVE gateway/daemon/worker, all lights green, no shutdown line:

- ``navig gateway start`` supersede + ``_free_port`` (#173 / the CLAUDE.md sharp edge),
- ``daemon/supervisor.py::_kill_orphan_daemons`` (#669), reachable via ``navig service stop``,
- ``navig bot stop`` / ``bot status`` (#672), a hand-rolled ``Get-CimInstance`` / ``pkill``.

Each was fixed the same way — scope the sweep by the process's effective config dir
(``single_instance.config_dir_of``) and never kill one you can't identify — but nothing
stopped a *fourth* from being written. This is the lock on that door.

It flags two shapes. (1) Any ``subprocess.*`` / ``os.system`` call that uses a
**command-line-match** primitive: ``pkill`` / ``pgrep`` (POSIX kill/enumerate by
pattern) or ``Get-CimInstance Win32_Process`` filtered on ``CommandLine`` (the WMI
cmdline enumeration). (2) The psutil-native form — a ``for x in psutil.process_iter(…)``
loop whose body force-kills (``.kill()`` / ``.terminate()`` / ``.send_signal()`` /
``os.kill``); enumerating the whole table and killing matches in-loop is the same
machine-wide sweep with no shell string to catch. It deliberately does NOT flag a kill
of a **specific known PID** (``taskkill /PID`` / a ``Get-CimInstance … -Filter
"ProcessId=…"`` read), a ``pkill`` in a help/hint STRING, or a ``process_iter`` loop
that only READS/lists — the danger is *pattern* enumeration feeding a kill.

The sanctioned scoped killer is ``navig.daemon.single_instance.kill_other_instances``
(pass ``config_dir=paths.config_dir()``). Legitimate cmdline-match sites that are NOT a
navig-brain kill — the scoped killer itself, browser-image cleanup, a remote SSH
``pkill`` — are an explicit ALLOWLIST, each with the reason it is safe. A new
unlisted site fails the build with a pointer to the helper.

What this proves: the *habit* (no raw cmdline-match kill outside the scoped helper),
not that every kill is provably scoped — the same tripwire shape as the SSRF-wiring and
orphan-task guards. Remaining gaps (deliberate): a *collect-pids-then-kill-in-a-separate-
loop* split evades the in-loop check, and scope is ``core/navig/`` only — plugins
(navig-mini, navig-antivirus) manage their OWN / remote / browser processes, a different
domain from the core config-dir brain, so they are out of scope here.
"""

from __future__ import annotations

import ast
from pathlib import Path

# core/tests/quality/<this> -> parents[2] == core, then /navig.
_NAVIG_ROOT = Path(__file__).resolve().parents[2] / "navig"

# Subprocess entry points whose command argument may carry a shell/CLI kill primitive.
_SUBPROCESS_FUNCS = frozenset(
    {"run", "Popen", "check_output", "call", "check_call", "getoutput", "getstatusoutput", "system"}
)

# Files that legitimately contain a cmdline-match process primitive — each SAFE for a
# documented reason. Keyed by path relative to ``core/navig``. A NEW site not listed
# here fails the build; test_allowlist_has_no_stale_entries proves each stays live.
_ALLOWLIST: dict[str, str] = {
    "daemon/single_instance.py": (
        "THE sanctioned scoped killer: kill_other_instances scopes every kill by "
        "config_dir_of; the WMI/ps enumeration here only builds the process table it filters"
    ),
    "daemon/supervisor.py": (
        "_kill_orphan_daemons enumerator (_enumerate_navig_pids) — its results are scoped "
        "by config_dir_of before any kill (#669); _verify_daemon_pid reads a specific PID"
    ),
    "browser/targets.py": (
        "kills BROWSER processes by image name (chrome/edge/brave/electron), never a navig "
        "brain — a different domain (see the no-chrome-kill rule)"
    ),
    "builtins/mini_control/plugin.py": (
        "a REMOTE `navig run --host <ssh>` pkill of the deployed agent.py on the SSH target "
        "— not a local navig process, cannot reach the operator's brain"
    ),
}


def _call_strings(node: ast.AST) -> list[str]:
    """Every string-constant value anywhere inside a Call node (argv items, f-string parts)."""
    return [
        n.value
        for n in ast.walk(node)
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
    ]


def _is_subprocess_call(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in _SUBPROCESS_FUNCS
    )


def _cmdline_match_primitive(node: ast.Call) -> str | None:
    """Return a label if this subprocess call uses a command-line-MATCH process primitive.

    Returns None for specific-PID kills and PID-filtered reads (both safe).
    """
    s = " ".join(_call_strings(node)).lower()
    if "pkill" in s or "pgrep" in s:
        return "pkill/pgrep (cmdline-match process enumeration/kill)"
    # WMI: only a CommandLine *filter* (Where-Object / -like / -match) is a pattern
    # enumeration. `-Filter "ProcessId=N"` reads one known PID — not flagged.
    if (
        "get-ciminstance" in s
        and "win32_process" in s
        and "commandline" in s
        and ("where-object" in s or "-like" in s or "-match" in s)
    ):
        return "Get-CimInstance Win32_Process | CommandLine filter (cmdline-match enumeration)"
    return None


# Killing methods that, called inside a psutil.process_iter loop, turn a whole-table
# enumeration into a kill. os.kill(...) shares the ``kill`` attribute name and is caught too.
_KILL_METHODS = frozenset({"kill", "terminate", "send_signal"})


def _psutil_kill_loop(node: ast.AST) -> str | None:
    """Flag a ``for x in psutil.process_iter(...)`` loop whose body force-kills.

    Enumerating the whole process table and killing matches in the loop body is the
    psutil-native form of the machine-wide cmdline-match kill — no ``pkill``/``pgrep``/WMI
    string for the primitive detector to catch. A loop that only READS/lists is NOT
    flagged. The sanctioned killer builds its table with ``process_iter`` but kills
    OUTSIDE the loop (in ``kill_other_instances``), so its loop body never matches.
    """
    if not (
        isinstance(node, ast.For)
        and isinstance(node.iter, ast.Call)
        and isinstance(node.iter.func, ast.Attribute)
        and node.iter.func.attr == "process_iter"
    ):
        return None
    for inner in ast.walk(ast.Module(body=node.body, type_ignores=[])):
        if (
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Attribute)
            and inner.func.attr in _KILL_METHODS
        ):
            return f"psutil.process_iter loop body calls .{inner.func.attr}() (enumerate-then-kill)"
    return None


def _violations() -> list[tuple[str, int, str]]:
    out: list[tuple[str, int, str]] = []
    for path in _NAVIG_ROOT.rglob("*.py"):
        rel = path.relative_to(_NAVIG_ROOT).as_posix()
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            why = None
            if _is_subprocess_call(node):
                why = _cmdline_match_primitive(node)
            elif isinstance(node, ast.For):
                why = _psutil_kill_loop(node)
            if why:
                out.append((rel, node.lineno, why))
    return out


def test_no_unscoped_navig_process_killer():
    offenders = [(rel, ln, why) for rel, ln, why in _violations() if rel not in _ALLOWLIST]
    assert not offenders, (
        "Unscoped command-line-match process killer(s) found. Route the kill through "
        "navig.daemon.single_instance.kill_other_instances(patterns, config_dir=paths.config_dir()) "
        "— which scopes every kill by config_dir_of — or add an allowlist entry with the "
        "reason it is not a navig-brain kill:\n"
        + "\n".join(f"  {rel}:{ln}  {why}" for rel, ln, why in offenders)
    )


def test_allowlist_has_no_stale_entries():
    flagged = {rel for rel, _, _ in _violations()}
    stale = sorted(rel for rel in _ALLOWLIST if rel not in flagged)
    assert not stale, (
        "Allowlist entries no longer contain a flagged primitive (remove them so the "
        f"list stays honest): {stale}"
    )


# ── detector teeth: real bug shape flags, safe shapes don't ──────────────────


def _first_call(src: str) -> ast.Call:
    tree = ast.parse(src)
    return next(n for n in ast.walk(tree) if isinstance(n, ast.Call))


def _first_subprocess_call(src: str) -> ast.Call:
    tree = ast.parse(src)
    return next(n for n in ast.walk(tree) if _is_subprocess_call(n))


def test_detector_flags_unscoped_pkill():
    node = _first_subprocess_call('subprocess.run(["pkill", "-f", "navig gateway start"])')
    assert _cmdline_match_primitive(node) is not None


def test_detector_flags_wmi_commandline_like():
    node = _first_subprocess_call(
        'subprocess.run(["powershell", "-Command", '
        '"Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like \'*navig gateway*\' }"])'
    )
    assert _cmdline_match_primitive(node) is not None


def test_detector_ignores_specific_pid_taskkill():
    node = _first_subprocess_call('subprocess.run(["taskkill", "/F", "/PID", str(pid), "/T"])')
    assert _cmdline_match_primitive(node) is None


def test_detector_ignores_processid_filter_read():
    # `_verify_daemon_pid` reads ONE known PID's cmdline — not a pattern enumeration.
    node = _first_subprocess_call(
        'subprocess.run(["powershell", "-Command", '
        'f\'(Get-CimInstance Win32_Process -Filter "ProcessId={pid}").CommandLine\'])'
    )
    assert _cmdline_match_primitive(node) is None


def _first_for(src: str) -> ast.For:
    return next(n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.For))


def test_detector_flags_psutil_iter_then_kill():
    src = (
        "for p in psutil.process_iter(['pid', 'cmdline']):\n"
        "    if 'navig gateway' in ' '.join(p.info['cmdline'] or []):\n"
        "        p.kill()\n"
    )
    assert _psutil_kill_loop(_first_for(src)) is not None


def test_detector_flags_psutil_iter_then_os_kill():
    src = "for p in psutil.process_iter(['pid']):\n    os.kill(p.pid, signal.SIGKILL)\n"
    assert _psutil_kill_loop(_first_for(src)) is not None


def test_detector_ignores_psutil_iter_listing():
    # A process_iter loop that only READS/lists (no kill) is fine — monitor/processes list,
    # and the scoped killer's table build (kill happens in a separate loop).
    src = (
        "for p in psutil.process_iter(['pid', 'name']):\n"
        "    rows.append((p.info['pid'], p.info['name']))\n"
    )
    assert _psutil_kill_loop(_first_for(src)) is None


def test_detector_ignores_pkill_in_a_hint_string():
    # `pkill` in a help/hint string is not a subprocess call, so it is never a violation.
    assert not _is_subprocess_call(_first_call('ch.info("kill it manually: pkill -f navig gateway")'))
