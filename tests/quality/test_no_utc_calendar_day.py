"""A calendar DAY must not be derived from a UTC clock while its data is written locally.

Two live defects came from this in one file (#1288, #1303). The deck's health, habits
and life routes computed ``today`` as ``datetime.now(timezone.utc).strftime("%Y-%m-%d")``
and prefix-matched it against rows whose date the CLI stamps with ``date.today()`` -- the
LOCAL date -- and against cron's ``last_run``, a naive local ``datetime.now()``. So on any
machine not on UTC there is a window every day where the reader looks up a day the writer
never wrote:

  * ``navig body`` records sleep, and the Health tab reports nothing for today;
  * a habit ticked in the deck reads back as NOT done, immediately.

On the operator's UTC+2 box that window is 00:00-02:00, daily. Both defects were invisible
outside it, which is exactly why they survived: the tests that caught them read as flake,
passing in isolation and failing only in a run that happened to cross local midnight.

WHAT THIS FORBIDS, precisely: turning a UTC clock into a calendar DAY -- ``.strftime`` with
a date-only format, or ``.date()`` -- because a day is the unit a human compares. It does
NOT touch UTC INSTANTS (``.isoformat()``, ``created_at``, ``at=``), which are correct and
outnumber the day-shaped uses ~30:1 (89 vs 3 when this landed). An instant is a point on a
timeline every machine agrees on; a day is a question about somebody's calendar.

An entry in ALLOWED is a claim that the value is never compared against locally-stamped
data -- a label, a folder name -- and it must say which.

Scope is a DAY (``%d`` present, or ``.date()``). A month/year bucket is deliberately out:
media_engine/budget.py keys spend by ``"%Y-%m"`` in UTC, which is the same shape but is
both self-consistent (one function writes and reads it) and an order of magnitude less
sharp -- it can only disagree with a local calendar on a month boundary. Widening this
rule to months would flag it and teach the next reader that the guard is noisy.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
CORE = REPO / "core" / "navig"
PLUGINS = REPO / "plugins"

#: file -> why a UTC-derived day is safe THERE. Never a compared value.
ALLOWED = {
    "navig/gateway/channels/telegram_commands.py":
        "a markdown section heading ('Intake <date>') — rendered, never compared",
    "navig/inbox/retention.py":
        "an archive folder name — written and listed as itself, never matched to a local day",
    "navig/telegram/contacts.py":
        "a value rendered into a note template — a label, never compared",
    "navig/selfheal/git_manager.py":
        "a git BRANCH NAME (navig-selfheal/<date>-<hash>) — an identifier, never matched to a day",
}

_UTC_NOW = ("utcnow", "now")


def _is_utc_clock(node: ast.AST) -> bool:
    """True for ``datetime.utcnow()`` / ``datetime.now(timezone.utc)`` / ``now(UTC)``."""
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return False
    if node.func.attr not in _UTC_NOW:
        return False
    if node.func.attr == "utcnow":
        return True
    for arg in [*node.args, *(k.value for k in node.keywords)]:
        src = ast.unparse(arg)
        if "utc" in src.lower():
            return True
    return False


def _day_shaped(call: ast.Call) -> bool:
    """True when this call turns its receiver into a calendar DAY, not an instant."""
    if not isinstance(call.func, ast.Attribute):
        return False
    if call.func.attr == "date":
        return True
    if call.func.attr == "strftime" and call.args:
        fmt = call.args[0]
        return (
            isinstance(fmt, ast.Constant)
            and isinstance(fmt.value, str)
            and "%H" not in fmt.value          # a day, not a timestamp
            and "%d" in fmt.value               # a DAY, not a month/year bucket
        )
    return False


def _scan(path: Path) -> list[int]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and _day_shaped(node)
        and _is_utc_clock(node.func.value)  # type: ignore[union-attr]
    ]


def _sources() -> list[Path]:
    files = [p for p in CORE.rglob("*.py") if "__pycache__" not in p.parts]
    files += [
        p for p in PLUGINS.rglob("*.py")
        if "__pycache__" not in p.parts and "/tests/" not in p.as_posix()
    ]
    return files


def test_the_scan_reaches_both_trees():
    """A miscomputed root makes every count below it meaningless while looking fine."""
    assert CORE.is_dir() and PLUGINS.is_dir(), f"roots moved: {CORE} / {PLUGINS}"
    files = _sources()
    assert len(files) > 500, f"only {len(files)} files — the walk is not reaching core"
    assert sum(1 for p in files if "plugins" in p.parts) > 50, "plugins are not being scanned"


def test_no_new_utc_derived_calendar_day():
    offenders: list[str] = []
    for path in _sources():
        rel = path.relative_to(REPO / "core").as_posix() if (REPO / "core") in path.parents \
            else path.relative_to(REPO).as_posix()
        for line in _scan(path):
            if rel not in ALLOWED:
                offenders.append(f"{rel}:{line}")
    assert offenders == [], (
        "a calendar DAY derived from a UTC clock: "
        + ", ".join(offenders)
        + " — readers compare days against data the CLI stamps with date.today() (LOCAL), "
          "so a UTC day is a different day for hours each night. Use date.today() when the "
          "value answers 'which day?'; keep UTC for instants. If it is only a label, add it "
          "to ALLOWED with the reason."
    )


@pytest.mark.parametrize("src,flagged", [
    ('datetime.now(timezone.utc).strftime("%Y-%m-%d")', True),
    ("datetime.utcnow().date()", True),
    ('datetime.now(timezone.utc).isoformat()', False),        # an instant — correct
    ('datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")', False),  # a timestamp
    ('date.today().isoformat()', False),                      # the local day — the fix
])
def test_the_detector_separates_days_from_instants(tmp_path, src, flagged):
    f = tmp_path / "probe.py"
    f.write_text(f"x = {src}\n", encoding="utf-8")
    assert bool(_scan(f)) is flagged, f"{src} should {'' if flagged else 'NOT '}be flagged"
