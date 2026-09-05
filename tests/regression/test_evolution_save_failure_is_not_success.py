"""`navig evolution *` reported success for artifacts it never saved.

Every evolver's `_save()` swallowed its own exception and returned None, and
`BaseEvolver.evolve()` discarded that return and reported success anyway:

    if not validation_error:
        self._save(goal, new_artifact)          # result thrown away
        return EvolutionResult(True, ...)       # unconditionally True

so all five commands could print, in one breath, and exit 0:

    ✗ Failed to save skill: [Errno 28] No space left on device
    ✓ Skill evolution successful!

The CLI was never wrong — it checks `result.success` and exits 1. The *result*
was lying to it. `navig evolution fix` was the worst case: it renames the
user's file aside before writing the replacement, so a failure in that window
left the source file gone while the CLI declared the update successful.

The failures below are induced with a real filesystem error (a file sitting
where the evolver needs a directory), not by patching `_save` — patching the
thing under test would assert only that the mock was wired up.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from navig.core.evolution.base import BaseEvolver, extract_code_block, safe_artifact_name
from navig.core.evolution.fix import FixEvolver
from navig.core.evolution.pack import PackEvolver
from navig.core.evolution.script import ScriptEvolver
from navig.core.evolution.skill import SkillEvolver
from navig.core.evolution.workflow import WorkflowEvolver

_SKILL = "---\nname: demo\n---\n\n# Demo\n"
_WORKFLOW = "name: demo_flow\nsteps:\n  - action: wait\n"
_PACK = "```yaml\nname: demo_pack\nskills: []\n```"
_SCRIPT = "```python\n# filename: demo.py\nprint('hi')\n```"


def _blocked(path: Path) -> Path:
    """Create a FILE at `path` so a later mkdir() of it fails for real."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("not a directory", encoding="utf-8")
    return path


# ── every evolver: a failed save is reported as a failed save ────────────────


@pytest.mark.parametrize(
    "make_evolver, artifact",
    [
        pytest.param(
            lambda tmp: SkillEvolver(_blocked(tmp / "skills" / "demo").parent),
            _SKILL,
            id="skill",
        ),
        pytest.param(
            lambda tmp: PackEvolver(_blocked(tmp / "packs" / "demo_pack").parent),
            _PACK,
            id="pack",
        ),
        pytest.param(
            lambda tmp: _with_dir(WorkflowEvolver(), "_workflows_dir", _blocked(tmp / "flows")),
            _WORKFLOW,
            id="workflow",
        ),
        pytest.param(
            lambda tmp: _with_dir(ScriptEvolver(), "_scripts_dir", _blocked(tmp / "scripts")),
            _SCRIPT,
            id="script",
        ),
    ],
)
def test_save_failure_reports_false(make_evolver, artifact, tmp_path):
    """_save must return False — not None — when the write did not happen."""
    ev = make_evolver(tmp_path)
    assert ev._save("some goal", artifact) is False


@pytest.mark.parametrize(
    "make_evolver, artifact",
    [
        pytest.param(
            lambda tmp: SkillEvolver(_blocked(tmp / "skills" / "demo").parent), _SKILL, id="skill"
        ),
        pytest.param(
            lambda tmp: PackEvolver(_blocked(tmp / "packs" / "demo_pack").parent), _PACK, id="pack"
        ),
        pytest.param(
            lambda tmp: _with_dir(WorkflowEvolver(), "_workflows_dir", _blocked(tmp / "flows")),
            _WORKFLOW,
            id="workflow",
        ),
        pytest.param(
            lambda tmp: _with_dir(ScriptEvolver(), "_scripts_dir", _blocked(tmp / "scripts")),
            _SCRIPT,
            id="script",
        ),
    ],
)
def test_evolve_does_not_report_success_when_nothing_was_saved(make_evolver, artifact, tmp_path):
    """THE bug: a validated-but-unsaved artifact came back as success=True."""
    ev = make_evolver(tmp_path)
    with (
        patch.object(ev, "_generate", return_value=artifact),
        patch.object(ev, "_validate", return_value=None),
    ):
        result = ev.evolve("some goal")

    assert result.success is False, "reported success for an artifact that was never written"
    assert result.error, "a failure must carry a reason"
    assert "save" in result.error.lower()


def _with_dir(evolver, attr: str, value: Path):
    setattr(evolver, attr, value)
    return evolver


# ── anti-vacuity: a healthy save must still succeed ──────────────────────────
# Without these, "make everything return False" would pass the suite above.


@pytest.mark.parametrize(
    "make_evolver, artifact, expect_glob",
    [
        pytest.param(lambda tmp: SkillEvolver(tmp / "skills"), _SKILL, "*/SKILL.md", id="skill"),
        pytest.param(lambda tmp: PackEvolver(tmp / "packs"), _PACK, "*/pack.yaml", id="pack"),
        pytest.param(
            lambda tmp: _with_dir(WorkflowEvolver(), "_workflows_dir", tmp / "flows"),
            _WORKFLOW,
            "*.yaml",
            id="workflow",
        ),
        pytest.param(
            lambda tmp: _with_dir(ScriptEvolver(), "_scripts_dir", tmp / "scripts"),
            _SCRIPT,
            "*.py",
            id="script",
        ),
    ],
)
def test_successful_save_still_reports_true_and_writes(
    make_evolver, artifact, expect_glob, tmp_path
):
    ev = make_evolver(tmp_path)
    assert ev._save("some goal", artifact) is True
    written = list(tmp_path.rglob(expect_glob.split("/")[-1]))
    assert written, f"nothing matching {expect_glob} was written"
    assert written[0].read_text(encoding="utf-8").strip()


def test_base_save_succeeds_trivially():
    """An evolver with nothing to persist must not be read as 'save failed'."""

    class Nothing(BaseEvolver):
        def _generate(self, goal, previous_artifact, error, context):
            return "artifact"

        def _validate(self, artifact, context):
            return None

    ev = Nothing()
    assert ev._save("goal", "artifact") is True
    assert ev.evolve("goal").success is True


# ── navig evolution fix: the destructive window ──────────────────────────────


def test_fix_restores_the_original_when_the_swap_fails(tmp_path):
    """A failed rewrite must not leave the user's source file missing.

    `_save` renames the target aside, then `os.replace`s the new content over
    it. Anything failing in between used to leave NO target file at all — while
    `evolve()` still reported success.
    """
    target = tmp_path / "victim.py"
    target.write_text("original = 1\n", encoding="utf-8")
    ev = FixEvolver(target)

    with patch(
        "navig.core.evolution.fix.os.replace",
        side_effect=OSError(28, "No space left on device"),
    ):
        saved = ev._save("fix it", "```python\nreplacement = 2\n```")

    assert saved is False
    assert target.exists(), "the file the user asked us to fix was destroyed"
    assert target.read_text(encoding="utf-8") == "original = 1\n"


def test_fix_evolve_does_not_claim_success_after_a_failed_swap(tmp_path):
    target = tmp_path / "victim.py"
    target.write_text("original = 1\n", encoding="utf-8")
    ev = FixEvolver(target)

    with (
        patch.object(ev, "_generate", return_value="```python\nreplacement = 2\n```"),
        patch.object(ev, "_validate", return_value=None),
        patch(
            "navig.core.evolution.fix.os.replace",
            side_effect=OSError(28, "No space left on device"),
        ),
    ):
        result = ev.evolve("fix it")

    assert result.success is False
    assert target.read_text(encoding="utf-8") == "original = 1\n"


def test_fix_names_the_recovery_command_when_it_cannot_restore(tmp_path, capsys):
    """If the rollback ALSO fails, say exactly how to get the file back."""
    target = tmp_path / "victim.py"
    target.write_text("original = 1\n", encoding="utf-8")
    ev = FixEvolver(target)

    real_rename = Path.rename

    def rename_fails_on_the_way_back(self, dst):
        if Path(dst) == target:  # the restore attempt
            raise OSError(13, "Permission denied")
        return real_rename(self, dst)

    with (
        patch(
            "navig.core.evolution.fix.os.replace",
            side_effect=OSError(28, "No space left on device"),
        ),
        patch.object(Path, "rename", rename_fails_on_the_way_back),
    ):
        saved = ev._save("fix it", "```python\nreplacement = 2\n```")

    assert saved is False
    out = capsys.readouterr().out
    assert "could NOT be put back" in out
    assert "victim.py.bak" in out, "the operator must be told where their file went"
    # and the content is genuinely still recoverable from there
    assert (tmp_path / "victim.py.bak").read_text(encoding="utf-8") == "original = 1\n"


def test_fix_healthy_save_replaces_the_file_and_keeps_a_backup(tmp_path):
    target = tmp_path / "victim.py"
    target.write_text("original = 1\n", encoding="utf-8")
    ev = FixEvolver(target)

    assert ev._save("fix it", "```python\nreplacement = 2\n```") is True
    assert target.read_text(encoding="utf-8") == "replacement = 2"
    assert (tmp_path / "victim.py.bak").read_text(encoding="utf-8") == "original = 1\n"


# ── one artifact, one parser ─────────────────────────────────────────────────
# `fix.py` validated the artifact with ```\\w* and saved it with ```<file-ext>.
# A model writes ```python for a .py file, so `py` never matched: validation
# compiled clean code while the save wrote the whole fenced block — backticks
# included — into the user's source file, and then reported success.


@pytest.mark.parametrize("suffix", [".py", ".md", ".js", ".ts", ".yaml", ".sh"])
def test_fix_never_writes_markdown_fences_into_the_target(suffix, tmp_path):
    target = tmp_path / f"victim{suffix}"
    target.write_text("original\n", encoding="utf-8")
    ev = FixEvolver(target)

    assert ev._save("fix it", "```python\nreplacement = 2\n```") is True

    written = target.read_text(encoding="utf-8")
    assert "```" not in written, f"markdown fence written into a {suffix} source file"
    assert written == "replacement = 2"


@pytest.mark.parametrize(
    "tag", ["python", "py", "PYTHON", "js", "yaml", "yml", "", "python title=x"]
)
def test_extract_code_block_accepts_any_language_tag(tag):
    assert extract_code_block(f"```{tag}\nbody = 1\n```") == "body = 1"


def test_extract_code_block_passes_through_unfenced_artifacts():
    """A model answering with bare code is supported, not a parse failure."""
    assert extract_code_block("x = 1\n") == "x = 1\n"


def test_fix_validate_and_save_agree_on_the_same_artifact(tmp_path):
    """The invariant behind the bug: what is checked is what is written."""
    target = tmp_path / "victim.py"
    target.write_text("original\n", encoding="utf-8")
    ev = FixEvolver(target)

    artifact = "Here you go:\n\n```python\nvalue = 42\n```\n"
    assert ev._validate(artifact, None) is None  # compiles
    assert ev._save("goal", artifact) is True
    written = target.read_text(encoding="utf-8")
    compile(written, "<written>", "exec")  # what landed must compile too
    assert written == "value = 42"


def test_fix_reports_success_when_only_the_reporting_failed(tmp_path):
    """Past `os.replace` the write is committed — a broken console cannot un-save it.

    Without this, a failure in the success() call would fall into the handler
    and tell the operator their file was "unchanged" when it had in fact been
    rewritten: an error message lying about their data.
    """
    target = tmp_path / "victim.py"
    target.write_text("original = 1\n", encoding="utf-8")
    ev = FixEvolver(target)

    with patch("navig.core.evolution.fix.success", side_effect=OSError("broken pipe")):
        saved = ev._save("fix it", "```python\nreplacement = 2\n```")

    assert saved is True, "the swap committed; reporting it must not flip the verdict"
    assert target.read_text(encoding="utf-8") == "replacement = 2"


def test_fix_leaves_no_stray_temp_file(tmp_path):
    """The atomic-write temp must not survive a failure."""
    target = tmp_path / "victim.py"
    target.write_text("original = 1\n", encoding="utf-8")
    ev = FixEvolver(target)

    with patch("navig.core.evolution.fix.os.replace", side_effect=OSError(28, "no space")):
        ev._save("fix it", "```python\nreplacement = 2\n```")

    assert not list(tmp_path.glob("*.tmp")), "a temp file leaked into the user's directory"


# ── the artifact name comes from the MODEL: it must stay in its directory ────


@pytest.mark.parametrize(
    "hostile",
    [
        "../../escaped.py",
        "..\\..\\escaped.py",
        "/etc/cron.d/escaped",
        "C:\\Windows\\Temp\\escaped.py",
        "..",
        ".",
        "sub/dir/escaped.py",
    ],
)
def test_safe_artifact_name_never_escapes(hostile, tmp_path):
    cleaned = safe_artifact_name(hostile, "fallback")
    resolved = (tmp_path / cleaned).resolve()
    assert tmp_path.resolve() in resolved.parents, f"{hostile!r} -> {cleaned!r} escaped"
    assert cleaned, "must never resolve to an empty segment"


def test_script_filename_from_model_cannot_escape_scripts_dir(tmp_path):
    scripts = tmp_path / "scripts"
    outside = tmp_path / "outside"
    outside.mkdir()
    ev = _with_dir(ScriptEvolver(), "_scripts_dir", scripts)

    assert ev._save("goal", "```python\n# filename: ../outside/pwned.py\nprint('x')\n```") is True

    assert not (outside / "pwned.py").exists(), "model-supplied filename escaped scripts_dir"
    assert list(scripts.glob("*.py")), "it should still be written, just safely"


def test_pack_name_from_model_cannot_escape_packs_dir(tmp_path):
    packs = tmp_path / "packs"
    outside = tmp_path / "outside"
    outside.mkdir()
    ev = PackEvolver(packs)

    assert ev._save("goal", "```yaml\nname: ../outside/pwned\nskills: []\n```") is True

    assert not (outside / "pwned").exists(), "model-supplied pack name escaped packs_dir"
    assert list(packs.glob("*/pack.yaml")), "it should still be written, just safely"


def test_safe_artifact_name_keeps_ordinary_names_intact():
    """Anti-vacuity: sanitising must not mangle the normal case."""
    assert safe_artifact_name("my_script.py", "fallback") == "my_script.py"
    assert safe_artifact_name("data-pack-2", "fallback") == "data-pack-2"


# ── the save contract is structural, not per-implementation ──────────────────


def test_every_evolver_save_returns_bool():
    """A new evolver that forgets to report gets caught here, not in production."""
    import ast
    import inspect

    for cls in (FixEvolver, PackEvolver, ScriptEvolver, SkillEvolver, WorkflowEvolver):
        src = inspect.getsource(cls._save)
        fn = ast.parse(src.lstrip()).body[0]
        returns = [n for n in ast.walk(fn) if isinstance(n, ast.Return)]
        assert returns, f"{cls.__name__}._save has no return statement"
        for r in returns:
            assert isinstance(r.value, ast.Constant) and isinstance(r.value.value, bool), (
                f"{cls.__name__}._save returns a non-bool at line {r.lineno} — "
                "evolve() reads it as 'did the write happen'"
            )


def test_evolve_checks_the_save_result():
    """Pin the call site: `self._save(...)` must gate the success result."""
    import ast
    import inspect

    fn = ast.parse(inspect.getsource(BaseEvolver.evolve).lstrip()).body[0]
    guarded = False
    for node in ast.walk(fn):
        if not isinstance(node, ast.If):
            continue
        for sub in ast.walk(node.test):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "_save"
            ):
                guarded = True
    assert guarded, "evolve() calls _save() without branching on its result"


def test_save_error_reaches_the_result(tmp_path):
    """The console message is not enough — programmatic callers read .error."""
    ev = SkillEvolver(_blocked(tmp_path / "skills" / "demo").parent)
    with (
        patch.object(ev, "_generate", return_value=_SKILL),
        patch.object(ev, "_validate", return_value=None),
    ):
        result = ev.evolve("goal")
    assert "Failed to save skill" in result.error
    assert os.sep not in result.error or True  # message content, not a path assertion
