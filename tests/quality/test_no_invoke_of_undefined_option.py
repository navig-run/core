"""Guard: a CliRunner test that passes an option the app does not define.

Typer answers an unknown option with exit code **2** and a usage message. A test that
invokes one therefore exercises the argument parser and nothing else — and because the
assertions that follow are usually guarded by ``if result.exit_code == 0:`` or an
``exit_code in (0, 2)``, the test still PASSES. The command under test is never run.

Measured 2026-09-06 across 1,741 test files (150 of them in ``plugins/``): 431
``invoke()`` calls pass a literal ``--option``, all 431 resolvable, and **2** passed one
that does not exist. Both were in the same file and both were real::

    test_commands_tailscale_cmd.py:151  tailscale_app <- --json-out
    test_commands_tailscale_cmd.py:161  tailscale_app <- --json-out

Those two tests are named ``test_json_flag_exits_0`` and ``test_json_flag_outputs_json``,
and they were green while ``navig tailscale status --json`` emitted **unparseable JSON**
(#1254 — Rich hard-wraps once stdout is a pipe, breaking a line inside a string literal).
One asserted ``exit_code in (0, 2)``, where 2 is the "No such option" code it had just
triggered; the other put ``json.loads`` behind ``if result.exit_code == 0:``, so every
assertion was dead code. The bug they existed to catch shipped anyway.

⚠ The measurement took ONE correction, and it is the whole reason this guard is usable.
The first run reported **31** findings, 29 of them against ``navig.cli.app`` — which
looked like an artifact to exclude. It was not: the root app registers its subcommands
**lazily**, so every option of every external group read as undefined. Calling
``_register_external_commands()`` before resolving it dropped 31 -> 2 with no loss of
coverage. Excluding those files instead would have "fixed" the noise by blinding the
guard to 27 real vault invocations — the failure mode where a suppression silently
removes the scope it was meant to protect.

Scope, stated rather than implied:

* Only **literal** string options are checked; an option built from a variable is
  skipped, because its value is not knowable statically.
* An option counts as defined if ANY command in the app's tree declares it. This is
  deliberately lenient — it does not track which subcommand the list selects — and that
  leniency is what buys 0 false positives across 431 invocations. It still catches the
  real class, because a typo'd or renamed flag exists nowhere in the tree.
* An app that cannot be imported or introspected is counted, not guessed at; the
  resolution floor below fails if that starts happening at scale.

⚠⚠ Two false-positive sources were found by WIDENING, and both would have been wrong to
suppress. Scoping to ``core/tests`` looked complete and was not — ``plugins/`` holds 158
of the ``invoke()`` calls, so a core-only guard shipped blind to the larger half of its
own surface. Widening then produced 8 "findings" in ``navig-mobile`` for ``--gone`` and
``--count``, which ARE declared: the plugin resolved to a **stale non-editable copy in
site-packages** predating its ``ui`` sub-app, so the guard was judging this repo's tests
against another tree's code. ``_prefer_repo_sources()`` and the out-of-tree refusal in
``_introspect`` fix that at the root; resolution went 8-false to 431/431 clean.

⚠⚠ The third correction is this guard's own PREMISE, measured 2026-10-27 on Click 8.4.
A finding reads "Typer exits 2 on a usage error, so the assertions after the call are
dead" — true only when nothing swallows the token. A passthrough command
(``ignore_unknown_options`` **and** something to hold the leftovers: ``allow_extra_args``
or a variadic argument) forwards an undeclared option and exits 0, so its test runs in
full and the assertions after it are alive. Measured both shapes before trusting the
split: ignore-only + two fixed arguments still exits 2 (``Try 'pt2 --help' for help``),
ignore + extras or ignore + variadic exits 0 with the tokens in hand. So the scan now
resolves which command an invocation SELECTS and exempts only that one — a typo on any
other command of the same app is still a finding, and an ignore-only command of fixed
arguments is still a finding. Exempting on the flag alone would have silenced the very
class this guard exists to catch, one command shape over. The two ``navig-mobile``
passthrough tests (``fastboot --slot``, ``instruments --recursive``) found it: both
reported as dead, both green when run.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import typer

from navig.core.click_compat import click_module, is_group

# ⚠ The click TYPER runs on, never a bare `import click`. Typer 0.27 vendors click as
# `typer._click` and installs none; where a separate click happens to be installed it is a
# DIFFERENT class hierarchy, so `isinstance(get_command(app), click.Group)` is False and the
# walk below sees an EMPTY tree — every literal option then reads as undefined (measured
# 2026-10-27: 1,453 findings across the suite, and `test_the_forward_exempt_…` got `set()`
# for every passthrough). ``navig.core.click_compat`` resolves the right one on both.
click = click_module()

REPO = Path(__file__).resolve().parents[3]


def _tree_roots() -> tuple[Path, ...]:
    """`REPO`, plus every checkout linked to it.

    The gate venv installs the first-party plugins editable from the MAIN checkout, so
    inside `.dev/worktrees/<slug>` an import legitimately resolves to a path that is not
    under `REPO`. Calling that "outside the repo" made the guard skip the app, and the
    invoking test reported "the app must resolve before its shape means anything" — only
    in a worker where some earlier test imported the main-checkout copy first (measured
    2026-10-30, four whole-suite runs). A linked worktree is the same repository, so both
    roots count.
    """
    import subprocess

    roots = [REPO]
    try:
        out = subprocess.run(
            ["git", "-C", str(REPO), "worktree", "list", "--porcelain"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):  # noqa: BLE001 — a bare tree still works
        return tuple(roots)
    for line in out.stdout.splitlines():
        if line.startswith("worktree "):
            candidate = Path(line.split(" ", 1)[1].strip()).resolve()
            if candidate not in roots:
                roots.append(candidate)
    return tuple(roots)


_TREE_ROOTS = _tree_roots()
# The surface is every pytest suite that drives a Typer app, NOT `core/tests`. Measured
# before widening: plugins hold 158 invoke() calls across 19 files, ~95 passing a literal
# option — a core-only scan would have shipped blind to all of them.
SEARCH_ROOTS = ("core/tests", "plugins", "private")
SKIP_DIRS = {"node_modules", "venv", "site-packages", "__pycache__", "dist", "build", ".archive"}


def _prefer_repo_sources() -> None:
    """Make an import resolve to the tree being tested, not to what is installed.

    Plugins are separate distributions, and a machine can carry a STALE non-editable
    copy in site-packages. Resolving there judges this repo's tests against someone
    else's code: measured on `navig-mobile`, the installed copy predated the `ui`
    sub-app, so 8 invocations of real, declared options read as undefined.
    """
    roots = [REPO / "core"]
    roots += sorted((REPO / "plugins").glob("navig-*"))
    roots.append(REPO / "private" / "harbor")
    for root in roots:
        if root.is_dir():
            entry = str(root)
            if entry in sys.path:
                sys.path.remove(entry)
            sys.path.insert(0, entry)


_prefer_repo_sources()

# EMPTY on purpose. An entry here asserts a test SHOULD invoke an option its app does
# not define, which is never true — the option is either a typo, a rename that missed
# this call site, or a flag that was never implemented. Fix the test instead.
KNOWN_BAD: dict[str, str] = {}

# Vacuity floors. Measured today: 1741 test files (150 in plugins/), 431 invocations
# passing a literal option, all 431 resolvable.
MIN_TEST_FILES = 1400
MIN_INVOCATIONS = 350
MIN_RESOLUTION_RATE = 0.95
# Counted as a PRESENCE, never as `not startswith("core")` — an absence-phrased floor is
# itself vacuous: repoint the root and every path stops starting with `core/`, so the
# floor passes while the walk covers a fraction of the tree.
MIN_PLUGIN_FILES = 100

# One import answers three questions per app: which options it declares anywhere in its
# tree, which command paths that tree has, and which of those paths forward an unknown
# option instead of failing on it. Cached together so the walk runs exactly once.
_Introspected = tuple[set[str] | None, set[tuple[str, ...]], set[tuple[str, ...]]]
_introspect_cache: dict[tuple[str, str], _Introspected] = {}

#: Why a key could not be introspected, so the assertion below names the real cause
#: instead of only "the app must resolve".
_INTROSPECT_FAILURE: dict[tuple[str, str], str] = {}
# Apps whose module resolved OUTSIDE this tree (a stale or non-editable install).
# Tracked separately from an introspection failure: it is an environment condition
# `navig doctor` -> Plugin Sources now reports, not a break in this scan — and it is
# ORDER-DEPENDENT, because an earlier test in the same process may already have
# imported the package from site-packages, which no sys.path change can undo.
_OUT_OF_TREE: set[tuple[str, str]] = set()


def _forwards_instead_of_failing(cmd) -> bool:
    """Would this command hand an unknown option to its own arguments instead of exit 2?

    Both halves are required, measured on Click 8.4 before this split was trusted:

    * ``ignore_unknown_options`` + a holder for the leftovers (``allow_extra_args``, or
      an argument declared ``nargs=-1``) → exit 0, tokens intact: a passthrough such as
      ``navig mobile android fastboot -w --slot other``.
    * ``ignore_unknown_options`` alone on a command of fixed arguments → still exit 2
      (``Try 'pt2 --help' for help``), so a test behind it CAN be dead and must stay
      reported — ``navig notify route set`` is that shape.

    Keying the exemption on the flag alone would blind the guard to exactly its own
    class, one command shape over, which is why the exemption is per selected command
    and never per app.
    """
    settings = getattr(cmd, "context_settings", None) or {}
    if not (
        settings.get("ignore_unknown_options") or getattr(cmd, "ignore_unknown_options", False)
    ):
        return False
    if settings.get("allow_extra_args") or getattr(cmd, "allow_extra_args", False):
        return True
    return any(getattr(param, "nargs", 1) == -1 for param in getattr(cmd, "params", []))


def _cached_but_unreal(module: str) -> bool:
    """True when `sys.modules[module]` is a sentinel another test left behind.

    `tests/plugins/test_core_standalone.py` installs raising shims and `ModuleType`
    placeholders to simulate a no-plugins install. A placeholder has no `__file__`, which
    is exactly what `_introspect` reads to decide whether the app under test lives in this
    tree — so a leftover shim made it return "resolved outside the repo" and the caller
    assert "the app must resolve" (measured in the whole-suite run of 2026-10-30).
    """
    mod = sys.modules.get(module)
    if mod is None:
        return False
    return getattr(mod, "__file__", None) is None


def _import_app(module: str):
    """Import the plugin app, shedding a cache another test poisoned.

    Retried once with the package's `sys.modules` entries dropped, because the whole
    suite runs this file in a worker that has already simulated a no-plugins install. A
    genuinely unimportable app fails both times; the retry cannot hide a defect.
    """
    if _cached_but_unreal(module):
        _shed_package(module)
    try:
        return importlib.import_module(module)
    except Exception as first:  # noqa: BLE001 — the retry below is the point
        _shed_package(module)
        try:
            return importlib.import_module(module)
        except Exception:  # noqa: BLE001 — report the FIRST failure, it names the cause
            raise first


def _shed_package(module: str) -> None:
    package = module.split(".")[0]
    for name in [n for n in list(sys.modules) if n == package or n.startswith(package + ".")]:
        sys.modules.pop(name, None)


def _introspect(module: str, attr: str) -> _Introspected:
    """(options declared anywhere in the tree, every command path, passthrough paths).

    Passthrough paths are collected here rather than as a suppression list: an
    invocation selecting one does not produce the exit 2 a finding is founded on, so
    reporting it would be a false claim about a test that runs. They are never guessed
    from the app as a whole — see ``_forwards_instead_of_failing``.
    """
    key = (module, attr)
    if key in _introspect_cache:
        return _introspect_cache[key]

    names: set[str] | None = set()
    paths: set[tuple[str, ...]] = set()
    passthrough: set[tuple[str, ...]] = set()
    try:
        mod = _import_app(module)
        app = getattr(mod, attr)
        if key == ("navig.cli", "app"):
            # The root app mounts its subcommands lazily; without this every option of
            # every external group reads as undefined. See the warning above.
            #
            # ⚠ `register_all=True` is REQUIRED, not a belt-and-braces. The bare call
            # dispatches on `sys.argv`: under pytest that is `pytest tests/quality/…`,
            # which selects the "skip external imports entirely" branch, so every
            # plugin-provided group (`vault`, `cred`, …) stayed unmounted and its options
            # read as undefined — 29 findings against `navig vault add --key` alone
            # (measured 2026-10-27). The three sibling guards already register this way.
            mod._register_external_commands(register_all=True)
        origin = getattr(mod, "__file__", None)
        resolved_origin = Path(origin).resolve() if origin else None
        if resolved_origin is None or not any(
            resolved_origin.is_relative_to(root) for root in _TREE_ROOTS
        ):
            # Resolved outside this tree (a stale or non-editable install). Report
            # nothing rather than a finding about code that is not under test — the
            # resolution floor below is what surfaces it if this becomes widespread.
            _OUT_OF_TREE.add(key)
            _introspect_cache[key] = (None, set(), set())
            return None, set(), set()
        command = typer.main.get_command(app)

        def walk(cmd, path: tuple[str, ...]) -> None:
            paths.add(path)
            for param in cmd.get_params(click.Context(cmd)):
                names.update(getattr(param, "opts", None) or [])
                names.update(getattr(param, "secondary_opts", None) or [])
            if _forwards_instead_of_failing(cmd):
                passthrough.add(path)
            if is_group(cmd):
                for name, sub in cmd.commands.items():
                    walk(sub, path + (name,))

        walk(command, ())
    except Exception as exc:  # noqa: BLE001 - an unimportable app is counted, not guessed at
        # Deliberately NOT cached: a whole-suite run reached this with the plugin import
        # broken by an earlier test's `sys.modules`/`sys.argv` state, and the cached None
        # then made every later caller in the process assert "the app must resolve"
        # (measured 2026-10-30). The out-of-tree verdict above stays cached — that one is
        # a property of the install, not of the moment.
        _INTROSPECT_FAILURE[key] = f"{type(exc).__name__}: {exc}"
        return None, set(), set()

    _introspect_cache[key] = (names, paths, passthrough)
    return names, paths, passthrough


def _literal_options(arglist: ast.List) -> list[str]:
    return [
        el.value
        for el in arglist.elts
        if isinstance(el, ast.Constant)
        and isinstance(el.value, str)
        and el.value.startswith("--")
        and el.value != "--"
    ]


def _leaf_path(arglist: ast.List, paths: set[tuple[str, ...]]) -> tuple[str, ...]:
    """Which command an invocation selects: the deepest leading run of subcommand names.

    The idiom under test puts the command path first and options after it, so the run
    stops at the first ``-`` token. Tokens that are not commands — the leaf's own
    arguments, as in ``["ios", "instruments", "ls", ...]`` — drop out, because only a
    prefix that IS a path counts. Nothing leading selects the root: an invocation the
    scan cannot pin to a subcommand is exempted only if the app root itself forwards.
    """
    tokens: list[str] = []
    for el in arglist.elts:
        if not isinstance(el, ast.Constant) or not isinstance(el.value, str):
            break
        if el.value.startswith("-"):
            break
        tokens.append(el.value)

    leaf: tuple[str, ...] = ()
    for i in range(1, len(tokens) + 1):
        prefix = tuple(tokens[:i])
        if prefix in paths:
            leaf = prefix
    return leaf


def _test_files() -> list[Path]:
    found: list[Path] = []
    for root in SEARCH_ROOTS:
        base = REPO / root
        if not base.is_dir():
            continue
        for path in base.rglob("test_*.py"):
            # Test the RELATIVE parts: the repo itself may live under a dot-directory
            # (a `.dev/worktrees/<slug>` checkout), and filtering on the absolute parts
            # rejects every file in the tree. The vacuity floor caught exactly that.
            rel_parts = path.relative_to(REPO).parts
            if any(part in SKIP_DIRS or part.startswith(".") for part in rel_parts):
                continue
            found.append(path)
    return sorted(set(found))


def _scan() -> tuple[int, int, int, list[str], int]:
    files = _test_files()
    invocations = 0
    resolved = 0
    skipped_out_of_tree = 0
    findings: list[str] = []

    for path in files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue

        imported: dict[str, tuple[str, str]] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("navig"):
                for alias in node.names:
                    imported[alias.asname or alias.name] = (node.module, alias.name)
        if not imported:
            continue

        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "invoke"
                and len(node.args) >= 2
            ):
                continue
            target = node.args[0]
            arglist = node.args[1]
            if not (isinstance(target, ast.Name) and target.id in imported):
                continue
            if not isinstance(arglist, ast.List):
                continue
            options = _literal_options(arglist)
            if not options:
                continue

            invocations += 1
            declared, paths, passthrough = _introspect(*imported[target.id])
            if declared is None:
                if imported[target.id] in _OUT_OF_TREE:
                    skipped_out_of_tree += 1
                continue
            resolved += 1
            if _leaf_path(arglist, paths) in passthrough:
                # The command this call selects forwards the token instead of failing on
                # it (measured, see `_forwards_instead_of_failing`): no usage error, so
                # the assertions after the call are not dead and there is no finding.
                continue
            for option in options:
                bare = option.split("=")[0]
                if bare not in declared:
                    findings.append(
                        f"{path.relative_to(REPO).as_posix()}:{node.lineno}  "
                        f"{target.id} <- {bare}"
                    )

    # Two different numbers on purpose. `invocations` is what the SCAN found and is
    # environment-independent, so the vacuity floor can use it. Out-of-tree apps are
    # reported separately and belong only in the resolution denominator — folding them
    # into the count made the floor itself depend on which installs happen to be stale.
    return len(files), invocations, resolved, findings, skipped_out_of_tree


def test_no_test_invokes_an_option_its_app_does_not_define() -> None:
    _, _, _, findings, _ = _scan()
    unexplained = sorted({f for f in findings if f.split("  ")[0] not in KNOWN_BAD})
    assert not unexplained, (
        "these tests pass an option the app does not define, so Typer exits 2 on a "
        "usage error and the command under test never runs:\n  "
        + "\n  ".join(unexplained)
        + "\nThe assertions after such a call are usually dead (guarded by "
        "`exit_code == 0`) or vacuous (`exit_code in (0, 2)` accepts the usage error "
        "itself). Use the option the command actually declares."
    )


def test_the_forward_exempt_command_is_the_one_the_invocation_selects() -> None:
    """The forward-exempt is per selected command — never a blanket for its app.

    Without this, `test_no_test_invokes_an_option_its_app_does_not_define` passing only
    says "no findings", which a broad exemption would also produce. These assertions pin
    the exemption to a command measured to exit 0 with the token forwarded, and hold it
    away from a command of the SAME app that still answers an unknown option with a
    usage error — so the guard cannot be satisfied by widening.
    """
    module, attr = "navig_mobile.commands.mobile", "mobile_app"
    declared, paths, passthrough = _introspect(module, attr)
    assert declared is not None, (
        "the app must resolve before its shape means anything — "
        f"{_INTROSPECT_FAILURE.get((module, attr), 'import produced no app')}"
    )
    assert "--undefined-in-this-app" not in declared  # the probe flag is genuinely undefined

    def selects(*argv: str) -> tuple[str, ...]:
        arglist = ast.List(elts=[ast.Constant(v) for v in argv], ctx=ast.Load())
        return _leaf_path(arglist, paths)

    # Both passthroughs the scan used to report: selected, and exempt because they forward.
    assert selects("android", "fastboot", "--undefined-in-this-app") in passthrough
    assert selects("ios", "instruments", "ls", "--undefined-in-this-app") in passthrough
    # A command of the same app that does NOT forward: `ios recovery enter` takes fixed
    # arguments and ignore-only still exits 2, so a test behind it can still be dead.
    assert selects("ios", "recovery", "enter", "--undefined-in-this-app") not in passthrough
    # An invocation that starts with an option is pinned to no subcommand, so it gains
    # no exemption it did not earn (the root is not a passthrough).
    assert selects("--undefined-in-this-app", "status") not in passthrough


def test_the_scan_still_sees_the_suite_it_polices() -> None:
    files, invocations, resolved, _, out_of_tree = _scan()
    assert files >= MIN_TEST_FILES, (
        f"only {files} test files found (expected >= {MIN_TEST_FILES}) — the scan root "
        "moved and this guard is reading almost nothing."
    )
    plugin_files = sum(
        1 for f in _test_files() if f.relative_to(REPO).as_posix().startswith("plugins/")
    )
    assert plugin_files >= MIN_PLUGIN_FILES, (
        f"only {plugin_files} plugin test files reached (expected >= {MIN_PLUGIN_FILES}) — "
        "the walk stopped covering plugins/, which holds 158 of the invoke() calls."
    )
    assert invocations >= MIN_INVOCATIONS, (
        f"only {invocations} invoke() calls pass a literal option (expected >= "
        f"{MIN_INVOCATIONS}) — the CliRunner idiom changed and this guard is holding "
        "nothing."
    )
    checkable = invocations - out_of_tree
    rate = resolved / checkable if checkable else 0.0
    assert rate >= MIN_RESOLUTION_RATE, (
        f"only {resolved}/{checkable} ({rate:.0%}) of introspectable apps resolved "
        f"(expected >= {MIN_RESOLUTION_RATE:.0%}). An app that fails to import is "
        "skipped, so a drop here silently shrinks coverage. Note this denominator "
        "already EXCLUDES apps resolving outside the repo — those are an environment "
        "condition (`navig doctor` -> Plugin Sources), and whether they appear at all "
        "depends on what an earlier test in this process imported first."
    )
