"""navig evolve sweep — the unattended check-and-fix loop.

The end-to-end cases build a real throwaway git repository and run real ruff and real pytest;
only the model is faked. What they pin is the contract that makes an unattended fixer safe to
leave running: the operator's checkout is never touched, a fix exists only if its check passed,
a cheating answer is refused, a failing test escalates to the fallback model, and a lint fix
that breaks a passing test is dropped from the branch.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from navig.core.evolution.base import extract_code_block
from navig.core.evolution.sweep import (
    SweepOptions,
    _frames,
    _Source,
    cheat_reason,
    normalise_reply,
    pick_window,
    ruff_command,
    run_sweep,
)

needs_ruff = pytest.mark.skipif(ruff_command() is None, reason="ruff is not installed")
# These run a real pytest several times over (baseline, each attempt, the gate): 7-17 s on an
# idle machine, past the gate's 60 s default when a model or another session holds the CPU.
runs_pytest = pytest.mark.timeout(240)
# These drive git (a worktree, commits, a branch) and ruff once per attempt: 7-10 s idle,
# past the gate's 30 s per-test ceiling under load, where pytest-timeout kills the xdist
# worker outright and xdist's rescheduling then aborts the whole run.
runs_git = pytest.mark.timeout(240)


# ─────────────────────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────────────────────


def test_extract_code_block_keeps_indentation_when_asked() -> None:
    reply = "Here you go:\n```python\n    x = 1\n    return x\n```\n"
    assert extract_code_block(reply) == "x = 1\n    return x"  # whole-file mode: unchanged
    assert extract_code_block(reply, keep_indent=True) == "    x = 1\n    return x"


def test_normalise_reply_strips_the_wrapper_and_restores_lost_indentation() -> None:
    original = ["    data = f.read()", "    return dat"]
    reply = "```python\ndata = f.read()\nreturn data\n```"
    assert normalise_reply(reply, original) == ["    data = f.read()", "    return data"]


@pytest.mark.parametrize(
    ("new", "test_fix", "expected"),
    [
        (["    return dat  # noqa: F821"], False, "silenced the check"),
        # ruff honours any case of the marker, so the refusal must too
        (["    return dat  # NOQA: F821"], False, "silenced the check"),
        (["    return dat  # NoQa"], False, "silenced the check"),
        (["    return dat  # Type: Ignore"], False, "silenced the check"),
        (["    return dat"], False, "changed nothing"),
        ([""], False, "empty"),
        (["    pytest.skip('later')", "    return dat"], True, "silenced the check"),
    ],
)
def test_cheat_reason_refuses_answers_that_do_not_fix(
    new: list[str], test_fix: bool, expected: str
) -> None:
    reason = cheat_reason(["    return dat"], new, test_fix=test_fix)
    assert reason is not None and expected in reason


def test_cheat_reason_refuses_a_lint_fix_that_deletes_code() -> None:
    old = [f"    x{i} = {i}" for i in range(5)]
    reason = cheat_reason(old, old[:1], test_fix=False)
    assert reason is not None and "deleted" in reason
    assert (
        cheat_reason(old, [*old[:4], "    x4 = 4  "], test_fix=False) is not None
    )  # whitespace only
    assert cheat_reason(old, [*old[:4], "    x4 = 5"], test_fix=False) is None


def test_pick_window_prefers_the_enclosing_function() -> None:
    src = _Source(["import os", "", "def f():", "    a = 1", "    return b", "", "x = 2"], "\n")
    assert pick_window(src, 5, max_fn=60, pad=1) == (2, 5)
    assert pick_window(src, 7, max_fn=60, pad=1) == (5, 7)  # module level: line ± pad


def test_frames_read_relative_and_windows_absolute_paths(tmp_path: Path) -> None:
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("x = 1\n", encoding="utf-8")
    detail = f"pkg/mod.py:3: in f\n{tmp_path / 'pkg' / 'mod.py'}:7: AssertionError\nC:/py/lib/x.py:1: in g\n"
    assert _frames(detail, tmp_path) == [("pkg/mod.py", 3), ("pkg/mod.py", 7)]


# ─────────────────────────────────────────────────────────────────────────────
# End to end: real git, real ruff, real pytest, fake model
# ─────────────────────────────────────────────────────────────────────────────


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=root, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout.strip()


def _repo(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "proj"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "sweep@test.invalid")
    _git(root, "config", "user.name", "sweep test")
    _git(root, "config", "commit.gpgsign", "false")
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _replying(*answers_by_model: tuple[str, str]):
    """A fake model: the first answer registered for the model asked, then repeat it."""
    calls: list[str] = []

    def call(model: str, messages: list[dict[str, str]]) -> str:
        calls.append(model)
        for name, answer in answers_by_model:
            if name == model:
                return answer
        raise AssertionError(f"unexpected model {model}")

    call.calls = calls  # type: ignore[attr-defined]
    return call


_BROKEN = "import os\n\n\ndef load(path):\n    data = open(path).read()\n    return dat\n\n\nprint(os.sep)\n"


@runs_git
@needs_ruff
def test_lint_fix_is_committed_on_a_branch_and_the_checkout_is_untouched(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"pkg/mod.py": _BROKEN})
    head = _git(root, "rev-parse", "HEAD")
    model = _replying(
        ("m7", "```python\ndef load(path):\n    data = open(path).read()\n    return data\n```")
    )

    report = run_sweep(
        root, SweepOptions(model="m7", fallback_model=None, tests=False), call_model=model
    )

    # ruff reports F841 (`data` unused) before F821 (`dat` undefined); the one-line answer
    # clears both, so the F841 fix is the only commit and F821 never needs its own.
    fixed = [o for o in report.outcomes if o.status == "fixed" and o.target_file == "pkg/mod.py"]
    assert len(fixed) == 1 and fixed[0].commit and fixed[0].diagnostic.code == "F841"
    assert not [o for o in report.outcomes if o.diagnostic.code == "F821"]
    assert report.kept_branch and report.branch.startswith("chore/sweep/")
    assert "return data" in _git(root, "show", f"{report.branch}:pkg/mod.py")
    # the operator's checkout: same HEAD, same file, same branch, no worktree left behind
    assert _git(root, "rev-parse", "HEAD") == head
    assert (root / "pkg" / "mod.py").read_text(encoding="utf-8") == _BROKEN
    assert _git(root, "branch", "--show-current") == "main"
    assert "sweep-" not in _git(root, "worktree", "list")


@runs_git
@needs_ruff
def test_a_cheating_answer_is_refused_and_nothing_is_committed(tmp_path: Path) -> None:
    root = _repo(tmp_path, {"pkg/mod.py": _BROKEN})
    model = _replying(
        (
            "m7",
            "```python\ndef load(path):\n    data = open(path).read()\n    return dat  # noqa: F821\n```",
        )
    )

    report = run_sweep(
        root,
        SweepOptions(model="m7", fallback_model=None, tests=False, retries=2),
        call_model=model,
    )

    outcome = next(o for o in report.outcomes if o.diagnostic.code == "F821")
    assert outcome.status == "failed" and "noqa" in outcome.reason
    assert outcome.attempts == 2
    assert not report.kept_branch  # nothing fixed → the empty branch is deleted
    assert report.branch not in _git(root, "branch", "--list")


@runs_git
@needs_ruff
def test_fixing_another_same_rule_diagnostic_is_not_the_targeted_fix(tmp_path: Path) -> None:
    """Two F841 in one snippet: an answer that clears the OTHER one drops the per-rule count,
    but the targeted diagnostic is still there, so it must not be reported as fixed."""
    root = _repo(tmp_path, {"pkg/two.py": "def f():\n    a = 1\n    b = 2\n    return 0\n"})
    # whichever F841 is targeted, the answer removes `b` and keeps the unused `a`
    model = _replying(("m7", "```python\ndef f():\n    a = 1\n    return 0\n```"))

    report = run_sweep(
        root,
        SweepOptions(model="m7", fallback_model=None, tests=False, retries=1),
        call_model=model,
    )

    target_a = next(
        o for o in report.outcomes if o.diagnostic.code == "F841" and o.diagnostic.line == 2
    )
    assert target_a.status == "failed" and "line 2" in target_a.reason


_CALC = "def add(a, b):\n    return a - b\n"
_CALC_TEST = "from calc import add\n\n\ndef test_add():\n    assert add(2, 3) == 5\n"
_CALC_FIXED = "```python\ndef add(a, b):\n    return a + b\n```"


@runs_pytest
def test_a_failing_test_escalates_to_the_fallback_model(tmp_path: Path) -> None:
    root = _repo(
        tmp_path, {"calc.py": _CALC, "tests/test_calc.py": _CALC_TEST, "tests/__init__.py": ""}
    )
    # the primary model keeps returning the bug; the fallback fixes it
    model = _replying(
        ("m7", "```python\ndef add(a, b):\n    return b - a\n```"), ("m32", _CALC_FIXED)
    )

    report = run_sweep(
        root,
        SweepOptions(
            model="m7", fallback_model="m32", lint=False, tests=True, python=[sys.executable]
        ),
        call_model=model,
    )

    assert report.baseline_failures == ["tests/test_calc.py::test_add"]
    outcome = next(o for o in report.outcomes if o.diagnostic.kind == "test")
    assert outcome.status == "fixed" and outcome.model == "m32"
    assert outcome.target_file == "calc.py"  # the source was changed, never the test
    assert model.calls.count("m7") == 2 and "m32" in model.calls  # type: ignore[attr-defined]
    assert "a + b" in _git(root, "show", f"{report.branch}:calc.py")
    assert report.final_failures == []


@runs_pytest
def test_a_failure_inside_the_test_itself_is_skipped_not_rewritten(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        {"tests/__init__.py": "", "tests/test_x.py": "def test_x():\n    assert 1 == 2\n"},
    )
    model = _replying(("m7", "unused"))

    report = run_sweep(
        root,
        SweepOptions(
            model="m7", fallback_model=None, lint=False, tests=True, python=[sys.executable]
        ),
        call_model=model,
    )

    outcome = next(o for o in report.outcomes if o.diagnostic.kind == "test")
    assert outcome.status == "skipped" and "inside the test" in outcome.reason
    assert model.calls == []  # type: ignore[attr-defined]


@runs_pytest
@needs_ruff
def test_a_lint_fix_that_breaks_a_passing_test_is_dropped_from_the_branch(tmp_path: Path) -> None:
    root = _repo(
        tmp_path,
        {
            "val.py": "def value():\n    a = 1; b = 2\n    return a + b\n",
            "tests/__init__.py": "",
            "tests/test_val.py": "from val import value\n\n\ndef test_value():\n    assert value() == 3\n",
        },
    )
    # splits the line (ruff is satisfied) but changes the value (the test is not)
    model = _replying(
        ("m7", "```python\ndef value():\n    a = 1\n    b = 5\n    return a + b\n```")
    )

    report = run_sweep(
        root,
        SweepOptions(
            model="m7", fallback_model=None, tests=True, autofix=False, python=[sys.executable]
        ),
        call_model=model,
    )

    outcome = next(o for o in report.outcomes if o.diagnostic.code == "E702")
    assert outcome.status == "rolled_back" and "test_value" in outcome.reason
    assert not report.kept_branch
    assert report.final_failures == []


def test_a_candidate_that_fails_its_check_is_undone(tmp_path: Path) -> None:
    """A written-but-failed answer must not stay in the file: the next fix in the same file
    would read it as the original, and its commit would carry the bad edit along."""
    from navig.core.evolution.sweep import SnippetFixEvolver

    original = "def f():\n    return dat\n"
    (tmp_path / "m.py").write_text(original, encoding="utf-8")
    seen: list[str] = []

    def check() -> str | None:
        seen.append((tmp_path / "m.py").read_text(encoding="utf-8"))
        return "ruff still reports F821"

    evolver = SnippetFixEvolver(
        root=tmp_path,
        rel_file="m.py",
        window=(0, 2),
        model="m7",
        call_model=lambda _m, _msgs: "```python\ndef f():\n    return datt\n```",
        check=check,
        user_prompt="fix",
        system_prompt="fix",
        test_fix=False,
        max_retries=2,
    )
    result = evolver.evolve("F821")

    assert not result.success and result.attempts == 2
    assert all("datt" in text for text in seen)  # the candidate really was on disk for the check
    assert (tmp_path / "m.py").read_text(encoding="utf-8") == original


@runs_pytest
def test_a_later_fix_is_shown_the_right_code_after_an_earlier_fix_moved_it(tmp_path: Path) -> None:
    """Regression (live run, 2026-09-30): the first fix grew `median`, every line below it
    shifted, and the stale baseline line for `chunk` then pointed INSIDE the new `median` —
    so the model was shown the wrong function and its answer was refused."""
    root = _repo(
        tmp_path,
        {
            "tests/__init__.py": "",
            "calc.py": "def grow():\n    return 1\n\n\ndef later():\n    return 1\n",
            "tests/test_calc.py": (
                "from calc import grow, later\n\n\n"
                "def test_grow():\n    assert grow() == 2\n\n\n"
                "def test_later():\n    assert later() == 3\n"
            ),
        },
    )
    shown: list[str] = []

    def model(_name: str, messages: list[dict[str, str]]) -> str:
        snippet = messages[1]["content"].split("Source snippet to fix", 1)[1]
        shown.append(snippet)
        if "def later" in snippet:
            return "```python\ndef later():\n    return 3\n```"
        # grow() gains several lines, pushing later() far down the file
        return "```python\ndef grow():\n    one = 1\n    two = one + one\n    three = two\n    return three\n```"

    report = run_sweep(
        root,
        SweepOptions(
            model="m7", fallback_model=None, lint=False, tests=True, python=[sys.executable]
        ),
        call_model=model,
    )

    assert [o.status for o in report.outcomes] == ["fixed", "fixed"]
    assert "def later" in shown[-1] and "def grow" not in shown[-1]
    assert report.final_failures == []


# ─────────────────────────────────────────────────────────────────────────────
# A ruff that could not run is not a clean tree
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("rc, timed_out", [(2, False), (-1, True)])
def test_a_failed_ruff_check_raises_instead_of_reporting_clean(monkeypatch, tmp_path, rc, timed_out):
    """Review #1676: `--exit-zero` still exits 2 on a config error, with empty stdout,
    and `run.out or "[]"` turned that into "no diagnostics"."""
    from navig.core.evolution import sweep

    monkeypatch.setattr(
        sweep, "_run", lambda *a, **k: sweep._Run(rc=rc, out="", err="ruff failed", timed_out=timed_out)
    )
    with pytest.raises(sweep.SweepError):
        sweep.ruff_diagnostics(["ruff"], tmp_path, ["."])
    with pytest.raises(sweep.SweepError):
        sweep.ruff_autofix(["ruff"], tmp_path, None)


def test_a_successful_ruff_run_still_parses(monkeypatch, tmp_path):
    from navig.core.evolution import sweep

    monkeypatch.setattr(
        sweep, "_run", lambda *a, **k: sweep._Run(rc=0, out="[]", err="", timed_out=False)
    )
    assert sweep.ruff_diagnostics(["ruff"], tmp_path, ["."]) == []
    sweep.ruff_autofix(["ruff"], tmp_path, None)
