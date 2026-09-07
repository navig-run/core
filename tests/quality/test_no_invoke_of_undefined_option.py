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
``_declared_options`` fix that at the root; resolution went 8-false to 431/431 clean.
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path

import click
import typer

REPO = Path(__file__).resolve().parents[3]
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

_flag_cache: dict[tuple[str, str], set[str] | None] = {}
# Apps whose module resolved OUTSIDE this tree (a stale or non-editable install).
# Tracked separately from an introspection failure: it is an environment condition
# `navig doctor` -> Plugin Sources now reports, not a break in this scan — and it is
# ORDER-DEPENDENT, because an earlier test in the same process may already have
# imported the package from site-packages, which no sys.path change can undo.
_OUT_OF_TREE: set[tuple[str, str]] = set()


def _declared_options(module: str, attr: str) -> set[str] | None:
    """Every option name declared anywhere in the app's command tree."""
    key = (module, attr)
    if key in _flag_cache:
        return _flag_cache[key]

    names: set[str] | None = set()
    try:
        mod = importlib.import_module(module)
        app = getattr(mod, attr)
        if key == ("navig.cli", "app"):
            # The root app mounts its subcommands lazily; without this every option of
            # every external group reads as undefined. See the warning above.
            mod._register_external_commands()
        origin = getattr(mod, "__file__", None)
        if origin is None or not Path(origin).resolve().is_relative_to(REPO):
            # Resolved outside this tree (a stale or non-editable install). Report
            # nothing rather than a finding about code that is not under test — the
            # resolution floor below is what surfaces it if this becomes widespread.
            _OUT_OF_TREE.add(key)
            _flag_cache[key] = None
            return None
        command = typer.main.get_command(app)

        def walk(cmd: click.Command) -> None:
            for param in cmd.get_params(click.Context(cmd)):
                names.update(getattr(param, "opts", None) or [])
                names.update(getattr(param, "secondary_opts", None) or [])
            if isinstance(cmd, click.Group):
                for sub in cmd.commands.values():
                    walk(sub)

        walk(command)
    except Exception:  # noqa: BLE001 - an unimportable app is counted, not guessed at
        names = None

    _flag_cache[key] = names
    return names


def _literal_options(arglist: ast.List) -> list[str]:
    return [
        el.value
        for el in arglist.elts
        if isinstance(el, ast.Constant)
        and isinstance(el.value, str)
        and el.value.startswith("--")
        and el.value != "--"
    ]


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
            declared = _declared_options(*imported[target.id])
            if declared is None:
                if imported[target.id] in _OUT_OF_TREE:
                    skipped_out_of_tree += 1
                continue
            resolved += 1
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
