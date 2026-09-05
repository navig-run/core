r"""A bracketed value in a `ch.*` message is parsed as Rich markup and DROPPED.

`ch.info(f"[{job_id}] {name}")` looks like it prints `[job_8] nightly`. It prints
` nightly` — Rich reads `[job_8]` as a style tag, finds no such style, and emits
nothing for it. Measured directly:

    >>> ch.info("  OLD: [job_8] nightly backup")
    ℹ    OLD:  nightly backup

That is invisible in code review and invisible in the output, and the values being
eaten were the ones users need to type back:

  * `navig cron list`  hid every job id — the argument `cron remove|enable|disable|run` takes
  * `navig cdp tabs`   hid the tab index — the `--ref` you click with
  * `navig work list`  hid the work item id
  * onboarding menus   hid the choice numbers you pick a provider with (4 sites)

16 sites across 10 files when this guard was written. The fix is Rich's escape,
spelled `\\[` in source — a bare `\[` is a SyntaxWarning.

Not covered here: a *value* that itself contains markup-like text is eaten the same
way (`f"\\[{title}]"` where title is `a[bold]b` renders `a b`). Escaping every
interpolation of user data in every `ch.*` call is a much larger sweep with real
double-escaping risk, so it is deliberately out of scope — this guard pins the
delimiter, which is unambiguously meant to be literal.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

PKG = Path(__file__).resolve().parents[2] / "navig"

# Sinks that render through Rich markup.
_SINKS = frozenset({"info", "success", "warning", "error", "dim", "print"})

# `[` immediately followed by an f-string interpolation. A style tag is a literal
# name (`[dim]`, `[/green]`), never a substituted value — so this shape is always
# a mistake, which is what makes the guard precise enough to sit at zero.
#
# The lookbehind is load-bearing and was missing at first: the FIX writes `\[` into
# the source, which still contains the two characters `[{`, so without it the guard
# flagged every site it had just corrected. A guard that rejects its own remedy is
# worse than no guard — it teaches you to delete it.
_BRACKETED_VALUE = re.compile(r"(?<!\\)\[\{")


def _is_console_sink(call: ast.Call) -> bool:
    fn = call.func
    return (
        isinstance(fn, ast.Attribute)
        and fn.attr in _SINKS
        and isinstance(fn.value, ast.Name)
        and fn.value.id in {"ch", "_ch"}
    )


def _offenders(path: Path) -> list[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    except SyntaxError:  # pragma: no cover - a broken file is another guard's job
        return []

    out: list[str] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_console_sink(node)):
            continue
        for arg in node.args:
            if not isinstance(arg, ast.JoinedStr):
                continue
            # Rebuild only the LITERAL parts, marking where interpolations sit, so
            # `[` + `{expr}` is detectable without re-rendering the expression.
            sketch = "".join(
                part.value if isinstance(part, ast.Constant) and isinstance(part.value, str)
                else "{"
                for part in arg.values
            )
            # `f"[{colour}]{state}[/{colour}]"` interpolates a STYLE NAME, which is
            # legitimate dynamic markup and renders correctly. The closing `[/{`
            # is the unambiguous tell, so skip the whole call when it appears —
            # `repo.py` does this deliberately in two places. Rejecting correct
            # code is how a guard gets deleted instead of obeyed (#729).
            if "[/{" in sketch:
                continue
            if _BRACKETED_VALUE.search(sketch):
                out.append(f"{path.name}:{node.lineno}")
    return out


def test_no_ch_message_wraps_a_value_in_unescaped_brackets() -> None:
    hits: list[str] = []
    for py in sorted(PKG.rglob("*.py")):
        hits += _offenders(py)

    assert not hits, (
        "These print `[{value}]` through a Rich-rendered sink, so Rich parses it as a "
        "style tag and the value NEVER APPEARS. Escape the bracket — `\\\\[{value}]`:\n  "
        + "\n  ".join(hits)
    )


def test_the_guard_detects_the_shape() -> None:
    """Anti-vacuity: a detector that quietly matches nothing reports a clean sweep."""
    import tempfile

    bad = 'ch.info(f"  [{job.get(\'id\')}] {job.get(\'name\')}")\n'
    good = (
        'ch.info(f"  \\\\[{job_id}] {name}")\n'
        'ch.info(f"  [dim]{name}[/dim]")\n'          # real markup, not a value
        'ch.info(f"  {name} [{ok}")\n'               # unbalanced, but still `[{`
        'log.info(f"  [{job_id}] {name}")\n'         # not a Rich sink
        'ch.info(f"  [{colour}]{state}[/{colour}]")\n'  # dynamic STYLE — renders fine
    )
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "probe.py"
        p.write_text(bad, encoding="utf-8")
        assert _offenders(p), "detector missed the known-bad shape"

        p.write_text(good, encoding="utf-8")
        found = _offenders(p)
        # Line 3 IS a real hit (`[{ok}`). The escaped one, the literal-markup one,
        # the non-sink one and the dynamic-style one must all be left alone — that
        # last is the exemption `repo.py` depends on.
        assert len(found) == 1, f"expected exactly the unbalanced-bracket hit, got {found}"
        assert found[0].endswith(":3"), found


def test_rich_really_drops_an_unescaped_bracketed_value() -> None:
    """The premise, asserted rather than assumed — via Rich itself, not a screenshot."""
    from rich.console import Console

    out = Console(file=None, width=80, record=True, markup=True)
    out.print("x: [job_8] y")
    assert "job_8" not in out.export_text(), "Rich no longer eats this; the guard can go"

    out2 = Console(file=None, width=80, record=True, markup=True)
    out2.print("x: \\[job_8] y")
    assert "job_8" in out2.export_text(), "the escape stopped working"
