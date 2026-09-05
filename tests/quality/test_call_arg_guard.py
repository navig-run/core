"""The `call-arg` and `override` rules in `scripts/check_module_attrs.py` must count.

mypy's `call-arg` is "this call cannot succeed" — a missing required argument, or a keyword
the callee does not define — resolved from the real signature. It earns a gate because the
failure is *invisible by construction*: such calls sit inside `try:` blocks whose handler
returns a default, so a guaranteed TypeError degrades into "nothing configured" with no
traceback and no log.

#1024 was exactly that: `config_loader.load_config()` called with no arguments (it takes a
required `path`), so an operator's whole `tools:` safety policy read as empty forever. Three
more of the same call were found when this guard was wired, one behind `agent.fallback_chain`
— a documented setting whose reader could only ever return `[]`.

The `override` rule (added later) covers the class `call-arg` structurally cannot see: a
subclass that is *self-consistently* wrong, whose real contract lives in its base class.

These tests drive the parsing and counting against synthetic mypy output rather than running
mypy (~10 minutes over 22 scopes). The end-to-end behaviour is teeth-tested by hand; what can
rot silently is the counting rule, which is what this pins.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
GUARD = REPO / "scripts" / "check_module_attrs.py"


@pytest.fixture(scope="module")
def guard():
    spec = importlib.util.spec_from_file_location("_check_module_attrs", GUARD)
    assert spec and spec.loader, f"cannot load {GUARD}"
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _line(path: str, line: int, msg: str) -> str:
    return f"{path}:{line}: error: {msg}  [call-arg]"


def test_it_parses_a_finding_out_of_real_mypy_output(guard):
    """The shape mypy actually emits, backslashes and all."""
    out = _line(
        r"navig\llm\generate.py", 469,
        'Missing positional argument "path" in call to "load_config"',
    )
    seen = guard._call_arg_findings(out)

    assert seen == {
        ("navig/llm/generate.py",
         'Missing positional argument "path" in call to "load_config"'): ["469"]
    }, "the guard did not parse a real mypy call-arg line — it would report zero forever"


def test_a_finding_outside_the_baseline_fails(guard):
    seen = {("navig/brand_new.py", 'Missing positional argument "x" in call to "f"'): ["12"]}

    assert guard._call_arg_offenders(seen), (
        "a call-arg finding in a file with no baseline entry was accepted"
    )


def test_a_second_instance_in_an_already_known_file_fails(guard):
    """The case a naive 'is this file known?' check would wave through.

    A file already carrying one known finding must not become a place where new ones hide.
    """
    key = next(iter(guard._CALL_ARG_BASELINE))
    allowed = guard._CALL_ARG_BASELINE[key]

    at_baseline = {key: [str(100 + i) for i in range(allowed)]}
    assert not guard._call_arg_offenders(at_baseline)

    one_more = {key: [str(100 + i) for i in range(allowed + 1)]}
    offenders = guard._call_arg_offenders(one_more)
    assert offenders, "an extra instance in an already-baselined file was accepted"
    assert f"{allowed + 1} found, {allowed} known" in offenders[0], (
        "the failure must name the delta, not just say something is wrong"
    )


def test_fixing_one_of_several_does_not_fail(guard):
    """A baseline is a ceiling, not a quota — going down must stay green."""
    key = next(
        (k for k, v in guard._CALL_ARG_BASELINE.items() if v > 1), None
    )
    if key is None:
        pytest.skip("no multi-instance baseline entry to exercise")

    assert not guard._call_arg_offenders({key: ["1"]})


def test_every_baseline_entry_carries_a_written_verdict(guard):
    """A number with no reason is how a baseline becomes a place to hide things.

    Mirrors the rule the module-attr half already states: adding a suppression requires
    writing down why it is safe. Checked on the source, because the verdicts are comments.
    """
    src = GUARD.read_text(encoding="utf-8")
    start = src.index("_CALL_ARG_BASELINE: dict")
    # End at the NEXT rule, not at `_mypy_available` — a later rule inserted between the
    # two would silently widen this slice and let its comments count as verdicts here.
    end = src.index("# ── The third rule")
    block = src[start:end]

    verdicts = block.count("# ── artifact:") + block.count("# ── real")
    # Two artifact GROUPS remain (the load_pem_private_key union, and shutil.rmtree's
    # `onexc` against an older typeshed) and zero `real` — every genuine defect has been
    # fixed. The floor tracks the groups that exist, not a number frozen when there were
    # more; `test_no_real_defects_are_parked_in_the_baseline` is the strict half.
    assert verdicts >= 2, (
        "the call-arg baseline lost its per-group verdicts — every entry must be marked "
        "'artifact' (correct at runtime, mypy is wrong) or 'real' (a genuine defect), "
        "with the reason"
    )


def test_the_baseline_is_not_growing_unbounded(guard):
    """A ceiling that can be raised freely is not a ceiling.

    39 findings existed when this was wired; 12 were fixed with it and 8 more later. This is a
    tripwire, not a hard limit: raising it is a deliberate edit that shows up in review.
    """
    total = sum(guard._CALL_ARG_BASELINE.values())
    assert total <= 8, (
        f"the call-arg baseline is now {total}; 39 -> 27 -> 15 -> 8 as the real defects "
        "were fixed. Every remaining entry is a mypy ARTIFACT. New unsatisfiable "
        "calls should be fixed, not baselined — the baseline exists for mypy artifacts and "
        "for pre-existing debt that needs a product decision."
    )


# ─────────────────────────────────────────────────────────────────────────────
# The `override` rule — a subclass that breaks its base class's contract
# ─────────────────────────────────────────────────────────────────────────────
#
# `call-arg` structurally CANNOT catch this class: `bash_exec` declared
# `on_status: Callable[[str], None]` and called it with one argument, so every line in that
# file agreed with every other line. The contract it had to satisfy —
# `StatusCallback = Callable[[str, str, int], Coroutine]` — lived in the base class, and the
# framework passed a real 3-arg coroutine, so each status event became a coroutine that was
# created, dropped and never awaited.


def test_the_override_rule_parses_real_mypy_output(guard):
    # Verbatim from a real run — mypy emits plain double quotes and Windows backslashes.
    out = (
        'navig\\tools\\bash_exec.py:108: error: Argument 2 of "run" is incompatible with '
        'supertype "navig.tools.registry.BaseTool"  [override]'
    )
    seen = guard._findings(out, guard._OVERRIDE_RE)

    assert len(seen) == 1, f"the override rule did not parse a real mypy line: {out!r}"
    (rel, msg), lines = next(iter(seen.items()))
    assert rel == "navig/tools/bash_exec.py"
    assert lines == ["108"]
    assert "incompatible with supertype" in msg


def test_the_override_baseline_is_empty(guard):
    """Empty is the point. An override that disagrees with its base is a defect by
    definition — the base class IS the contract — so there is no "mypy is wrong about a
    union" category to grandfather, the way there is for call-arg.

    All 8 findings that existed when this rule was wired were fixed in the same change.
    """
    assert guard._OVERRIDE_BASELINE == {}, (
        "the override baseline gained an entry. An override that breaks its base class is "
        "not a suppressible artifact — fix the override, or change the base class and say "
        "so explicitly."
    )


def test_any_override_finding_fails(guard):
    seen = {("navig/tools/anything.py", 'Argument 2 of "run" is incompatible'): ["10"]}

    assert guard._offenders(seen, guard._OVERRIDE_BASELINE), (
        "an override finding was accepted against an empty baseline"
    )


def test_both_rules_share_one_comparison(guard):
    """Two copies of the counting rule would be two chances for one to become a membership
    test. `_call_arg_offenders` must delegate rather than re-implement.
    """
    import inspect

    body = inspect.getsource(guard._call_arg_offenders)
    assert "_offenders(" in body and "for key" not in body, (
        "_call_arg_offenders re-implements the comparison instead of delegating to "
        "_offenders — the two rules can now drift apart"
    )


def test_no_real_defects_are_parked_in_the_baseline(guard):
    """The baseline is for mypy artifacts, not for debt nobody got round to.

    39 findings existed when the gate was wired. 31 of them were genuine defects and are
    fixed (12 in #1032, 8 in #1049, 6 in #1058); the 8 that remain are all cases where
    mypy's view is wrong and the call is correct at runtime — the union of key types from
    `load_pem_private_key`, and `shutil.rmtree(onexc=)` against an older typeshed.

    A `real` entry means "this call raises and we shipped it anyway". Keeping that
    category empty is what stops the baseline becoming a parking space; if one is ever
    added, this test failing is the prompt to write down what blocks fixing it.
    """
    src = GUARD.read_text(encoding="utf-8")
    start = src.index("_CALL_ARG_BASELINE: dict")
    end = src.index("# ── The third rule")
    block = src[start:end]

    assert "# ── real" not in block, (
        "a `real` (genuinely broken) call was added to the call-arg baseline. Fix it, or "
        "if it truly cannot be fixed now, say what blocks it and update this test."
    )
    assert block.count("# ── artifact:") >= 2, (
        "the artifact entries lost their written reasons"
    )
