"""Guard: a test must not convert its own failures into skips.

`AssertionError` IS an `Exception`. So a test shaped like::

    try:
        resp = requests.post(...)
        assert resp.status_code == 200, "Failed to create job"
    except Exception as e:
        pytest.skip(f"Gateway not accessible: {e}")

reports a genuine failure as a SKIP. The intent is obvious and reasonable -- skip when the
service is not there -- but the handler cannot tell "the gateway is absent" from "the
gateway answered and the assertion about its answer failed", so it silently turns the
second into the first.

Measured 2026-09-03 across the whole suite: **7 try-blocks, 9 assertions at risk**, all in
tests/agent/test_autonomous_agent.py -- including a deliberate
``assert False, f"Failed to create job: {resp.status_code}"`` that could only ever be
reported as a skip. Every other test file was already clean, which is why this is a floor
worth holding rather than a debt list to grow.

Proven, not assumed::

    without the guard:  assert False  ->  reported as SKIP
    with the guard:     assert False  ->  AssertionError propagates -> FAILURE

The fix is one clause, before the broad handler::

    except AssertionError:
        raise
    except Exception as e:
        pytest.skip(...)

Narrow handlers (`except ConnectionError`, `except ImportError`, `except gaierror`) are
untouched by this guard and remain the better shape: they say what they are tolerating.
Only `except Exception` / `except BaseException` / bare `except:` can swallow an assertion,
so only those are checked.

Repo-wide, not scoped to ``core/tests``: the surface is "every pytest suite" -- plugins,
private/harbor, registry and apps/os/resources included. It shipped scanning core/tests
alone, which is this repo's most-repeated guard defect: a guard pointed at a path stops
protecting the moment the code it guards lives somewhere else. Measured when widening it:
167 test files outside core/, **0 offenders** among them, so this holds a floor under the
other 24 suites rather than tracking debt.

Pure AST: zero dependencies, launches nothing, and it cannot go vacuous -- it checks that
the repo root really is the repo root, then that it scanned a plausible number of files
including files under plugins/.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

# Directories that hold no live suite: dot-dirs (.lab reference corpus, .dev worktrees,
# .backup, .git) plus vendored trees. Pruned during the walk, not filtered after, so the
# scan never descends into node_modules or a vendored site-packages.
VENDOR_DIRS = {"node_modules", "venv", "site-packages", "__pycache__", "target", "dist", "build"}

# Vacuity floors. Measured 2026-09-03: 1,709 test files, 150 of them under plugins/. Far
# below the real counts so ordinary growth or pruning never trips them, but high enough
# that a walk which silently collapsed to one directory fails instead of reporting zero.
#
# The plugin floor counts files under a directory that must exist, rather than files that
# merely do NOT start with "core/". A floor phrased as an absence cannot tell "nothing was
# excluded" from "everything was excluded" -- the negative form was tried in this guard's
# sibling and passed a teeth test it should have failed.
MIN_SCANNED = 800
MIN_PLUGIN_FILES = 100


def _calls_pytest_skip(node: ast.AST) -> bool:
    for sub in ast.walk(node):
        if (
            isinstance(sub, ast.Call)
            and isinstance(sub.func, ast.Attribute)
            and sub.func.attr == "skip"
            and getattr(sub.func.value, "id", "") == "pytest"
        ):
            return True
    return False


def _swallows_assertion_error(handler: ast.ExceptHandler) -> bool:
    """True when this handler would catch an AssertionError."""
    if handler.type is None:  # bare `except:`
        return True
    names = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    return any(getattr(n, "id", "") in {"Exception", "BaseException"} for n in names)


def _reraises_assertion_first(try_node: ast.Try) -> bool:
    """True when an earlier handler catches AssertionError, making a later broad one safe.

    Order matters: Python takes the FIRST matching handler, so `except AssertionError:
    raise` placed above `except Exception:` lets real failures through while connection
    errors still skip.
    """
    for handler in try_node.handlers:
        if getattr(handler.type, "id", "") == "AssertionError":
            return True
        if _swallows_assertion_error(handler):
            return False  # the broad one comes first — it wins
    return False


def _test_files() -> list[Path]:
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(REPO):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in VENDOR_DIRS]
        found += [
            Path(dirpath, f) for f in filenames if f.startswith("test_") and f.endswith(".py")
        ]
    return found


def _offenders() -> tuple[list[str], int, int]:
    offenders: list[str] = []
    scanned = 0
    plugin_files = 0
    for path in sorted(_test_files()):
        rel_path = path.relative_to(REPO).as_posix()
        if rel_path.startswith("plugins/"):
            plugin_files += 1
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:  # pragma: no cover - a broken test file is another guard's job
            continue
        scanned += 1
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            if not any(isinstance(n, ast.Assert) for n in ast.walk(node)):
                continue
            if _reraises_assertion_first(node):
                continue
            for handler in node.handlers:
                if _calls_pytest_skip(handler) and _swallows_assertion_error(handler):
                    offenders.append(f"{rel_path}:{node.lineno}")
    return offenders, scanned, plugin_files


def test_no_test_swallows_its_own_assertions_into_a_skip():
    # Anchor REPO structurally before counting anything. A miscomputed root is the one
    # failure that makes every count below meaningless while still looking plausible.
    assert (REPO / "core" / "navig").is_dir() and (REPO / "plugins").is_dir(), (
        f"{REPO} is not the repo root — REPO is derived by parent count, so a file moved "
        "between directories silently narrows the whole scan."
    )

    offenders, scanned, plugin_files = _offenders()

    assert scanned >= MIN_SCANNED, (
        f"scanned only {scanned} test file(s), expected >= {MIN_SCANNED}. Something "
        "narrowed the walk — a moved tests/ dir or a bad glob — and a guard that reads "
        "nothing passes while checking nothing."
    )
    assert plugin_files >= MIN_PLUGIN_FILES, (
        f"only {plugin_files} test file(s) under plugins/ reached, expected >= "
        f"{MIN_PLUGIN_FILES}. The walk degraded to core/ alone, so plugins, private/harbor, "
        "registry and apps/os/resources are unguarded."
    )

    assert not offenders, (
        "these try-blocks contain assertions AND a handler that skips on Exception, so a "
        "real failure is reported as a skip:\n  "
        + "\n  ".join(offenders)
        + "\n\nPut `except AssertionError:\\n    raise` ABOVE the broad handler. Order "
        "matters: Python takes the first matching handler."
    )
