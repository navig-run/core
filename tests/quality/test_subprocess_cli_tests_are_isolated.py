"""A test that redirects HOME for a CLI subprocess must also pin NAVIG_CONFIG_DIR.

`core/tests/conftest.py` sets `NAVIG_CONFIG_DIR` session-wide, `os.environ.copy()`
inherits it, and `platform.paths.config_dir()` prefers it over HOME. So a helper
that redirects only HOME builds a subprocess that reads a config **the test never
wrote** — and then falls back to defaults, where the operator's LIVE daemon is
listening.

That is not hypothetical. Four of eight such helpers were missing it, and
`test_gateway_session_handles_missing_gateway_without_invalid_url` — a test whose
whole premise is "there is no gateway" — reached the real daemon and started
failing with **HTTP 401** the moment #994 required a bearer token. It fails on
clean `main` for anyone with a daemon running, which on this project is everyone.

⚠ Scope note: this scans the test tree and names no module of its own, so "tests
for changed modules" can never select it. It is registered in `sourceGuardArgs`
in `scripts/ci-local.mjs`; without that it would run only in the full suite.
"""
from __future__ import annotations

import ast
from pathlib import Path

_TESTS_ROOT = Path(__file__).resolve().parents[1]

#: Setting either of these is what declares "this subprocess gets its own home".
_HOME_KEYS = frozenset({"HOME", "USERPROFILE"})

#: Both outrank HOME in `platform.paths`, and conftest exports both — so pinning
#: only the first leaves the subprocess writing state into the session's data dir.
#: "config is isolated" is not "state is isolated"; requiring both costs nothing
#: (measured: zero helpers miss either today) and forecloses the identical
#: half-fix.
_REQUIRED_KEYS = ("NAVIG_CONFIG_DIR", "NAVIG_DATA_DIR")


def _copies_the_environment(fn: ast.AST) -> bool:
    """True when *fn* builds a mutable copy of the process environment.

    This is what separates a SUBPROCESS env builder from an in-process
    `patch.dict("os.environ", {"HOME": …})`, which is a legitimate way to test an
    OS adapter and must not be flagged.
    """
    for node in ast.walk(fn):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "copy"
                and isinstance(node.func.value, ast.Attribute)
                and node.func.value.attr == "environ"):
            return True
    return False


def _subscript_keys_assigned(fn: ast.AST) -> set[str]:
    """String keys assigned by subscript inside *fn* (`env["X"] = …`)."""
    keys: set[str] = set()
    for node in ast.walk(fn):
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if (isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and isinstance(target.slice.value, str)):
                keys.add(target.slice.value)
    return keys


def _any_keys_mentioned(fn: ast.AST) -> set[str]:
    """Every string key this function sets, however it sets it.

    Deliberately WIDER than the detector above: strict about what raises the
    alarm, generous about what silences it, so a helper that pins the config dir
    via `env.update({...})` is not reported as an offender.
    """
    keys = _subscript_keys_assigned(fn)
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            keys.update(kw.arg for kw in node.keywords if kw.arg)
            for arg in node.args:
                if isinstance(arg, ast.Dict):
                    keys.update(k.value for k in arg.keys
                                if isinstance(k, ast.Constant) and isinstance(k.value, str))
    return keys


def _is_offender(fn: ast.AST) -> bool:
    """Does *fn* build a subprocess environment with a half-redirected home?"""
    if not _copies_the_environment(fn):
        # An in-process env mutation — `patch.dict("os.environ", …)` or a direct
        # `os.environ["HOME"] = …` — is not a subprocess and has no config dir to
        # pin. Flagging one would be a warning that can never go green.
        return False
    if not _subscript_keys_assigned(fn) & _HOME_KEYS:
        return False
    return bool(set(_REQUIRED_KEYS) - _any_keys_mentioned(fn))


def _offenders() -> list[str]:
    out: list[str] = []
    for path in sorted(_TESTS_ROOT.rglob("test_*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_offender(node):
                out.append(f"{path.relative_to(_TESTS_ROOT.parent)}:{node.lineno} -> {node.name}()")
    return out


def test_a_redirected_home_also_redirects_the_config_dir() -> None:
    offenders = _offenders()
    assert not offenders, (
        "These build a subprocess environment with a redirected HOME but leave the "
        f"session's {' and '.join(_REQUIRED_KEYS)} in place — which WIN over HOME, so "
        "the subprocess reads a config the test never wrote and can reach the "
        "operator's live daemon. Pin them to that same home:\n  "
        + "\n  ".join(offenders)
    )


def test_the_guard_is_actually_reading_the_test_tree() -> None:
    """A scan that reads nothing passes the assertion above for free."""
    files = list(_TESTS_ROOT.rglob("test_*.py"))
    assert len(files) > 400, f"only found {len(files)} test files — is the root wrong?"


def test_the_guard_sees_the_shape_it_is_looking_for() -> None:
    """Teeth: the detector must fire on the exact pattern that caused the bug.

    Asserted against a synthetic function rather than a real file, so it keeps
    working once every real offender is fixed.
    """
    fn = ast.parse(
        "def _cli_env(tmp_path):\n"
        "    env = os.environ.copy()\n"
        '    env["HOME"] = str(tmp_path)\n'
        '    env["USERPROFILE"] = str(tmp_path)\n'
        "    return env\n"
    ).body[0]
    assert _is_offender(fn), "the detector cannot see the shape that caused the bug"

    fixed = ast.parse(
        "def _cli_env(tmp_path):\n"
        "    env = os.environ.copy()\n"
        '    env["HOME"] = str(tmp_path)\n'
        '    env["NAVIG_CONFIG_DIR"] = str(tmp_path / ".navig")\n'
        '    env["NAVIG_DATA_DIR"] = str(tmp_path / ".navig" / "data")\n'
        "    return env\n"
    ).body[0]
    assert not _is_offender(fixed), "the fixed shape must not be flagged"

    # …and an in-process patch.dict must never be flagged, whatever it sets.
    in_process = ast.parse(
        'def test_home(self):\n'
        '    with patch.dict("os.environ", {"HOME": "/home/navig"}):\n'
        '        pass\n'
    ).body[0]
    assert not _is_offender(in_process), (
        "patch.dict on os.environ is an in-process adapter test, not a subprocess"
    )

    # The env-copy requirement earns its place here rather than in the tree: no
    # test does this TODAY, so only a synthetic case can prove the filter is
    # load-bearing. Without it this is flagged — a subscript assignment of HOME —
    # and the report would name a function with no subprocess to isolate.
    direct = ast.parse(
        'def test_home(tmp_path):\n'
        '    os.environ["HOME"] = str(tmp_path)\n'
        '    assert Path.home() == tmp_path\n'
    ).body[0]
    assert _subscript_keys_assigned(direct) & _HOME_KEYS, "premise: it assigns HOME"
    assert not _is_offender(direct), (
        "an in-process os.environ mutation is not a subprocess environment"
    )
