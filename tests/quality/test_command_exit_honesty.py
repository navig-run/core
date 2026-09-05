"""A command that PRINTS an error must not EXIT 0.

`ch.error(...)` followed by a bare `return` is the silent-failure shape #358 swept:
the user sees ✗, the shell sees success. Scripts cannot branch on it, and since #345
the operations ledger records the run as SUCCESS — a failed command distilled into a
working recipe step.

The sweep was not self-sustaining. #358 listed eight modules as done, yet `docker.py`
still had two live cases (#728: `docker exec` warned about a failed container command
and exited 0, so `navig docker exec app "npm run migrate" && ./deploy.sh` deployed on
top of a failed migration) and `backup.py` had one. Nothing re-checked them, so the
class quietly grew back.

THE SWEEP IS FINISHED, so this is now a WHOLE-TREE BAN. It was a ratchet for most of
its life — `SWEPT_MODULES` was an opt-IN list of reviewed modules — and that shape had
one structural flaw: a module nobody remembered to enrol started life unguarded. That
is exactly how the class grew back after #358 declared it swept, and why #728/#734/#736
each found live paths in modules the sweep had never looked at.

Now every file in navig/commands/ is scanned, so a NEW command module is covered the
moment it lands. The only exemptions are the three functions in `RENDERING_ONLY`, and
they are keyed by FUNCTION rather than module, so a new bug elsewhere in those same
files still fails. `test_every_exemption_is_still_needed` deletes any that stop
suppressing something, because a stale exemption reads as "reviewed and fine" while
quietly covering whatever the function grows into.

TWO SHAPES are covered.

1. Same frame — the error and the return in one function.

2. CALLER-SIDE SWALLOW — the failure is printed in a helper and dropped by its caller:

       final = _resolve_command(...)   # prints "File not found", returns None
       if final is None:
           return                      # ← exits 0

   This was the guard's documented blind spot until it turned out to be the more
   dangerous half. `navig run --file missing.sh` exited 0 through exactly this, and
   in `remote.py` a single-frame scan actively pointed at the WRONG code: it flagged
   eight `-> str | None` helpers that were all correct while missing the two real
   bugs in their caller. `ahk.py` had 17 more (`navig ahk volume` on Linux printed
   "only available on Windows" and exited 0), `config_backup.py` 3.

   A "failure helper" is identified by shape, not by name: a value-returning function
   that itself contains an error-then-return-None pair. That deliberately does NOT
   match a helper which returns None *silently* for a legitimate "absent" answer —
   only one that announces a failure and hands back None.

KNOWN REACH — still do not read a pass as "this module cannot exit 0 on failure".
Both shapes are syntactic and intra-module. A failure carried across modules, or
signalled by something other than None (a falsy dataclass, an empty list, a status
enum), is not visible here.

The correct exit codes, following #358:
  * `typer.Exit(2)` — usage class: not found, missing required argument, no config.
  * `typer.Exit(1)` — operation failure; `from e` when an exception drove it.
And a path that is genuinely NOT a failure (an optional component that is simply
absent) must not be announced with `ch.error` at all — say it is a skip and return 0,
as `backup_hestia` does. That keeps the exit code and the on-screen glyph agreeing,
which is the whole point.
"""

from __future__ import annotations

import ast
from pathlib import Path

COMMANDS = Path(__file__).resolve().parents[2] / "navig" / "commands"
# Plugins ship CLI verbs too (`navig games doctor`, `navig github status`, …), so the
# same rule has to reach them or the last un-guarded command surface stays un-guarded.
PLUGINS = COMMANDS.parents[2] / "plugins"


def _scanned_files() -> list[Path]:
    """Every module that can own a CLI failure path: core commands + plugin sources."""
    files = sorted(COMMANDS.glob("*.py"))
    if PLUGINS.is_dir():
        for pkg in sorted(p for p in PLUGINS.glob("navig-*") if p.is_dir()):
            files += sorted(
                f
                for f in pkg.rglob("*.py")
                if not {"tests", "build", "dist", ".venv"} & set(f.parts)
            )
    return files

# Modules whose error-then-return paths have been individually reviewed. Each stays
# at zero forever. Add a module here only once you have actually swept it.
# THE SWEEP IS COMPLETE — this is now a WHOLE-TREE ban, not a ratchet.
#
# Every module in navig/commands/ is scanned. A NEW command module is covered the
# moment it lands, which is precisely what the ratchet could not do: SWEPT_MODULES
# was an opt-IN list, so a module nobody added started life unguarded, and that is
# how #728/#734/#736 grew back after #358 declared the class swept.
#
# RENDERING_ONLY is the whole exemption surface: three functions where `ch.error`
# renders a message rather than deciding a command's outcome. It is keyed by
# FUNCTION, not module — a new bug anywhere else in these same files still fails.
RENDERING_ONLY: dict[str, frozenset[str]] = {
    # Keyed by a path SUFFIX, not a bare module name: 34 stems collide once the plugin
    # packages are in scope (`games.py` exists in both commands/ and deck_routes/), so a
    # stem key would silently exempt a file nobody reviewed.
    #
    # The one entry is the "wrong frame" shape this guard's docstring warns about — the
    # error is rendered here and the exit code is decided one frame up.
    "navig_games/commands/games.py": frozenset({
        # A nested check() that prints and sets `nonlocal ok`; cmd_doctor reads that flag
        # and raises typer.Exit(1). Forcing the raise into check() would abort the doctor
        # on its FIRST failure and hide every later one — the opposite of what it is for.
        "check",
    }),
    # `navig_mobile/commands/mobile.py::_report_scan` WAS exempt, deferring "should a
    # spyware scan exit non-zero on IOC detections?" as a product decision. It has been
    # made: yes. `navig mobile scan spyware && echo "device clean"` printed *device clean*
    # on a flagged phone, and the exemption's own taxonomy — a detection is a finding, not
    # a failure to scan — is served by the MESSAGE and the `--json` payload, which carry
    # `detections`/`returncode`/`clean`. It is not served by letting a flagged device and
    # a clean one return the same code to a script. Exit 0 now means `clean` and nothing
    # else. See the reporter's docstring.
    # Core ships ZERO exemptions. Three candidates (a severity dispatcher, a per-row glyph
    # in a listing, a help printer whose caller exits) were all resolved by REFACTORING
    # instead, each leaving the code clearer than an exemption would have.
}


def _exempt_functions(path: Path) -> frozenset[str]:
    posix = path.as_posix()
    names: set[str] = set()
    for suffix, functions in RENDERING_ONLY.items():
        if posix.endswith(suffix):
            names |= set(functions)
    return frozenset(names)

# Names on the console helper that render a FAILURE to the user.
ERROR_SINKS = frozenset({"error", "failure"})

# Receivers that are NOT a user-facing console. `<x>.error(...)` alone is ambiguous:
# it is the console helper (`ch`, `_ch`, and antivirus's `c = _Console()`), a LOGGER,
# or a result object's own field. Measured across core + plugins: ch 1030 · logger 251
# · c 78 · _ch 48 · then PublishReceipt/ValidationResult/DeliveryReceipt/DeliveryResult.
#
# This is a BLOCKLIST on purpose, not an allowlist of console aliases. An allowlist
# that misses a new alias silently stops guarding real commands — a false NEGATIVE
# ships the bug. A blocklist that misses a new logger alias only produces noise, which
# somebody notices and fixes. Err toward noise in a guard.
NON_CONSOLE_RECEIVERS = frozenset({"logger", "log", "_log", "_logger", "LOG", "logging"})


def _is_error_call(node: ast.stmt) -> str | None:
    """`<console>.error(...)` / `.failure(...)` as a bare statement.

    A `logger.error(...)` inside a daemon loop is not a command announcing failure —
    navig-audio's session manager and wake-word engine are full of them, and treating
    them as exit-honesty findings would have demanded a `typer.Exit` from a background
    coroutine that has no exit code to give.
    """
    if not isinstance(node, ast.Expr) or not isinstance(node.value, ast.Call):
        return None
    fn = node.value.func
    if (
        isinstance(fn, ast.Attribute)
        and fn.attr in ERROR_SINKS
        and isinstance(fn.value, ast.Name)
    ):
        receiver = fn.value.id
        if receiver in NON_CONSOLE_RECEIVERS:
            return None
        # `PublishReceipt.error(...)` / `ValidationResult.error(...)` — a CLASS name is
        # a constructor or a field, never an instance you print through.
        if receiver[:1].isupper():
            return None
        return f"{receiver}.{fn.attr}"
    return None


def _is_bare_return(node: ast.stmt) -> bool:
    """`return` or `return None` — the shapes that yield exit status 0."""
    return isinstance(node, ast.Return) and (
        node.value is None
        or (isinstance(node.value, ast.Constant) and node.value.value is None)
    )


def _returns_a_value(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    """True for a helper that signals failure by RETURNING, not by exiting.

    `remote.py`'s `_resolve_command(...) -> str | None` prints the specific reason
    and returns None so its caller can decide — that is the correct contract for a
    helper, not a silent failure, and flagging it would be this guard repeating the
    #729 mistake of rejecting the very pattern the codebase wants.

    A nested def has its own contract, so we must NOT descend into one — an inner
    helper's `return x` would otherwise excuse the outer command. `ast.walk` cannot
    express that (it yields every descendant), so walk the children explicitly.
    """
    def _scan(node: ast.AST) -> bool:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue  # its own contract — do not descend
            if isinstance(child, ast.Return) and child.value is not None:
                if not (
                    isinstance(child.value, ast.Constant) and child.value.value is None
                ):
                    return True
            if _scan(child):
                return True
        return False

    return _scan(fn)


def _descends_into(stmt: ast.stmt) -> bool:
    """Whether a walker should recurse into this statement's body.

    False at a nested `def`/`async def`. `_returns_a_value` already documents the
    principle — "a nested def has its own contract, so we must NOT descend into
    one" — but the three walkers below did descend, which broke it in both
    directions:

      * FALSE POSITIVE. A nested helper that legitimately announces a failure and
        returns None (the `_resolve_command` shape this guard exists to EXEMPT) was
        re-flagged through its enclosing command: the helper was correctly skipped
        as itself, then scanned again as part of the outer function, which returns
        nothing. Rejecting correct code is how a guard gets deleted (#729).
      * DOUBLE REPORT. `ast.walk` in `_offenders` visits every nested def in its own
        right, so anything inside one was reported twice — once under the inner
        name, once under the outer. That is also why the remaining-exposure counts
        quoted in the sweep notes were upper bounds rather than counts (voice.py
        reported 8 sites for 4 real ones).

    Coverage is unchanged: a nested def is still scanned, by `_offenders`, against
    its OWN contract.
    """
    return not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef))


def _same_receiver_call(stmt: ast.stmt, receiver: str) -> bool:
    """`<receiver>.<anything>(...)` — another line of output on the SAME helper.

    Deliberately keyed to the receiver rather than "any call": an error followed by
    `ch.info("try X")` is still an error-then-return, but an error followed by
    `store.save(...)` is real work and must not be skipped over.
    """
    if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)):
        return False
    fn = stmt.value.func
    return (
        isinstance(fn, ast.Attribute)
        and isinstance(fn.value, ast.Name)
        and fn.value.id == receiver
    )


def _quiet_guarded_error(stmt: ast.stmt) -> str | None:
    """`if <cond>: <only error calls>` — an error wrapped in a display-suppression guard.

    Matched narrowly on purpose: the inner block must contain NOTHING but error calls, so
    an `if` that also does real work (or reports at another level) is not a suppression
    wrapper and is left to the ordinary pairing.
    """
    if not isinstance(stmt, ast.If) or stmt.orelse or not stmt.body:
        return None
    sinks = [_is_error_call(inner) for inner in stmt.body]
    if not all(sinks):
        return None
    return sinks[0]


def _walk_bodies(body: list[ast.stmt], hits: list[tuple[int, str]]) -> None:
    for i, stmt in enumerate(body):
        sink = _is_error_call(stmt)
        if sink:
            # Skip further output on the same helper before looking for the return.
            # Requiring ADJACENCY was a blind spot, and the shape that evades it is
            # the most natural one there is — an error followed by a hint:
            #     ch.error("Could not detect package manager.")
            #     ch.info("Supported: apt-get, yum, dnf, …")
            #     return                      # <- exit 0
            # Measured across the swept modules when this was added: 5 sites, every
            # one a real command reporting failure and exiting 0 (hestia list-users
            # and list-domains on a non-Hestia box, `remote install-package` with no
            # package manager, an invalid trigger type, a failed decryption).
            # `_swallows` below already allowed for an intervening log line; this is
            # the same reasoning applied to the check it belongs to.
            j = i + 1
            while j < len(body) and _same_receiver_call(body[j], sink.split(".")[0]):
                j += 1
            if j < len(body) and _is_bare_return(body[j]):
                hits.append((stmt.lineno, sink))
        elif _quiet_guarded_error(stmt):
            # THE DISPLAY-SUPPRESSION WRAPPER. The error is nested one level down and the
            # return is at THIS level, so the two are not siblings and the pairing above
            # never sees them:
            #     if not app_name:
            #         if not quiet:                     # <- suppresses OUTPUT only
            #             ch.error("App name is required")
            #         return                            # <- exit 0 regardless
            # `quiet` decides whether the user is TOLD, never whether it failed, so the
            # exit code must not depend on it. Measured when this was added: 6 sites — the
            # 3 in `navig app remove` were real (no active host / unknown app both printed
            # and exited 0), and the other 3 were `inspect_host`, a `dict | None` helper
            # whose `return None` IS its failure signal. Those are skipped for free by
            # _returns_a_value, which is why this reuses the same walk rather than adding
            # a second scan with its own idea of what a helper is.
            j = i + 1
            if j < len(body) and _is_bare_return(body[j]):
                hits.append((stmt.lineno, _quiet_guarded_error(stmt)))
        if not _descends_into(stmt):
            continue  # its own contract — see _descends_into
        for field in ("body", "orelse", "finalbody"):
            nested = getattr(stmt, field, None)
            if isinstance(nested, list) and nested and isinstance(nested[0], ast.stmt):
                _walk_bodies(nested, hits)
        for handler in getattr(stmt, "handlers", []):
            _walk_bodies(handler.body, hits)


def _failure_helpers(tree: ast.AST) -> set[str]:
    """Functions that ANNOUNCE a failure and hand back None.

    Identified by shape: a value-returning function that itself contains an
    error-then-return-None pair (`_resolve_command`, `_get_adapter`,
    `_require_input_file`). A helper that returns None *silently* for a legitimate
    "absent" answer is not a failure helper and must not be matched — the point is
    that the user has already been shown a ✗ by the time the caller decides.
    """
    helpers: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _returns_a_value(node):
            continue
        hits: list[tuple[int, str]] = []
        _walk_bodies(node.body, hits)
        if hits:
            helpers.add(node.name)
    return helpers


def _called_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Await):
        return _called_name(node.value)
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name):
            return node.func.id
        if isinstance(node.func, ast.Attribute):
            return node.func.attr
    return None


def _tests_for_none(test: ast.expr, name: str) -> bool:
    """`x is None` · `not x` · either of those inside an `and`/`or`."""
    if isinstance(test, ast.Compare) and isinstance(test.left, ast.Name):
        if test.left.id == name and any(isinstance(op, ast.Is) for op in test.ops):
            return any(
                isinstance(c, ast.Constant) and c.value is None for c in test.comparators
            )
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        return isinstance(test.operand, ast.Name) and test.operand.id == name
    if isinstance(test, ast.BoolOp):
        return any(_tests_for_none(v, name) for v in test.values)
    return False


def _swallows(fn: ast.AST, helpers: set[str], hits: list[tuple[int, str]]) -> None:
    """`x = <failure helper>(...)` then `if x is None: return` — inside a command."""
    def walk(body: list[ast.stmt]) -> None:
        for i, stmt in enumerate(body):
            if isinstance(stmt, ast.Assign) and len(stmt.targets) == 1:
                target = stmt.targets[0]
                called = _called_name(stmt.value)
                if isinstance(target, ast.Name) and called in helpers:
                    # The check normally follows immediately; allow a couple of
                    # statements so an intervening log line does not hide it.
                    for follow in body[i + 1 : i + 4]:
                        if (
                            isinstance(follow, ast.If)
                            and _tests_for_none(follow.test, target.id)
                            and follow.body
                            and _is_bare_return(follow.body[-1])
                            and not any(
                                isinstance(n, ast.Raise) for n in ast.walk(follow)
                            )
                        ):
                            hits.append((follow.lineno, f"{target.id} = {called}(...)"))
            if not _descends_into(stmt):
                continue  # its own contract — see _descends_into
            for field in ("body", "orelse", "finalbody"):
                nested = getattr(stmt, field, None)
                if isinstance(nested, list) and nested and isinstance(nested[0], ast.stmt):
                    walk(nested)
            for handler in getattr(stmt, "handlers", []):
                walk(handler.body)

    walk(fn.body)


def _raises_anywhere(fn: ast.AST) -> bool:
    """A `raise` in the function's own body (not inside a nested def)."""
    def scan(node: ast.AST) -> bool:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(child, ast.Raise) or scan(child):
                return True
        return False

    return scan(fn)


def _tail_errors(body: list[ast.stmt], hits: list[tuple[int, str]]) -> None:
    """An error that is the LAST statement of its block — no `return` to find.

    The other half of the blind spot: `_walk_bodies` looks for a return AFTER the
    error, so an error that simply ends its block (and the function) is invisible.
    Falling off the end is `return None`, which is exit 0:

        except json.JSONDecodeError:
            ch.error("Failed to parse HestiaCP output")   # <- function ends, exit 0

    Measured when added: 5 sites, all real — `remote install-package` printing
    "❌ Installation failed" and exiting 0, `trigger fire` printing "Trigger
    failed" and exiting 0, `_save_triggers` swallowing a write failure so the
    caller reported a trigger it never persisted, and two hestia parse failures.

    LIMITATION, stated so nobody reads this as complete: it exempts any function
    containing a `raise` ANYWHERE, because "error here, raise later" is a
    legitimate and common shape (see `backup_system_config`). Deciding whether a
    raise is actually reachable AFTER a given error is path-sensitive analysis,
    which is more than a source guard should attempt. Without the exemption the
    same scan reports 18 sites, 13 of them legitimate. So this catches a subset by
    design; `config_backup.import_config`'s catch-all is a known miss (it raises in
    an earlier validation branch) and is covered by a behaviour test instead.
    """
    for i, stmt in enumerate(body):
        sink = _is_error_call(stmt)
        if sink:
            j = i + 1
            while j < len(body) and _same_receiver_call(body[j], sink.split(".")[0]):
                j += 1
            if j >= len(body):
                hits.append((stmt.lineno, sink))
        if not _descends_into(stmt):
            continue  # its own contract — see _descends_into
        for field in ("body", "orelse", "finalbody"):
            nested = getattr(stmt, field, None)
            if isinstance(nested, list) and nested and isinstance(nested[0], ast.stmt):
                _tail_errors(nested, hits)
        for handler in getattr(stmt, "handlers", []):
            _tail_errors(handler.body, hits)


def _offenders(path: Path) -> list[str]:
    # utf-8-sig, not utf-8: a BOM is a SyntaxError to ast.parse, and a file carrying
    # one runs fine while going invisible to every AST guard.
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    helpers = _failure_helpers(tree)
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if _returns_a_value(node):
            continue  # a helper that signals failure to its caller — see above
        hits: list[tuple[int, str]] = []
        _walk_bodies(node.body, hits)
        out += [
            f"{path.name}:{lineno}  in {node.name}()  {sink}(...) then bare return"
            for lineno, sink in hits
        ]
        if not _raises_anywhere(node):
            tails: list[tuple[int, str]] = []
            _tail_errors(node.body, tails)
            out += [
                f"{path.name}:{lineno}  in {node.name}()  {sink}(...) ends the "
                "function — falling off the end exits 0"
                for lineno, sink in tails
            ]
        swallowed: list[tuple[int, str]] = []
        _swallows(node, helpers, swallowed)
        out += [
            f"{path.name}:{lineno}  in {node.name}()  {call} already printed the "
            "error — returning here exits 0"
            for lineno, call in swallowed
        ]
    return out


def test_no_command_prints_an_error_then_exits_zero() -> None:
    """The whole tree, not a list of modules someone remembered to enrol."""
    offenders: list[str] = []
    scanned = 0
    for path in _scanned_files():
        scanned += 1
        exempt = _exempt_functions(path)
        for finding in _offenders(path):
            if not any(f" in {fn}()" in finding for fn in exempt):
                offenders.append(finding)

    assert scanned > 300, (
        f"only {scanned} modules scanned - core commands plus the plugin packages are "
        "far more than that. The globs are looking in the wrong place and this guard "
        "is checking nothing."
    )
    assert not offenders, (
        "These print an error and then return, so the process exits 0 - the user "
        "sees the failure glyph and the shell sees success:\n  "
        + "\n  ".join(offenders)
        + "\n\nRaise typer.Exit(2) for a usage error (not found / missing "
        "argument), or typer.Exit(1) for an operation failure (`from e` if an "
        "exception drove it). If the path is genuinely NOT a failure, stop calling "
        "ch.error and say it is a skip - then exit 0 honestly."
    )


# The catch-all-eats-a-deliberate-exit class lives in `test_no_swallowed_exit.py`,
# which owns it tree-wide (core + plugins) in BOTH severities: swallowed outright
# (exit 0) and caught-then-rewritten (wrong non-zero code). It was briefly duplicated
# here, scoped to this file's module list; a second detector for one invariant is how the two
# drift apart, so this file keeps only the error-then-exit-0 shapes.


def test_every_exemption_is_still_needed() -> None:
    """A stale exemption is worse than none: it reads as 'reviewed and fine' while
    silently covering whatever that function grows into. Each entry must still be
    REACHED — i.e. removing it would produce a finding."""
    stale: list[str] = []
    files = _scanned_files()
    for suffix, functions in RENDERING_ONLY.items():
        matches = [f for f in files if f.as_posix().endswith(suffix)]
        assert len(matches) == 1, (
            f"RENDERING_ONLY key {suffix!r} matches {len(matches)} scanned files - a key "
            "must identify exactly one file, or it exempts code nobody reviewed."
        )
        findings = _offenders(matches[0])
        for fn in functions:
            if not any(f" in {fn}()" in f for f in findings):
                stale.append(f"{suffix}::{fn}")
    assert not stale, (
        "These exemptions no longer suppress anything — the code changed under them. "
        f"Delete them so the guard covers those functions again: {stale}"
    )


def test_the_guard_actually_detects_the_shape() -> None:
    """Anti-vacuity. A guard whose detector has quietly stopped matching reports a
    clean sweep for code it never understood — the #729 failure mode."""
    src = (
        "def cmd():\n"
        "    if bad:\n"
        "        ch.error('boom')\n"
        "        return\n"
        "    try:\n"
        "        go()\n"
        "    except Exception:\n"
        "        ch.failure('nested')\n"
        "        return None\n"
        "    ch.error('fine, this one raises')\n"
        "    raise typer.Exit(1)\n"
    )
    hits: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.FunctionDef):
            _walk_bodies(node.body, hits)

    assert [s for _, s in hits] == ["ch.error", "ch.failure"], (
        f"detector missed a known-bad shape (or invented one): {hits}"
    )


def _fn(src: str) -> ast.FunctionDef:
    return next(
        n for n in ast.walk(ast.parse(src)) if isinstance(n, ast.FunctionDef)
    )


def _named(tree: ast.AST, name: str) -> ast.FunctionDef:
    return next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == name
    )


def test_value_returning_helpers_are_not_commands() -> None:
    """A helper that returns None to signal failure is CORRECT — flagging it would
    repeat #729, where a guard rejected the very pattern the codebase wants."""
    helper = _fn(
        "def _resolve(x):\n"
        "    if bad:\n"
        "        ch.error('no such file')\n"
        "        return None\n"
        "    return x\n"
    )
    assert _returns_a_value(helper) is True

    command = _fn(
        "def cmd(options):\n"
        "    if bad:\n"
        "        ch.error('boom')\n"
        "        return\n"
        "    do_work()\n"
    )
    assert _returns_a_value(command) is False, (
        "a handler that only ever bare-returns must stay in scope — this is the "
        "exact shape the guard exists to catch"
    )


_SWALLOW_SRC = (
    "def _resolve(opts):\n"
    "    if bad:\n"
    "        ch.error('File not found')\n"
    "        return None\n"
    "    return opts['f']\n"
    "\n"
    "def cmd(opts):\n"
    "    f = _resolve(opts)\n"
    "    if f is None:\n"
    "        return\n"
    "    go(f)\n"
)


def test_a_caller_that_swallows_its_helpers_failure_is_caught() -> None:
    """The shape that hid `navig run --file missing.sh` exiting 0."""
    tree = ast.parse(_SWALLOW_SRC)
    helpers = _failure_helpers(tree)
    assert helpers == {"_resolve"}, f"failure helper not recognised: {helpers}"

    hits: list[tuple[int, str]] = []
    _swallows(_named(tree, "cmd"), helpers, hits)
    assert len(hits) == 1, f"the swallow was not caught: {hits}"


def test_a_caller_that_exits_is_not_flagged() -> None:
    """Anti-vacuity for the check above: fixing the bug must clear the finding."""
    fixed = _SWALLOW_SRC.replace("    if f is None:\n        return\n",
                                 "    if f is None:\n        raise typer.Exit(1)\n")
    tree = ast.parse(fixed)
    hits: list[tuple[int, str]] = []
    _swallows(_named(tree, "cmd"), _failure_helpers(tree), hits)
    assert hits == [], f"a caller that raises must not be flagged: {hits}"


def test_a_silently_absent_helper_is_not_a_failure_helper() -> None:
    """A helper returning None for a legitimate "not present" answer — with no ✗
    shown to the user — is NOT a failure signal, and a caller bailing on it is
    fine. Matching it would flag ordinary lookup code."""
    tree = ast.parse(
        "def _find(name):\n"
        "    for x in items:\n"
        "        if x.name == name:\n"
        "            return x\n"
        "    return None\n"
        "\n"
        "def cmd(opts):\n"
        "    x = _find(opts['n'])\n"
        "    if x is None:\n"
        "        return\n"
    )
    assert _failure_helpers(tree) == set()


def test_a_nested_helper_does_not_excuse_its_command() -> None:
    """`ast.walk` would see the inner `return x` and wave the whole command through.
    The outer function's contract is its own."""
    outer = _fn(
        "def cmd(options):\n"
        "    def _inner():\n"
        "        return 'a value'\n"
        "    if bad:\n"
        "        ch.error('boom')\n"
        "        return\n"
    )
    assert _returns_a_value(outer) is False


# ── The two shapes the adjacency rule could not see ───────────────────


def _offenders_in(src: str) -> list[str]:
    """`_offenders` over a source string — same walk AND the same `_returns_a_value`
    skip, so a boundary test cannot pass by exercising a laxer detector than the one
    that actually runs."""
    tree = ast.parse(src)
    out: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if _returns_a_value(node):
            continue
        hits: list[tuple[int, str]] = []
        _walk_bodies(node.body, hits)
        out += [f"{lineno}:{sink}" for lineno, sink in hits]
    return out


def test_a_quiet_guarded_error_is_caught() -> None:
    """`if not quiet: ch.error(...)` then `return` - the two are not siblings, so the
    ordinary pairing cannot see them, yet the command still exits 0."""
    src = (
        "def cmd(name, quiet=False):\n"
        "    if not name:\n"
        "        if not quiet:\n"
        "            ch.error('name is required')\n"
        "        return\n"
    )
    assert len(_offenders_in(src)) == 1


def test_a_quiet_guarded_error_in_a_value_returning_helper_is_not_flagged() -> None:
    """`inspect_host` is `dict | None`: its `return None` IS the failure signal handed to
    its caller, exactly like the `-> str | None` helpers already skipped. Without this,
    extending the detector would have flagged 3 correct helpers beside the 3 real bugs."""
    src = (
        "def helper(name, silent=False):\n"
        "    if not name:\n"
        "        if not silent:\n"
        "            ch.error('no active host')\n"
        "        return None\n"
        "    return {'ok': True}\n"
    )
    assert _offenders_in(src) == []


def test_a_guard_that_does_real_work_is_not_a_suppression_wrapper() -> None:
    """Narrowness is load-bearing: the inner block must be error calls ONLY. An `if` that
    also does something else is ordinary control flow, and treating it as a display guard
    would make the detector editorialise about code it has not understood."""
    src = (
        "def cmd(name, quiet=False):\n"
        "    if not name:\n"
        "        if not quiet:\n"
        "            ch.error('bad')\n"
        "            cleanup()\n"
        "        return\n"
    )
    assert _offenders_in(src) == []


def test_a_hint_between_the_error_and_the_return_does_not_hide_it() -> None:
    """The most natural shape there is: an error, a hint, then `return`.

    Requiring the return to sit IMMEDIATELY after the error meant one `ch.info`
    line was enough to go invisible. Five live sites evaded the guard this way.
    """
    src = (
        "def cmd():\n"
        "    if bad:\n"
        "        ch.error('Could not detect package manager.')\n"
        "        ch.info('Supported: apt-get, yum, dnf')\n"
        "        ch.dim('or install it by hand')\n"
        "        return\n"
    )
    hits: list[tuple[int, str]] = []
    _walk_bodies(_fn(src).body, hits)
    assert [s for _, s in hits] == ["ch.error"], hits


def test_real_work_between_the_error_and_the_return_is_not_skipped() -> None:
    """The gap is keyed to the SAME receiver, deliberately.

    `ch.error(...)` then `store.save(...)` then `return` is a different animal —
    the save is work, and skipping over arbitrary calls to reach a return would
    let the guard editorialise about code it has not understood.
    """
    src = (
        "def cmd():\n"
        "    if bad:\n"
        "        ch.error('boom')\n"
        "        store.save(x)\n"
        "        return\n"
    )
    hits: list[tuple[int, str]] = []
    _walk_bodies(_fn(src).body, hits)
    assert hits == [], f"skipped over real work to reach the return: {hits}"


def test_an_error_that_ends_the_function_is_caught() -> None:
    """Falling off the end IS `return None`, which is exit 0."""
    src = (
        "def cmd():\n"
        "    try:\n"
        "        go()\n"
        "    except ValueError:\n"
        "        ch.error('Failed to parse output')\n"
    )
    hits: list[tuple[int, str]] = []
    _tail_errors(_fn(src).body, hits)
    assert [s for _, s in hits] == ["ch.error"], hits


def test_a_function_that_raises_later_is_exempt_from_the_tail_check() -> None:
    """The documented limitation, pinned so it stays deliberate.

    "error here, raise later" is legitimate and common (backup_system_config
    prints the failure, then raises after writing its metadata). Without this
    exemption the same scan reports 18 sites, 13 of them fine.
    """
    src = (
        "def cmd():\n"
        "    if bad:\n"
        "        ch.error('every file failed')\n"
        "    write_metadata()\n"
        "    if bad:\n"
        "        raise typer.Exit(1)\n"
    )
    fn = _fn(src)
    assert _raises_anywhere(fn) is True
    # ...and a function with no raise at all is not exempt.
    assert _raises_anywhere(_fn("def cmd():\n    ch.error('x')\n")) is False


# ── nested defs have their own contract, in BOTH directions ────────────


def test_a_nested_value_returning_helper_does_not_flag_its_command() -> None:
    """The exact shape `_returns_a_value` exists to exempt, nested one level down.

    `_resolve_command`-style helpers announce a failure and hand back None so the
    caller can decide — correct, and exempt. But the walkers used to descend into
    them, so the helper was skipped as itself and then scanned AGAIN as part of the
    enclosing command, which returns nothing. The command got flagged for its
    helper's legitimate behaviour: a guard rejecting the very pattern it documents.
    """
    src = (
        "def cmd(options):\n"
        "    def _resolve():\n"
        "        if bad:\n"
        "            ch.error('cannot resolve')\n"
        "            return None\n"
        "        return 'ok'\n"
        "    print(_resolve())\n"
    )
    outer = _named(ast.parse(src), "cmd")
    assert _returns_a_value(outer) is False, "the command itself returns nothing"

    hits: list[tuple[int, str]] = []
    _walk_bodies(outer.body, hits)
    assert hits == [], f"flagged the command for its nested helper's contract: {hits}"

    tails: list[tuple[int, str]] = []
    _tail_errors(outer.body, tails)
    assert tails == [], f"same, via the tail check: {tails}"


def test_a_nested_offender_is_reported_once_not_twice() -> None:
    """`_offenders` visits every nested def in its own right via `ast.walk`.

    So a walker that ALSO descended reported the same line twice — once under the
    inner name, once under the outer. That is why the remaining-exposure counts in
    the sweep notes were upper bounds: voice.py reported 8 sites for 4 real ones.
    """
    src = (
        "def cmd(options):\n"
        "    def _run():\n"
        "        if bad:\n"
        "            ch.error('boom')\n"
        "            return\n"
        "    _run()\n"
    )
    tree = ast.parse(src)

    reported: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if _returns_a_value(node):
            continue
        hits: list[tuple[int, str]] = []
        _walk_bodies(node.body, hits)
        reported += [f"{node.name}:{line}" for line, _ in hits]

    assert reported == ["_run:4"], f"expected exactly one report, got {reported}"


def test_the_nested_def_still_gets_scanned_on_its_own() -> None:
    """Not descending must not mean not checking — coverage is unchanged."""
    src = (
        "def cmd(options):\n"
        "    def _run():\n"
        "        ch.error('boom')\n"
        "        return\n"
        "    _run()\n"
    )
    inner = _named(ast.parse(src), "_run")
    hits: list[tuple[int, str]] = []
    _walk_bodies(inner.body, hits)
    assert [s for _, s in hits] == ["ch.error"], hits
