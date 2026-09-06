"""A windowless process must not flash console windows at the operator.

**The class.** NAVIG's daemon is correctly windowless — ``pythonw.exe``, Task Scheduler
``<Hidden>true</Hidden>``, ``CREATE_NO_WINDOW`` on every start path. That is *why* its
children flash: a process with **no console cannot lend one**, so Windows allocates a
brand-new console for each console child (``git``, ``icacls``, ``taskkill``, ``powershell``,
``npx``, ``ffmpeg``) which appears and vanishes on screen. Measured over ``core/navig`` +
``plugins``: 486 direct spawn sites, **30** passing any ``creationflags``.

**Why this guard protects the MECHANISM and not the 486 call sites.** The fix is
``install_windowless_spawn_default()``, installed at each windowless entry point: it applies
only when this process has no console — the only condition in which a window can appear —
and never overrides a caller who already chose. One mechanism covers core, all plugins, and
third-party libraries that shell out. A mechanism, though, is exactly the thing that gets
silently unwired: "written · tested · never wired" is a recurring defect class here, and a
helper nothing calls is documentation. So the assertions below are wiring assertions.

⚖ **A broader subject was MEASURED and REJECTED — do not re-derive it.** "every
``subprocess`` call running a Windows console tool must pass window-suppressing
``creationflags``" yields **51** findings across core + plugins. It is not gateable, for the
reason the same doctrine rejected ``arg-type`` (314) and ``assignment`` (310): composition,
not size. Most of the 51 sit on CLI-only paths (``commands/doctor.py``, ``commands/menu.py``,
``commands/mount.py``, ``commands/interactive.py``) where the process **has** a console, the
child inherits the operator's terminal, and no window ever appears — so they are not defects,
and nothing in the AST distinguishes a daemon-reachable call from a CLI-only one. Gating it
would need a ~51-entry baseline of mostly-correct code, which is the "a baseline becomes a
place to hide things" failure that ``_CALL_ARG_BASELINE``'s design rejects. The runtime
mechanism already covers all 51 from the direction that matters.

Machinery is imported from ``test_console_subprocess_encoding`` rather than copied — it
already owns the console-tool set, the spawn-call set, the scan roots, and the scope-aware
name resolution, and a second copy would drift.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path


def _sibling():
    """Load the sibling guard FROM ITS PATH.

    A bare ``import test_console_subprocess_encoding`` depends on pytest's rootdir landing
    on ``sys.path``, which it does not here (``tests/quality`` has no ``__init__.py``).
    Loading by path is what ``test_vault_compat_parity.py`` already does for the same
    reason, and it fails loudly if the sibling is renamed rather than skipping.
    """
    path = Path(__file__).with_name("test_console_subprocess_encoding.py")
    assert path.is_file(), f"sibling guard missing: {path}"
    spec = importlib.util.spec_from_file_location("_console_encoding_guard_probe", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_g = _sibling()
REPO = _g.REPO
_CALLS = _g._CALLS
_python_files = _g._python_files
_scopes = _g._scopes

# Flags that already answer "does this child get a window?". Any one of them means the
# caller decided, and composing another onto it is not this guard's business.
_WINDOW_DECIDING = {"CREATE_NO_WINDOW", "CREATE_NEW_CONSOLE", "DETACHED_PROCESS"}

# Entry points that run WITHOUT a console and therefore must install the default.
# {module path: the interpreter/launcher that makes it windowless}
_WINDOWLESS_ENTRIES: dict[str, str] = {
    "core/navig/daemon/entry.py": "pythonw.exe via Task Scheduler / NSSM (service_manager)",
    "core/navig/daemon/telegram_worker.py": "pythonw.exe -m navig.daemon.telegram_worker",
    "core/navig/main.py": "pythonw.exe -m navig gateway start (and every CLI entry; no-ops "
                          "when a console exists)",
}

_INSTALLER = "install_windowless_spawn_default"

# Composed-flags offenders. EMPTY by construction: unlike a suppression baseline, an entry
# here would assert that flashing a console at the operator is acceptable at that site.
_BASELINE: dict[str, str] = {}


def _flag_names(node: ast.AST) -> set[str]:
    """Every bare/dotted name mentioned inside a ``creationflags`` expression."""
    out: set[str] = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute):
            out.add(n.attr)
        elif isinstance(n, ast.Name):
            out.add(n.id)
    return out


def _kwarg(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _spawn_calls(tree: ast.AST):
    """Yield every ``subprocess.<run|Popen|…>`` call node in *tree*."""
    for scan_nodes, _binding in _scopes(tree):
        for node in scan_nodes:
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not isinstance(func, ast.Attribute) or func.attr not in _CALLS:
                continue
            if getattr(func.value, "id", None) not in ("subprocess", "sp"):
                continue
            yield node


def _composed_flag_offenders() -> dict[str, str]:
    """Spawns that build ``creationflags`` but decide nothing about a window.

    This is the exact shape of the bug in ``platform/windows_utils.py``: a *shared*
    Windows-aware wrapper ORed in ``CREATE_NEW_PROCESS_GROUP`` — which lets you send
    ``CTRL_BREAK_EVENT`` and says nothing about windows — so every caller flashed, while
    its own ``taskkill`` escalation 23 lines below did pass ``CREATE_NO_WINDOW``. Someone
    reaching for ``creationflags`` at all has the window question in front of them; leaving
    it unanswered is a slip, not a style.
    """
    found: dict[str, str] = {}
    for path in _python_files():
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "creationflags" not in source:
            continue  # cheap pre-filter before paying for the AST
        try:
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = path.relative_to(REPO).as_posix()
        for call in _spawn_calls(tree):
            cf = _kwarg(call, "creationflags")
            if cf is None:
                continue
            names = _flag_names(cf)
            if "CREATE_NEW_PROCESS_GROUP" not in names:
                continue
            if names & _WINDOW_DECIDING:
                continue
            found[f"{rel}:{call.lineno}"] = (
                "creationflags carries CREATE_NEW_PROCESS_GROUP but nothing that decides "
                "whether the child gets a window. From a windowless parent this flashes a "
                "console. Use navig.platform.process.creation_flags(new_group=True)"
            )
    return found


def test_windowless_entry_points_install_the_default() -> None:
    """The mechanism must stay WIRED — a helper nothing calls is documentation.

    Checked on the AST, not by substring: the installer's name appearing in a docstring or
    a comment is a claim, and this class of defect ("written · tested · never wired") is
    precisely a claim that was never a call.
    """
    missing: list[str] = []
    for rel, why in _WINDOWLESS_ENTRIES.items():
        path = REPO / rel
        assert path.exists(), f"{rel} does not exist — update _WINDOWLESS_ENTRIES"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        called = any(
            isinstance(n, ast.Call)
            and (
                (isinstance(n.func, ast.Name) and n.func.id == _INSTALLER)
                or (isinstance(n.func, ast.Attribute) and n.func.attr == _INSTALLER)
            )
            for n in ast.walk(tree)
        )
        if not called:
            missing.append(f"  {rel} — runs under {why}")
    assert not missing, (
        f"{_INSTALLER}() is not CALLED in:\n" + "\n".join(missing) + "\n\n"
        "Without it every console child of that windowless process gets a brand-new "
        "console window that flashes on the operator's screen."
    )


def test_no_spawn_composes_flags_while_ignoring_the_window() -> None:
    offenders = {k: v for k, v in _composed_flag_offenders().items() if k not in _BASELINE}
    assert not offenders, (
        "creationflags composed without answering the window question:\n"
        + "\n".join(f"  {site}\n      {why}" for site, why in sorted(offenders.items()))
    )


def test_baseline_entries_still_match() -> None:
    offenders = _composed_flag_offenders()
    stale = sorted(set(_BASELINE) - set(offenders))
    assert not stale, (
        f"_BASELINE names sites that no longer offend: {stale}. Delete them, or the next "
        "real defect at those lines is exempt by accident."
    )


def test_the_scan_actually_reads_the_tree() -> None:
    """Anti-vacuity. A scan that silently reads nothing looks exactly like a clean pass.

    This guard's own subject is a rule with an EMPTY result set, so without a floor it is
    indistinguishable from a scanner that walked zero files — and this repo has shipped a
    whole-tree guard that passed having scanned nothing (a worktree checked out empty)
    three separate times. Floors are measured, not round: on 2026-09-06 the scanner reached
    1600+ files, of which 30 named `creationflags`.
    """
    files = _python_files()
    assert len(files) > 1400, f"only {len(files)} python files reached the scanner"

    named = [
        f for f in files
        if "creationflags" in f.read_text(encoding="utf-8", errors="ignore")
    ]
    assert len(named) >= 10, (
        f"only {len(named)} files mention creationflags — the pre-filter or the scan root "
        "is wrong; this repo has ~30"
    )
    # The known-good composed-flags site must be REACHED, or the rule is unexercised.
    assert any(f.as_posix().endswith("commands/service.py") for f in files)


def test_canonical_module_is_the_only_place_defining_the_flag() -> None:
    """One truth table for the constants, so a second (wrong) one cannot grow.

    Every exemption names what makes the site safe, not that it is old.
    """
    allowed = {
        # The canonical definition.
        "core/navig/platform/process.py",
        # Predates the canonical module; the documented
        # `getattr(subprocess, "CREATE_NO_WINDOW", 0)` shape, and correct.
        "core/navig/daemon/single_instance.py",
        # These two are ONE function, vendored: navig-vault ships a byte-equivalent copy of
        # `set_owner_only_file_permissions` so the vault runs with navig ABSENT, and
        # test_vault_compat_parity.py compares the two ASTs. Importing the canonical helper
        # in either would break the standalone copy and the parity guard at once. Both sit
        # after a non-nt early return, so the local is unconditionally Windows.
        "core/navig/core/file_permissions.py",
        "plugins/navig-vault/navig_vault/_compat.py",
    }
    definers: list[str] = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        rel = path.relative_to(REPO).as_posix()
        if rel in allowed:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and "NO_WINDOW" in target.id.upper():
                        definers.append(f"{rel}:{node.lineno}:{target.id}")
    assert not definers, (
        "hand-rolled no-window constants (import from navig.platform.process instead):\n"
        + "\n".join(f"  {d}" for d in definers)
    )


def test_helper_never_returns_creationflags_on_posix() -> None:
    """``creationflags`` is a ValueError on POSIX, so the helper must omit the key entirely.

    Returning ``{"creationflags": 0}`` would look harmless and break every call site on
    Linux/macOS — the platforms this repo cannot run CI on.
    """
    import navig.platform.process as p

    if p.IS_WINDOWS:
        assert "creationflags" in p.spawn_kwargs()
    else:  # pragma: no cover — asserted on POSIX runners
        assert p.spawn_kwargs() == {}
        assert p.creation_flags() == 0


def test_interactive_children_are_not_silenced() -> None:
    """Hiding a password prompt turns a flash into a silent hang — strictly worse."""
    from navig.platform.process import creation_flags

    assert creation_flags(interactive=True) == 0


def test_installing_the_default_twice_does_not_chain_wrappers() -> None:
    """Idempotence has teeth: a second install must not wrap the wrapper.

    Each extra wrap adds a frame to EVERY subprocess spawn in the process, forever, and
    nothing would ever surface it.
    """
    import subprocess

    from navig.platform.process import IS_WINDOWS, install_windowless_spawn_default

    original = subprocess.Popen.__init__
    try:
        first = install_windowless_spawn_default(force=True)
        after_first = subprocess.Popen.__init__
        second = install_windowless_spawn_default(force=True)
        assert subprocess.Popen.__init__ is after_first, "the second install re-wrapped"
        assert second is False, "a repeat install must report that it did nothing"
        if not IS_WINDOWS:  # pragma: no cover
            assert first is False
    finally:
        subprocess.Popen.__init__ = original


def test_an_explicit_window_choice_is_never_overridden() -> None:
    """`tray_app` deliberately opens a real terminal; the window IS the feature there."""
    from navig.platform.process import (
        CREATE_NEW_CONSOLE,
        CREATE_NO_WINDOW,
        DETACHED_PROCESS,
        IS_WINDOWS,
        creation_flags,
    )

    if not IS_WINDOWS:  # pragma: no cover
        return
    assert creation_flags(base=CREATE_NEW_CONSOLE) == CREATE_NEW_CONSOLE
    assert creation_flags(base=DETACHED_PROCESS) == DETACHED_PROCESS
    assert not (creation_flags(base=CREATE_NEW_CONSOLE) & CREATE_NO_WINDOW)
