"""The marks `ci-local.mjs` filters on must be real, and must mean what it assumes.

`-m` expressions are NOT covered by `--strict-markers`. That option validates marks
*applied in test files*; an unknown name inside a `-m` expression is simply false.
Verified: `-m "not integraton"` (typo) collects all 3 tests in a file where
`-m "not integration"` correctly collects 0. So a renamed or mistyped mark silently
changes what the gate runs, with no error anywhere — the same silent-failure shape
this repo keeps finding in product code.

WHY EACH EXCLUSION EXISTS — these are not interchangeable, and pytest.ini is the
authority on what each one means:

  * `live` / `requires_server` — genuinely need infrastructure that a dev machine
    may not have. Excluded from every local profile.
  * `slow` — runnable anywhere; excluded from the default profile purely for time.
  * `integration` — pytest.ini defines it as "Integration tests requiring MOCKED
    dependencies". Mocked means it needs nothing external, so excluding it from a
    local run buys no reliability. It was excluded anyway, which hid 5,559 tests
    (21% of the suite) from `npm run ci` and from the baseline this repo quotes.
"""

from __future__ import annotations

import re
from pathlib import Path

CORE = Path(__file__).resolve().parents[2]
PYTEST_INI = CORE / "pytest.ini"
CI_RUNNER = CORE.parent / "scripts" / "ci-local.mjs"

# Marks that mean "this machine may not be able to run it". Only these justify an
# exclusion on reliability grounds; anything else is a time or policy decision and
# should say so.
INFRASTRUCTURE_MARKS = frozenset({"live", "requires_server"})


def _registered_marks() -> set[str]:
    """Names declared under `markers =` in pytest.ini."""
    body = PYTEST_INI.read_text(encoding="utf-8")
    block = re.search(r"^markers\s*=\s*\n((?:[ \t]+\S.*\n)+)", body, re.M)
    assert block, "could not find the `markers =` block in pytest.ini"
    return {
        line.strip().split(":", 1)[0].strip()
        for line in block.group(1).splitlines()
        if line.strip()
    }


def _mark_expressions() -> list[str]:
    """The pytest `-m` MARK expressions in ci-local.mjs.

    Parsed from the `pytestArgs.push("-m", …)` call specifically, not from every
    `-m` in the file: `py("-m", "ruff", …)` and `["-m", "pytest", …]` are Python's
    MODULE flag, and treating those as marks made this guard's first run report
    `ruff` as an unregistered mark — a false positive in the guard, not a real
    finding.
    """
    src = CI_RUNNER.read_text(encoding="utf-8")
    push = re.search(r"pytestArgs\.push\(\s*\"-m\"\s*,(.*?)\);", src, re.S)
    assert push, (
        "could not find `pytestArgs.push(\"-m\", …)` in ci-local.mjs — the runner was "
        "restructured and this guard is now checking nothing."
    )
    return re.findall(r'"([^"]+)"', push.group(1))


def _marks_used_by_ci() -> set[str]:
    names: set[str] = set()
    for expr in _mark_expressions():
        names |= {
            t for t in re.findall(r"\b[a-z_][a-z0-9_]*\b", expr)
            if t not in {"not", "and", "or"}
        }
    return names


def test_every_mark_the_ci_filter_names_is_registered() -> None:
    """A mark that does not exist evaluates to false, so `not <typo>` matches
    EVERYTHING and the filter quietly stops filtering."""
    used, registered = _marks_used_by_ci(), _registered_marks()
    assert used, (
        "no marks parsed out of ci-local.mjs — the `-m` arguments were restructured "
        "and this guard is now checking nothing."
    )
    unknown = sorted(used - registered)
    assert not unknown, (
        f"ci-local.mjs filters on marks that pytest.ini does not register: {unknown}. "
        "`--strict-markers` does NOT catch this — an unknown name in a -m expression "
        "is just false, so the filter silently changes what the gate runs. Register "
        "them in pytest.ini or fix the spelling."
    )


def test_the_default_profile_only_drops_what_it_can_justify() -> None:
    """Guards the decision itself, not just the spelling.

    Re-adding `integration` here would re-hide 21% of the suite from the default
    gate on a premise pytest.ini contradicts. If a future change genuinely needs to
    drop another mark, add it with a reason to one of the two sets below.
    """
    exprs = _mark_expressions()
    assert len(exprs) == 2, (
        f"expected exactly two -m expressions (full + default), got {exprs}"
    )
    # The default profile is the more restrictive of the two.
    default = max(exprs, key=lambda e: len(re.findall(r"not\s+\w+", e)))
    excluded = set(re.findall(r"not\s+(\w+)", default))
    unjustified = sorted(excluded - INFRASTRUCTURE_MARKS - {"slow"})
    assert not unjustified, (
        f"the default profile excludes {unjustified}, which is neither infrastructure "
        f"({sorted(INFRASTRUCTURE_MARKS)}) nor the documented time exclusion (slow). "
        "pytest.ini calls `integration` mock-based, so dropping it hides runnable "
        "tests from every local run — that is how 5,559 of them went unmeasured."
    )
