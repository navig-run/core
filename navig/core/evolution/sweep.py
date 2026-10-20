"""navig.core.evolution.sweep — the unattended check-and-fix loop behind ``navig evolve sweep``.

The shape, and why each step is there:

1. **Isolate.** Everything happens in a fresh git worktree on a new ``chore/sweep/…`` branch
   cut from the committed HEAD. The operator's checkout — and any uncommitted work in it — is
   never touched, and nothing is pushed. The result is a branch to review, not a change to trust.
2. **Free pass first.** ``ruff check --fix`` applies the fixes ruff itself calls safe. No model.
3. **Model per diagnostic.** For every lint error left, a local model sees only a small window of
   the file around it and returns the corrected window. Small models are good at "fix this one
   error in these 30 lines" and bad at "rewrite this file".
4. **The check decides, not the model.** A candidate is spliced in and the SAME check is re-run:
   the error must be gone and nothing new may appear, or the file is restored. Answers that
   cheat (``# noqa``, ``pytest.skip``, deleting half the window, editing a test) are refused
   before they are even tried.
5. **Escalate.** A failing test the primary model cannot fix is handed to a larger fallback model
   — batched after the primary pass so the big model is loaded once, not once per test.
6. **Gate.** After each phase the whole suite is re-run against the baseline; a commit that made a
   passing test fail is dropped from the branch.

Retries reuse :class:`~navig.core.evolution.base.BaseEvolver` — generate → validate → retry with
the error fed back — and the code-block wrapper is removed by the shared
:func:`~navig.core.evolution.base.extract_code_block`, the one extractor every evolver uses.
"""

from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from collections import Counter
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from navig.core.evolution.base import BaseEvolver, extract_code_block

DEFAULT_MODEL = "qwen2.5-coder:7b"
DEFAULT_FALLBACK_MODEL = "qwen2.5-coder:32b"
BRANCH_PREFIX = "chore/sweep"

#: A model reply → the text the model returned. Injected in tests; defaults to Ollama.
CallModel = Callable[[str, list[dict[str, str]]], str]
Progress = Callable[[str], None]

# Additions that make a check pass without fixing anything. Counted before/after, so an
# existing suppression comment in the window is fine — adding one is not.
_CHEAT_MARKERS = ("noqa", "type: ignore", "pragma: no cover")
_TEST_CHEAT_MARKERS = ("pytest.skip", "pytest.xfail", "mark.skip", "mark.xfail", "unittest.skip")

# A test that fails for one of these reasons is failing because of the machine, not the code.
# A model asked to "fix" it will invent a code change for an environment problem.
_ENV_ERRORS = (
    "ModuleNotFoundError",
    "FileNotFoundError",
    "PermissionError",
    "ConnectionError",
    "ConnectionRefusedError",
    "TimeoutError",
    "fixture '",
)

_FRAME_RE = re.compile(r"^\s*((?:[A-Za-z]:[\\/])?[^\s:<>\"'][^:<>\"']*?\.py):(\d+):", re.MULTILINE)


# ─────────────────────────────────────────────────────────────────────────────
# Data
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class Diagnostic:
    kind: str  # "lint" | "test"
    file: str  # repo-relative, forward slashes
    line: int
    code: str  # ruff rule, or the test node id
    message: str
    detail: str = ""  # failure text for tests

    @property
    def where(self) -> str:
        return f"{self.file}:{self.line}"


@dataclass
class FixOutcome:
    diagnostic: Diagnostic
    status: str  # fixed | failed | skipped | rolled_back
    model: str = ""
    attempts: int = 0
    seconds: float = 0.0
    reason: str = ""
    commit: str = ""
    target_file: str = ""
    touched: list[str] = field(default_factory=list)  # files the commit changed (gate blame)


@dataclass
class SweepOptions:
    model: str = DEFAULT_MODEL
    fallback_model: str | None = DEFAULT_FALLBACK_MODEL
    lint: bool = True
    tests: bool | None = None  # None = auto-detect
    autofix: bool = True
    retries: int = 2
    limit: int = 50
    lint_select: str | None = None  # ruff --extend-select
    python: list[str] | None = None
    test_timeout: float = 900.0
    max_minutes: float | None = None
    keep_worktree: bool = False
    dry_run: bool = False


@dataclass
class SweepReport:
    repo: str
    base: str
    branch: str = ""
    worktree: str = ""
    outcomes: list[FixOutcome] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    autofix_files: int = 0
    baseline_failures: list[str] = field(default_factory=list)
    final_failures: list[str] = field(default_factory=list)
    started: str = ""
    seconds: float = 0.0
    kept_branch: bool = False
    report_path: str = ""

    def count(self, status: str) -> int:
        return sum(1 for o in self.outcomes if o.status == status)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class SweepError(RuntimeError):
    """The sweep could not start (not a repo, no model, no tools)."""


# ─────────────────────────────────────────────────────────────────────────────
# Subprocess + text helpers
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class _Run:
    rc: int
    out: str
    err: str
    timed_out: bool = False


def _run(
    cmd: list[str], cwd: Path, timeout: float = 300.0, env: dict[str, str] | None = None
) -> _Run:
    """Run *cmd*; on timeout take down the whole process TREE, not just the direct child.

    ``subprocess.run(timeout=)`` kills only the child it spawned — for ``py -3 -m pytest`` that
    is the launcher, and the interpreter running the tests would be left behind holding pipes.

    stdin is closed, never inherited: an unattended job has nobody to answer a prompt, so a
    tool that asks (a git credential or editor prompt) must fail instead of waiting forever.
    """
    from navig.core.aio_subprocess import terminate_process_tree_sync  # noqa: PLC0415
    from navig.platform.process import spawn_kwargs  # noqa: PLC0415

    proc = subprocess.Popen(  # noqa: S603 — argv list, no shell
        cmd,
        cwd=str(cwd),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        **spawn_kwargs(),
    )
    try:
        out, err = proc.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        terminate_process_tree_sync(proc)
        out, err = proc.communicate()
        timed_out = True
    return _Run(
        rc=proc.returncode if not timed_out else -1,
        out=(out or b"").decode("utf-8", errors="replace"),
        err=(err or b"").decode("utf-8", errors="replace"),
        timed_out=timed_out,
    )


def _git(root: Path, *args: str, timeout: float = 120.0) -> _Run:
    return _run(["git", *args], root, timeout=timeout)


@dataclass
class _Source:
    lines: list[str]
    newline: str

    @classmethod
    def read(cls, path: Path) -> _Source | None:
        try:
            raw = path.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            return None
        newline = "\r\n" if "\r\n" in raw else "\n"
        return cls(raw.replace("\r\n", "\n").split("\n"), newline)

    def text(self) -> str:
        return self.newline.join(self.lines)

    def write(self, path: Path) -> None:
        path.write_bytes(self.text().encode("utf-8"))


def is_test_file(rel: str) -> bool:
    p = Path(rel)
    return (
        p.name.startswith("test_")
        or p.name.endswith("_test.py")
        or p.name == "conftest.py"
        or any(part in ("tests", "test", "testing") for part in p.parts[:-1])
    )


def _enclosing_def(source_text: str, line: int) -> tuple[int, int] | None:
    """(first, last) 1-based lines of the innermost function containing *line*."""
    try:
        tree = ast.parse(source_text)
    except SyntaxError:
        return None
    best: tuple[int, int] | None = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            first = min([node.lineno] + [d.lineno for d in node.decorator_list])
            last = getattr(node, "end_lineno", None) or node.lineno
            if first <= line <= last and (best is None or last - first < best[1] - best[0]):
                best = (first, last)
    return best


def pick_window(src: _Source, line: int, max_fn: int, pad: int) -> tuple[int, int]:
    """0-based half-open [start, end) window around 1-based *line*.

    The enclosing function when it is small enough (the model then sees every name the error
    can depend on), otherwise ``line ± pad``.
    """
    n = len(src.lines)
    span = _enclosing_def("\n".join(src.lines), line)
    if span and span[1] - span[0] + 1 <= max_fn:
        return span[0] - 1, min(span[1], n)
    return max(0, line - 1 - pad), min(n, line + pad)


def _indent(s: str) -> int:
    return len(s) - len(s.lstrip(" \t"))


def normalise_reply(reply: str, original: list[str]) -> list[str]:
    """The model's reply → the replacement lines for *original*.

    Removes the code-block wrapper (shared extractor, indentation kept) and, when the model
    dedented the whole snippet to column 0 — small models do — shifts it back to where it was.
    """
    code = extract_code_block(reply, keep_indent=True)
    new = code.replace("\r\n", "\n").split("\n")
    old_min = min((_indent(s) for s in original if s.strip()), default=0)
    new_min = min((_indent(s) for s in new if s.strip()), default=0)
    if new_min < old_min:
        pad = " " * (old_min - new_min)
        new = [pad + s if s.strip() else s for s in new]
    return new


def cheat_reason(old: list[str], new: list[str], *, test_fix: bool) -> str | None:
    """Why a candidate is refused before it is tried, or None."""
    old_text, new_text = "\n".join(old), "\n".join(new)
    if not new_text.strip():
        return "the answer was empty"
    if [s.rstrip() for s in old] == [s.rstrip() for s in new]:
        return "the answer changed nothing"
    markers = _CHEAT_MARKERS + (_TEST_CHEAT_MARKERS if test_fix else ())
    for marker in markers:
        if new_text.count(marker) > old_text.count(marker):
            return f"the answer silenced the check (added `{marker}`) instead of fixing it"
    old_code = sum(1 for s in old if s.strip())
    new_code = sum(1 for s in new if s.strip())
    if old_code >= 6 and new_code < old_code * 0.5:
        return f"the answer deleted too much ({old_code} → {new_code} lines)"
    if not test_fix and new_code < old_code - 2:
        # A lint error disappears when its code does. A lint fix rewrites a line or two; it
        # does not remove a block.
        return f"the answer deleted code ({old_code} → {new_code} lines) instead of fixing it"
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Tools: ruff + pytest
# ─────────────────────────────────────────────────────────────────────────────


def ruff_command() -> list[str] | None:
    exe = shutil.which("ruff")
    if exe:
        return [exe]
    import importlib.util  # noqa: PLC0415

    if importlib.util.find_spec("ruff") is not None:
        return [sys.executable, "-m", "ruff"]
    return None


def ruff_diagnostics(
    ruff: list[str], root: Path, targets: list[str], select: str | None = None
) -> list[Diagnostic]:
    cmd = [*ruff, "check", "--output-format", "json", "--no-fix", "--exit-zero"]
    if select:
        cmd += ["--extend-select", select]
    run = _run([*cmd, *targets], root, timeout=300)
    try:
        data = json.loads(run.out or "[]")
    except json.JSONDecodeError as exc:
        raise SweepError(f"ruff output was not JSON: {(run.err or run.out)[:300]}") from exc
    diags: list[Diagnostic] = []
    for item in data:
        try:
            rel = Path(item["filename"]).resolve().relative_to(root.resolve()).as_posix()
        except (KeyError, ValueError):
            continue
        diags.append(
            Diagnostic(
                kind="lint",
                file=rel,
                line=int((item.get("location") or {}).get("row") or 1),
                code=str(item.get("code") or "syntax"),
                message=str(item.get("message") or ""),
            )
        )
    return diags


def ruff_autofix(ruff: list[str], root: Path, select: str | None) -> None:
    cmd = [*ruff, "check", "--fix", "--exit-zero", "--quiet"]
    if select:
        cmd += ["--extend-select", select]
    _run([*cmd, "."], root, timeout=600)


@dataclass
class PytestRun:
    failures: dict[str, Diagnostic] = field(default_factory=dict)
    passed: set[str] = field(default_factory=set)
    skipped: set[str] = field(default_factory=set)
    problem: str = ""  # set when the run itself broke (timeout, usage error, no junit)


def _nodeid(case: ET.Element) -> str:
    file = (case.get("file") or "").replace("\\", "/")
    name = case.get("name") or ""
    classname = case.get("classname") or ""
    if not file:
        return classname + ("::" + name if classname else name)
    module = file[:-3].replace("/", ".") if file.endswith(".py") else file
    cls = classname[len(module) + 1 :] if classname.startswith(module + ".") else ""
    return "::".join(part for part in (file, cls.replace(".", "::"), name) if part)


def run_pytest(
    python: list[str], root: Path, args: list[str], timeout: float, env: dict[str, str]
) -> PytestRun:
    fd, xml_path = tempfile.mkstemp(suffix=".xml", prefix="navig-sweep-")
    os.close(fd)
    empty_ini: Path | None = None
    try:
        # Pin pytest to THIS tree. The sweep worktree lives inside the operator's checkout
        # (<repo>/.dev/worktrees/…), so left to itself pytest walks up past it: it would load
        # the main checkout's conftest.py on top of the worktree's copy, or adopt a parent
        # project's config (its addopts, its plugins) and report paths relative to it.
        config = _pytest_config(root)
        if config is None:
            fd, ini = tempfile.mkstemp(suffix=".ini", prefix="navig-sweep-")
            os.write(fd, b"[pytest]\n")
            os.close(fd)
            empty_ini = config = Path(ini)
        cmd = [
            *python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=short",
            "-c", str(config), f"--rootdir={root}", f"--confcutdir={root}",
            "--continue-on-collection-errors", "-o", "junit_family=xunit1",
            f"--junitxml={xml_path}", *args,
        ]  # fmt: skip
        run = _run(cmd, root, timeout=timeout, env=env)
        result = PytestRun()
        if run.timed_out:
            result.problem = f"the test run exceeded {timeout:.0f}s and was stopped"
            return result
        if run.rc == 5:
            return result  # no tests collected
        try:
            # S314 (prefer defusedxml): this is pytest's own report, written by the process
            # we just ran into a private temp file -- not network or user data. XXE does not
            # apply: xml.etree resolves no external entities and raises on undefined ones.
            tree = ET.parse(xml_path)  # noqa: S314
        except (ET.ParseError, OSError):
            result.problem = (
                f"pytest exited {run.rc} without a report: {(run.err or run.out)[-400:]}"
            )
            return result
        for case in tree.iter("testcase"):
            nid = _nodeid(case)
            bad = case.find("failure")
            if bad is None:
                bad = case.find("error")
            if bad is not None:
                text = (bad.get("message") or "") + "\n" + (bad.text or "")
                file = (case.get("file") or nid.split("::", 1)[0]).replace("\\", "/")
                result.failures[nid] = Diagnostic(
                    kind="test",
                    file=file,
                    line=int(case.get("line") or 0) + 1,
                    code=nid,
                    message=(bad.get("message") or "failed").splitlines()[0][:200]
                    if bad.get("message")
                    else "failed",
                    detail=text,
                )
            elif case.find("skipped") is not None:
                result.skipped.add(nid)
            else:
                result.passed.add(nid)
        return result
    finally:
        Path(xml_path).unlink(missing_ok=True)
        if empty_ini is not None:
            empty_ini.unlink(missing_ok=True)


def _pytest_config(root: Path) -> Path | None:
    """The project's own pytest config file at *root*, in pytest's precedence order."""
    for name, marker in (
        ("pytest.ini", ""),
        (".pytest.ini", ""),
        ("pyproject.toml", "[tool.pytest"),
        ("tox.ini", "[pytest]"),
        ("setup.cfg", "[tool:pytest]"),
    ):
        path = root / name
        try:
            if path.is_file() and (not marker or marker in path.read_text(encoding="utf-8")):
                return path
        except (OSError, UnicodeDecodeError):
            continue
    return None


def resolve_python(root: Path, main_root: Path) -> list[str] | None:
    """The project's interpreter: its own venv first (untracked, so look in the MAIN checkout)."""
    for base in (main_root, root):
        for rel in (
            ".venv/Scripts/python.exe",
            "venv/Scripts/python.exe",
            ".venv/bin/python",
            "venv/bin/python",
        ):
            cand = base / rel
            if cand.exists():
                return [str(cand)]
    if os.name == "nt" and shutil.which("py"):
        return ["py", "-3"]
    for name in ("python3", "python"):
        exe = shutil.which(name)
        if exe:
            return [exe]
    return None


def has_tests(root: Path) -> bool:
    return _pytest_config(root) is not None or (root / "tests").is_dir()


def _frames(detail: str, root: Path) -> list[tuple[str, int]]:
    frames: list[tuple[str, int]] = []
    for m in _FRAME_RE.finditer(detail):
        raw, line = m.group(1).strip(), int(m.group(2))
        p = Path(raw)
        try:
            rel = (p.resolve().relative_to(root.resolve()) if p.is_absolute() else p).as_posix()
        except ValueError:
            continue  # outside the repo (site-packages, stdlib)
        if "site-packages" in rel or not (root / rel).is_file():
            continue
        frames.append((rel, line))
    return frames


def _called_names(test_src: str, func: str) -> list[str]:
    try:
        tree = ast.parse(test_src)
    except SyntaxError:
        return []
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call):
                    f = sub.func
                    name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
                    if name and name not in names and not name.startswith("assert"):
                        names.append(name)
            break
    return names


def _def_index(root: Path) -> dict[str, list[tuple[str, int]]]:
    run = _git(root, "ls-files", "*.py")
    index: dict[str, list[tuple[str, int]]] = {}
    for rel in run.out.splitlines()[:3000]:
        if is_test_file(rel):
            continue
        try:
            tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError, ValueError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                index.setdefault(node.name, []).append((rel, node.lineno))
    return index


def locate_test_target(
    diag: Diagnostic, root: Path, index_cache: dict[str, Any]
) -> tuple[str, int] | str:
    """Source (file, line) to change for a failing test, or a reason string to skip it."""
    for err in _ENV_ERRORS:
        if err in diag.detail:
            return f"looks environmental ({err.strip(chr(39) + ' ')}), not a code bug"
    frames = [f for f in _frames(diag.detail, root) if not is_test_file(f[0])]
    if frames:
        return frames[-1]  # deepest frame in project source
    # The failure surfaced in the test body (e.g. a wrong return value): find the project
    # function the test calls.
    test_file = diag.code.split("::", 1)[0]
    func = diag.code.rsplit("::", 1)[-1].split("[", 1)[0]
    try:
        test_src = (root / test_file).read_text(encoding="utf-8")
    except OSError:
        return "could not read the test file"
    if "index" not in index_cache:
        index_cache["index"] = _def_index(root)
    index = index_cache["index"]
    for name in _called_names(test_src, func):
        if name in index:
            return index[name][0]
    return "the failure is inside the test itself; tests are never rewritten"


def _test_source(root: Path, nodeid: str, max_lines: int = 60) -> str:
    test_file = nodeid.split("::", 1)[0]
    func = nodeid.rsplit("::", 1)[-1].split("[", 1)[0]
    try:
        text = (root / test_file).read_text(encoding="utf-8")
        tree = ast.parse(text)
    except (OSError, SyntaxError, UnicodeDecodeError):
        return ""
    lines = text.split("\n")
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func:
            end = getattr(node, "end_lineno", node.lineno)
            return "\n".join(lines[node.lineno - 1 : min(end, node.lineno - 1 + max_lines)])
    return ""


# ─────────────────────────────────────────────────────────────────────────────
# The evolver: one diagnostic, one window, retries from BaseEvolver
# ─────────────────────────────────────────────────────────────────────────────

_LINT_SYSTEM = """You fix ONE lint error in a snippet of a Python file.
Return ONLY the corrected snippet, in a single ```python code block.
- Return the WHOLE snippet (every line), changed as little as possible.
- Keep the indentation exactly as given.
- Never add `# noqa`, `type: ignore`, or comments explaining the change.
- Do not remove or rename unrelated code.
- Add no new behaviour: no print, logging, or extra statements the fix does not need."""

_TEST_SYSTEM = """You fix a bug in Python source code so that a failing test passes.
You may only change the source snippet shown. Return ONLY the corrected snippet, in a single
```python code block.
- Return the WHOLE snippet (every line), changed as little as possible.
- Keep the indentation exactly as given.
- The test is correct: never change what it expects, never skip it, never special-case it.
- Do not add comments explaining the change."""


class SnippetFixEvolver(BaseEvolver):
    """Fix one diagnostic by rewriting a small window of one file.

    ``_validate`` splices the candidate in, runs *check*, and restores the file when the check
    fails — so a failed attempt never leaves a half-fixed file behind. On success the file is
    left as validated, and ``_save`` has nothing further to write.
    """

    def __init__(
        self,
        *,
        root: Path,
        rel_file: str,
        window: tuple[int, int],
        model: str,
        call_model: CallModel,
        check: Callable[[], str | None],
        user_prompt: str,
        system_prompt: str,
        test_fix: bool,
        max_retries: int,
    ):
        super().__init__(max_retries=max_retries)
        self.root = root
        self.rel_file = rel_file
        self.path = root / rel_file
        self.window = window
        self.model = model
        self.call_model = call_model
        self.check = check
        self.user_prompt = user_prompt
        self.system_prompt = system_prompt
        self.test_fix = test_fix
        source = _Source.read(self.path)
        if source is None:
            raise SweepError(f"{rel_file} is not readable UTF-8 text")
        self.original = source
        self.old_window = source.lines[window[0] : window[1]]

    def _generate(self, goal: str, previous_artifact: Any, error: str, context: Any) -> Any:
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": self.user_prompt},
        ]
        if previous_artifact:
            messages.append({"role": "assistant", "content": str(previous_artifact)})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        f"That did not work: {error[:1500]}\n"
                        "Try again. Start from the ORIGINAL snippet and return the whole corrected snippet."
                    ),
                }
            )
        return self.call_model(self.model, messages)

    def _validate(self, artifact: Any, context: Any) -> str | None:
        new_window = normalise_reply(str(artifact), self.old_window)
        refused = cheat_reason(self.old_window, new_window, test_fix=self.test_fix)
        if refused:
            return refused
        lines = list(self.original.lines)
        lines[self.window[0] : self.window[1]] = new_window
        candidate = _Source(lines, self.original.newline)
        if self.path.suffix == ".py":
            try:
                compile(candidate.text(), self.rel_file, "exec")
            except SyntaxError as exc:
                return f"the result does not parse: {exc.msg} (line {exc.lineno})"
        try:
            candidate.write(self.path)
            failure = self.check()
        except Exception as exc:  # noqa: BLE001 — any check crash is a failed attempt
            failure = f"the check could not run: {exc}"
        if failure:
            self.original.write(self.path)  # restore — never leave a failed attempt behind
        return failure

    def _save(self, goal: str, artifact: Any) -> bool:
        return True  # _validate already left the validated candidate on disk


# ─────────────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────────────


def _ollama_caller(sizes: dict[str, int]) -> CallModel:
    from navig.agent.ollama_native import chat  # noqa: PLC0415

    def call(model: str, messages: list[dict[str, str]]) -> str:
        big = sizes.get(model, 0) > 12 * 2**30
        return chat(model, messages, timeout=1800.0 if big else 300.0).content

    return call


class _Sweep:
    def __init__(
        self,
        repo: Path,
        opts: SweepOptions,
        call_model: CallModel | None,
        progress: Progress | None,
    ):
        self.opts = opts
        self.progress = progress or (lambda _msg: None)
        top = _git(repo, "rev-parse", "--show-toplevel")
        if top.rc != 0:
            raise SweepError(f"{repo} is not inside a git repository")
        self.main_root = Path(top.out.strip())
        self.base = _git(self.main_root, "rev-parse", "HEAD").out.strip()
        if not self.base:
            raise SweepError("the repository has no commits to sweep")
        self.report = SweepReport(
            repo=str(self.main_root),
            base=self.base,
            started=datetime.now().isoformat(timespec="seconds"),
        )
        self.deadline = time.monotonic() + opts.max_minutes * 60 if opts.max_minutes else None
        self.root = self.main_root  # replaced by the worktree
        self.call_model = call_model
        self.fallback: str | None = opts.fallback_model
        self.ruff = ruff_command() if opts.lint else None
        self.python: list[str] | None = None
        self.env: dict[str, str] = {}
        self.sizes: dict[str, int] = {}
        self.last_run: PytestRun | None = None
        self._index_cache: dict[str, Any] = {}

    # ── preflight ────────────────────────────────────────────────────────────
    def preflight(self) -> None:
        o = self.opts
        if o.lint and not self.ruff:
            self.note("ruff is not installed — skipping lint (pip install ruff)")
            o.lint = False
        if self.call_model is None:
            from navig.agent.ollama_native import OllamaError, list_models  # noqa: PLC0415

            try:
                sizes = list_models()
            except OllamaError as exc:
                raise SweepError(str(exc)) from exc
            if o.model not in sizes:
                raise SweepError(f"model {o.model} is not installed — run: ollama pull {o.model}")
            if self.fallback and self.fallback not in sizes:
                self.note(
                    f"fallback {self.fallback} is not installed — no escalation (ollama pull {self.fallback})"
                )
                self.fallback = None
            self.sizes = sizes
            self.call_model = _ollama_caller(sizes)
        dirty = _git(self.main_root, "status", "--porcelain", "--untracked-files=no").out.strip()
        if dirty:
            self.note(
                "your uncommitted changes are NOT included — the sweep works on the committed HEAD"
            )

    def note(self, msg: str) -> None:
        self.report.notes.append(msg)
        self.progress(f"note: {msg}")

    def out_of_time(self) -> bool:
        return self.deadline is not None and time.monotonic() > self.deadline

    # ── worktree ────────────────────────────────────────────────────────────
    def open_worktree(self) -> None:
        from navig.selfheal.git_manager import create_worktree  # noqa: PLC0415

        probe = _git(self.main_root, "check-ignore", "-q", ".dev/worktrees/probe")
        if probe.rc != 0:
            exclude = Path(
                _git(self.main_root, "rev-parse", "--git-path", "info/exclude").out.strip()
            )
            if not exclude.is_absolute():
                exclude = self.main_root / exclude
            exclude.parent.mkdir(parents=True, exist_ok=True)
            with exclude.open("a", encoding="utf-8") as fh:
                fh.write("\n# navig evolve sweep worktrees\n.dev/\n")
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = self.main_root / ".dev" / "worktrees" / f"sweep-{stamp}"
        self.report.branch = create_worktree(self.main_root, dest, BRANCH_PREFIX, self.base)
        self.report.worktree = str(dest)
        self.root = dest
        self.progress(f"working in {dest} on {self.report.branch}")

    def close_worktree(self) -> None:
        from navig.selfheal.git_manager import remove_worktree  # noqa: PLC0415

        if not self.report.worktree:
            return
        commits = _git(
            self.main_root, "rev-list", "--count", f"{self.base}..{self.report.branch}"
        ).out.strip()
        keep_branch = not self.opts.dry_run and commits not in ("", "0")
        self.report.kept_branch = keep_branch
        if self.opts.keep_worktree:
            return
        leftover = remove_worktree(
            self.main_root, Path(self.report.worktree), None if keep_branch else self.report.branch
        )
        if leftover:
            self.note(
                f"could not delete {self.report.worktree} ({leftover}) — clear it with: navig repo prune"
            )
            return
        self.report.worktree = ""

    def commit(self, outcome: FixOutcome, paths: list[str], message: str) -> None:
        """Commit *paths* for *outcome*; a refused commit fails the item, not the run."""
        from navig.selfheal.git_manager import commit_paths  # noqa: PLC0415

        try:
            outcome.commit = commit_paths(self.root, paths, message)
            outcome.touched = list(paths)
            self._index_cache.clear()  # definitions below the change have moved
        except subprocess.CalledProcessError as exc:
            _git(self.root, "checkout", "--", *paths)
            detail = str(exc.stderr or exc.stdout or "").strip().splitlines()
            outcome.status = "failed"
            outcome.reason = "the fix passed its check but the commit was refused" + (
                f": {detail[-1][:160]}" if detail else ""
            )

    # ── tests ───────────────────────────────────────────────────────────────
    def setup_tests(self) -> bool:
        o = self.opts
        wanted = o.tests if o.tests is not None else has_tests(self.root)
        if not wanted:
            return False
        self.python = o.python or resolve_python(self.root, self.main_root)
        if not self.python:
            self.note("no Python interpreter found — skipping tests (pass --python)")
            return False
        probe = _run([*self.python, "-c", "import pytest"], self.root, timeout=60)
        if probe.rc != 0:
            self.note(f"pytest is not importable by {' '.join(self.python)} — skipping tests")
            return False
        # An editable install points imports at the MAIN checkout; put the worktree first so
        # the tests exercise the code being fixed.
        paths = [str(self.root)] + (
            [str(self.root / "src")] if (self.root / "src").is_dir() else []
        )
        self.env = {
            **os.environ,
            "PYTHONPATH": os.pathsep.join(paths + [os.environ.get("PYTHONPATH", "")]).rstrip(
                os.pathsep
            ),
            # A sweep runs an arbitrary repository in a detached, non-interactive child.
            # Do not let pytest discover plugins from NAVIG's host environment: an installed
            # interactive plugin can require a console and abort before writing the JUnit
            # report, while unrelated plugins can otherwise change collection semantics.
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "PYTHONIOENCODING": "utf-8",
            "PYTHONUTF8": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        return True

    def _py(self) -> list[str]:
        if self.python is None:
            raise SweepError("tests were not set up")
        return self.python

    def suite(self, label: str) -> PytestRun | None:
        self.progress(f"running the test suite ({label})…")
        run = run_pytest(self._py(), self.root, [], self.opts.test_timeout, self.env)
        if run.problem:
            self.note(f"test suite ({label}): {run.problem}")
            return None
        self.progress(f"  {len(run.passed)} passed · {len(run.failures)} failing")
        self.last_run = run
        return run

    def rerun(self, nodeid: str) -> PytestRun:
        return run_pytest(
            self._py(), self.root, [nodeid], min(self.opts.test_timeout, 600), self.env
        )

    def test_passes(self, nodeid: str, run: PytestRun | None = None) -> str | None:
        run = run or self.rerun(nodeid)
        if run.problem:
            return run.problem
        if nodeid in run.passed:
            return None
        if nodeid in run.failures:
            return "the test still fails:\n" + run.failures[nodeid].detail[-1500:]
        if nodeid in run.skipped:
            return "the test was skipped, not passed"
        # Usually the change broke IMPORTING the module, so the test was never collected.
        # Say why — "did not run" alone gives the model nothing to correct.
        other = next(iter(run.failures.values()), None)
        if other is not None:
            return "the test did not run — collecting it failed:\n" + other.detail[-1500:]
        return "the test did not run"

    def fresh_failure(self, diag: Diagnostic) -> Diagnostic | None:
        """Re-run *diag*'s test now: None when it passes, else a Diagnostic whose traceback
        reflects the CURRENT code.

        Tracebacks and definition lines captured by the baseline go stale as soon as an earlier
        fix lands in the same file — every line below it shifts, and a stale line number
        points the model at the wrong function.
        """
        run = self.rerun(diag.code)
        if self.test_passes(diag.code, run) is None:
            return None
        return run.failures.get(diag.code, diag)

    # ── gate ────────────────────────────────────────────────────────────────
    def gate(
        self, label: str, allowed: set[str], phase_start: str, phase: list[FixOutcome]
    ) -> set[str]:
        """Re-run the suite; drop this phase's commits that broke a previously passing test.

        Returns the failing set after the gate.
        """
        run = self.suite(f"gate after {label}")
        if run is None:
            return allowed
        new = set(run.failures) - allowed
        if not new:
            return set(run.failures)
        self.note(f"{len(new)} test(s) started failing after {label}: {', '.join(sorted(new)[:5])}")
        blamed_files = {f for nid in new for f, _ in _frames(run.failures[nid].detail, self.root)}
        committed = [o for o in phase if o.status == "fixed" and o.commit]
        culprits = [o for o in committed if blamed_files & set(o.touched)]
        for attempt, drop in enumerate((culprits, committed)):
            if not drop:
                continue
            self._rebuild(
                phase_start, committed, {o.commit for o in drop}, reason=f"broke {sorted(new)[0]}"
            )
            rerun = self.suite(f"gate after {label}, retry {attempt + 1}")
            if rerun is None:
                return allowed
            still = set(rerun.failures) - allowed
            if not still:
                return set(rerun.failures)
        self.note(f"{label}: could not isolate the regression; see the failing tests above")
        return set(run.failures)

    def _rebuild(
        self, start: str, committed: list[FixOutcome], drop: set[str], reason: str
    ) -> None:
        _git(self.root, "reset", "--hard", start)
        for o in committed:
            if o.commit in drop:
                o.status, o.reason, o.commit = "rolled_back", reason, ""
                continue
            pick = _git(self.root, "cherry-pick", o.commit)
            if pick.rc != 0:
                _git(self.root, "cherry-pick", "--abort")
                o.status, o.reason, o.commit = (
                    "rolled_back",
                    "no longer applied after a rollback",
                    "",
                )
            else:
                o.commit = _git(self.root, "rev-parse", "HEAD").out.strip()

    # ── fixing ──────────────────────────────────────────────────────────────
    def attempt(
        self, diag: Diagnostic, rel: str, line: int, model: str, test_fix: bool
    ) -> FixOutcome:
        started = time.monotonic()
        src = _Source.read(self.root / rel)
        if src is None:
            return FixOutcome(diag, "skipped", reason="not UTF-8 text", target_file=rel)
        if test_fix:
            window = pick_window(src, line, max_fn=120, pad=30)
            check = lambda: self.test_passes(diag.code)  # noqa: E731
            failure = "\n".join(diag.detail.strip().splitlines()[-40:])
            prompt = (
                f"Failing test: {diag.code}\n\nTest source:\n```python\n{_test_source(self.root, diag.code)}\n```\n\n"
                f"Failure:\n```\n{failure}\n```\n\n"
                f"Source snippet to fix — {rel}, lines {window[0] + 1}-{window[1]}:\n"
                f"```python\n" + "\n".join(src.lines[window[0] : window[1]]) + "\n```"
            )
            system = _TEST_SYSTEM
        else:
            window = pick_window(src, line, max_fn=60, pad=12)
            check = self._lint_check(rel, diag)
            offending = src.lines[line - 1].strip() if 0 < line <= len(src.lines) else ""
            prompt = (
                f"File: {rel}\nError {diag.code} on snippet line {line - window[0]} "
                f"(file line {line}): {diag.message}\nOffending line: {offending}\n\n"
                f"Snippet — lines {window[0] + 1}-{window[1]}:\n```python\n"
                + "\n".join(src.lines[window[0] : window[1]])
                + "\n```"
            )
            system = _LINT_SYSTEM
        if self.call_model is None:
            raise SweepError("no model caller configured")
        evolver = SnippetFixEvolver(
            root=self.root, rel_file=rel, window=window, model=model, call_model=self.call_model,
            check=check, user_prompt=prompt, system_prompt=system, test_fix=test_fix,
            max_retries=max(1, self.opts.retries),
        )  # fmt: skip
        result = evolver.evolve(diag.code)
        seconds = time.monotonic() - started
        if not result.success:
            return FixOutcome(
                diag,
                "failed",
                model,
                result.attempts,
                seconds,
                (result.error or "")[:300],
                target_file=rel,
            )
        return FixOutcome(diag, "fixed", model, result.attempts, seconds, target_file=rel)

    def _lint_check(self, rel: str, diag: Diagnostic) -> Callable[[], str | None]:
        ruff, root, select = self._ruff(), self.root, self.opts.lint_select
        before = Counter(d.code for d in ruff_diagnostics(ruff, root, [rel], select))

        def check() -> str | None:
            after_list = ruff_diagnostics(ruff, root, [rel], select)
            after = Counter(d.code for d in after_list)
            if after[diag.code] >= before[diag.code]:
                return f"ruff still reports {diag.code}: {diag.message}"
            added = [
                d for d in after_list if after[d.code] > before[d.code] and d.code != diag.code
            ]
            if added:
                d = added[0]
                return f"the change introduced {d.code} at line {d.line}: {d.message}"
            return None

        return check

    def _ruff(self) -> list[str]:
        if self.ruff is None:
            raise SweepError("ruff is not available")
        return self.ruff

    def lint_phase(self) -> list[FixOutcome]:
        ruff = self._ruff()
        outcomes: list[FixOutcome] = []
        if self.opts.autofix:
            self.progress("ruff --fix (safe fixes, no model)…")
            ruff_autofix(ruff, self.root, self.opts.lint_select)
            changed = [p for p in _git(self.root, "diff", "--name-only").out.splitlines() if p]
            if changed:
                self.report.autofix_files = len(changed)
                auto = FixOutcome(
                    Diagnostic("lint", f"{len(changed)} file(s)", 0, "ruff --fix", "safe autofixes"),
                    "fixed", "ruff", 1, 0.0,
                )  # fmt: skip
                self.commit(
                    auto, changed, f"style(sweep): ruff safe autofixes ({len(changed)} files)"
                )
                outcomes.append(auto)
        files = sorted(
            {d.file for d in ruff_diagnostics(ruff, self.root, ["."], self.opts.lint_select)}
        )
        budget = self.opts.limit
        for rel in files:
            tried: set[tuple[str, str, str]] = set()
            while budget > 0 and not self.out_of_time():
                src = _Source.read(self.root / rel)
                pending = [
                    d for d in ruff_diagnostics(ruff, self.root, [rel], self.opts.lint_select)
                    if (d.code, d.message, _line_text(src, d.line)) not in tried
                ]  # fmt: skip
                if not pending:
                    break
                diag = pending[0]
                tried.add((diag.code, diag.message, _line_text(src, diag.line)))
                budget -= 1
                n = self.opts.limit - budget
                self.progress(f"[lint {n}] {diag.code} {diag.where} — {diag.message[:70]}")
                outcome = self.attempt(diag, rel, diag.line, self.opts.model, test_fix=False)
                if outcome.status == "fixed":
                    self.commit(
                        outcome,
                        [rel],
                        f"fix(sweep): {diag.code} in {diag.where}\n\n{diag.message}\n\n"
                        f"Fixed by {outcome.model} in {outcome.attempts} attempt(s); validated with ruff.",
                    )
                self.progress(f"  → {_describe(outcome)}")
                outcomes.append(outcome)
            if budget <= 0 or self.out_of_time():
                if self.out_of_time():
                    self.note("time budget reached during lint")
                break
        return outcomes

    def test_phase(self, failing: dict[str, Diagnostic]) -> list[FixOutcome]:
        outcomes: list[FixOutcome] = []
        second_pass: list[Diagnostic] = []
        for nid, diag in list(failing.items())[: self.opts.limit]:
            if self.out_of_time():
                self.note("time budget reached during tests")
                break
            self.progress(f"[test] {nid} — {diag.message[:70]}")
            current = self.fresh_failure(diag)
            if current is None:
                if any(o.commit for o in outcomes):
                    outcomes.append(FixOutcome(diag, "fixed", reason="passes after an earlier fix"))
                    self.progress("  → passes after an earlier fix")
                else:
                    # Nothing has changed yet, so the code did not start working — the test
                    # only fails alongside others. That is not something to "fix" in source.
                    outcomes.append(
                        FixOutcome(
                            diag,
                            "skipped",
                            reason="passes when run on its own — flaky or order-dependent",
                        )
                    )
                    self.progress(
                        "  → skipped: passes when run on its own (flaky or order-dependent)"
                    )
                continue
            target = locate_test_target(current, self.root, self._index_cache)
            if isinstance(target, str):
                outcomes.append(FixOutcome(diag, "skipped", reason=target))
                self.progress(f"  → skipped: {target}")
                continue
            rel, line = target
            outcome = self.attempt(current, rel, line, self.opts.model, test_fix=True)
            outcome.diagnostic = diag
            if outcome.status == "fixed":
                self._commit_test_fix(diag, outcome)
            if outcome.status != "fixed":
                second_pass.append(diag)
            outcomes.append(outcome)
            self.progress(f"  → {_describe(outcome)}")
        if second_pass and self.fallback and self.sizes.get(self.fallback):
            # Checked NOW, not at start-up: an overnight run reaches this point hours later.
            shortfall = _ram_shortfall(self.sizes[self.fallback])
            if shortfall:
                self.note(f"escalation to {self.fallback} skipped: {shortfall}")
                second_pass = []
        if second_pass and self.fallback:
            self.progress(
                f"escalating {len(second_pass)} test(s) to {self.fallback} (loaded once)…"
            )
            for diag in second_pass:
                if self.out_of_time():
                    self.note("time budget reached during escalation")
                    break
                current = self.fresh_failure(diag)
                if current is None:
                    self._replace(
                        outcomes,
                        diag,
                        FixOutcome(diag, "fixed", reason="passes after an earlier fix"),
                    )
                    continue
                target = locate_test_target(current, self.root, self._index_cache)
                if isinstance(target, str):
                    continue  # the first pass located it; keep that outcome as it stands
                self.progress(f"[test · {self.fallback}] {diag.code}")
                outcome = self.attempt(current, target[0], target[1], self.fallback, test_fix=True)
                outcome.diagnostic = diag
                if outcome.status == "fixed":
                    self._commit_test_fix(diag, outcome)
                if outcome.status == "fixed":
                    self._replace(outcomes, diag, outcome)
                else:
                    prev = next(o for o in outcomes if o.diagnostic is diag)
                    outcome.reason = (
                        f"{self.opts.model} and {self.fallback} both failed: {outcome.reason}"
                    )
                    outcome.attempts += prev.attempts
                    outcome.seconds += prev.seconds
                    self._replace(outcomes, diag, outcome)
                self.progress(f"  → {_describe(outcome)}")
        return outcomes

    def _commit_test_fix(self, diag: Diagnostic, o: FixOutcome) -> None:
        self.commit(
            o,
            [o.target_file],
            f"fix(sweep): make {diag.code} pass\n\n{diag.message}\n\n"
            f"Changed {o.target_file}. Fixed by {o.model} in {o.attempts} attempt(s); "
            "validated by re-running the test.",
        )

    @staticmethod
    def _replace(outcomes: list[FixOutcome], diag: Diagnostic, new: FixOutcome) -> None:
        for i, o in enumerate(outcomes):
            if o.diagnostic is diag:
                outcomes[i] = new
                return

    # ── run ─────────────────────────────────────────────────────────────────
    def run(self) -> SweepReport:
        t0 = time.monotonic()
        self.preflight()
        self.open_worktree()
        try:
            tests = self.setup_tests()
            failing: set[str] = set()
            baseline: PytestRun | None = None
            if tests:
                baseline = self.suite("baseline")
                if baseline is None:
                    tests = False
                else:
                    failing = set(baseline.failures)
                    self.report.baseline_failures = sorted(failing)
            if self.opts.lint:
                start = _git(self.root, "rev-parse", "HEAD").out.strip()
                lint = self.lint_phase()
                self.report.outcomes += lint
                if tests and any(o.commit for o in lint):
                    failing = self.gate("lint fixes", failing, start, lint)
            if tests and failing and not self.out_of_time():
                current = (
                    self.last_run if self.last_run is not None else self.suite("before test fixes")
                )
                if current is not None and current.failures:
                    start = _git(self.root, "rev-parse", "HEAD").out.strip()
                    fixes = self.test_phase(current.failures)
                    self.report.outcomes += fixes
                    fixed = {o.diagnostic.code for o in fixes if o.status == "fixed"}
                    if any(o.commit for o in fixes):
                        failing = self.gate(
                            "test fixes", set(current.failures) - fixed, start, fixes
                        )
                    else:
                        failing = set(current.failures)
            self.report.final_failures = sorted(failing)
        finally:
            self.close_worktree()
            self.report.seconds = time.monotonic() - t0
        return self.report


def _line_text(src: _Source | None, line: int) -> str:
    if src is None or not 0 < line <= len(src.lines):
        return ""
    return src.lines[line - 1].strip()


def _describe(o: FixOutcome) -> str:
    if o.status == "fixed":
        return f"fixed by {o.model} ({o.attempts} attempt{'s' if o.attempts != 1 else ''}, {o.seconds:.0f}s)"
    if o.status == "skipped":
        return f"skipped: {o.reason}"
    return f"not fixed after {o.attempts} attempt(s), {o.seconds:.0f}s: {o.reason.splitlines()[0][:120] if o.reason else ''}"


def _ram_shortfall(model_bytes: int) -> str | None:
    """Why *model_bytes* will not fit in free RAM right now, or None.

    A model bigger than free memory still loads — Windows pages it to disk and every token then
    costs a disk read, so a run meant to take an hour takes all night and slows the machine.
    """
    try:
        import psutil  # noqa: PLC0415
    except ImportError:
        return None
    free = psutil.virtual_memory().available
    need = int(model_bytes * 1.15)  # weights + KV cache + runtime
    if free < need:
        return f"needs ~{need / 2**30:.1f} GB free RAM, {free / 2**30:.1f} GB available"
    return None


def save_report(report: SweepReport) -> Path | None:
    """Persist the report under navig's data dir (the branch is the change; this is the record)."""
    try:
        from navig.platform.paths import data_dir  # noqa: PLC0415

        folder = data_dir() / "sweeps"
        folder.mkdir(parents=True, exist_ok=True)
        name = f"{Path(report.repo).name}-{report.started.replace(':', '').replace('-', '')}.json"
        path = folder / name
        path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        report.report_path = str(path)
        return path
    except OSError:
        return None


def run_sweep(
    repo: Path,
    opts: SweepOptions | None = None,
    *,
    call_model: CallModel | None = None,
    progress: Progress | None = None,
) -> SweepReport:
    """Run one sweep over the git repository containing *repo*. See the module docstring."""
    return _Sweep(repo, opts or SweepOptions(), call_model, progress).run()
