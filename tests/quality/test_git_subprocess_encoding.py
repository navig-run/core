"""A text-mode subprocess that runs ``git`` must name ``encoding="utf-8"``.

One of three sibling guards over the same root cause — an implicit codec on this machine is
cp1251. This one covers a child process whose contract is UTF-8.
``test_text_encoding_discipline.py`` covers files we read and write ourselves.
``test_console_subprocess_encoding.py`` covers a Windows console tool, whose contract is the
CONSOLE code page — so its answer is the opposite one, and UTF-8 is an offence there.


``subprocess.run(cmd, text=True)`` decodes the child's stdout with
``locale.getpreferredencoding(False)``. That is UTF-8 on most Linux boxes and it is NOT on
Windows. git's own output encoding is UTF-8 by default (``i18n.logOutputEncoding``), so
every commit subject, author name, branch name and path comes back through the wrong codec.

Measured against this repository, not a fixture (2026-08-09, CPython 3.13, cp1251)::

    git log -1 --format=%s 4d1890135
    raw bytes          b'... clean main \\xe2\\x80\\x94 and the reason ...'   (UTF-8 em dash)
    text=True          '... clean main â€” and the reason ...'    <- WRONG, no exception
    text=True, utf-8   '... clean main — and the reason ...'      <- correct

There is no exception to notice: cp1251 maps almost every byte to some character, so the
call succeeds and returns corrupted text. That text then reaches changelog generation,
contributor analytics, diff summaries and the repo-guard briefing.

**Scope is git, deliberately.** 180 text-mode subprocess sites exist; only these have an
unambiguous UTF-8 contract. Eleven call Windows-native console tools (``tasklist``,
``netstat``, ``powershell``, ``sc``, ``cmd``) which emit the **OEM** code page. The rest
build their command dynamically and cannot be classified from source. Widening this guard
means classifying those first, not relaxing it.

⚠ Do NOT "finish the job" by adding ``encoding="utf-8"`` to the console-tool sites. Their
correct answer is the **console** code page, and it now has its own guard pointing the
opposite way — ``test_console_subprocess_encoding.py``, backed by
``navig.core.proc_text.console_encoding()``. Naming UTF-8 there is an offence in that guard.
The measurement (2026-08-10): ``chcp`` reports code page **866** while ``text=True`` decodes
with **cp1251**, and one filtered ``tasklist`` query returned 6710 bytes of which **85 were
non-ASCII**::

    cp1251  decodes OK        chrome.exe found: True
    cp866   decodes OK        chrome.exe found: True
    utf-8   RAISES UnicodeDecodeError at byte 150

Every one of those probes wraps its call in ``except Exception: return False``, so a strict
decode never surfaces as an error — it silently becomes *"the process is not running"*.
The three that mattered (``browser/targets.py``, navig-antivirus, navig-games) now compare
**bytes** and do not decode at all, which is why an ASCII needle needs no code page;
``tests/browser/test_process_probe_encoding.py`` and the two plugin siblings pin it.

``errors="replace"`` is required alongside, matching the house convention already used by
``builtin/tools/speedtest/worker.py``, ``commands/miniapp.py`` and navig-explore: a legacy
commit carrying an undeclared non-UTF-8 encoding degrades to U+FFFD instead of raising.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "navig"
REPO = CORE.parents[1]
PLUGINS = REPO / "plugins"

_CALLS = {"run", "check_output", "Popen", "call", "check_call"}
_SKIP_PARTS = {
    "__pycache__", ".lab", ".backup", ".dev", "node_modules", "site-packages",
    "scaffold-templates", ".archive", "build", "dist", "tests", "test",
}

# Keyed "<repo-relative>:<line>" -> reason. Ratcheted below; keep empty.
_ALLOWLIST: dict[str, str] = {}

# Pre-filter: does this file enable subprocess text mode at all? See _offenders().
_TEXT_MODE_HINT = re.compile(r"\btext\s*=\s*True\b|\buniversal_newlines\s*=")


def _scanned_roots() -> list[Path]:
    roots = [CORE, REPO / "core" / "tools", REPO / "scripts"]
    if PLUGINS.is_dir():
        roots += sorted(p for p in PLUGINS.glob("navig-*") if p.is_dir())
    harbor = REPO / "private" / "harbor"
    if harbor.is_dir():
        roots.append(harbor)
    return [r for r in roots if r.is_dir()]


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in _scanned_roots():
        for f in root.rglob("*.py"):
            if _SKIP_PARTS & set(f.relative_to(root).parts):
                continue
            files.append(f)
    return files


def _head_token(node: ast.AST) -> str | None:
    """The command word, when statically visible."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return re.split(r"\s", node.value.strip())[0]
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        head = node.elts[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            return head.value
    return None


def _is_git_token(token: str | None) -> bool:
    if not token:
        return False
    base = token.replace("\\", "/").split("/")[-1].lower()
    return base in ("git", "git.exe")


def _resolves_to_git_exe(node: ast.AST) -> bool:
    """An expression that yields the git executable itself.

    Covers the literal, and the lookup forms that wrap it —
    ``shutil.which("git")``, ``shutil.which("git") or "git"``,
    ``os.environ.get("GIT_EXE", "git")``. Missing this shape hid the single highest-value
    site in the class: ``agent/tools/git_tools.py::_run_git`` binds
    ``git_exe = shutil.which("git") or "git"`` and builds ``cmd = [git_exe, *args]``, so
    the head is a Name and a literal-only matcher walked straight past it — while EIGHT
    agent tools (status, diff, log, add, commit, stash) route through that one helper and
    hand its output to the model.

    Depth-bounded on purpose. Every one of these forms is shallow, while an unbounded
    recursion descends into every nested call in every assignment in the tree — measured,
    that alone took this guard from 30s to 56s in a step that runs on every push.
    """
    return _resolves_to_git_exe_at(node, 0)


def _resolves_to_git_exe_at(node: ast.AST, depth: int) -> bool:
    if depth > 2:
        return False
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _is_git_token(node.value)
    if isinstance(node, ast.BoolOp):                       # which("git") or "git"
        return any(_resolves_to_git_exe_at(v, depth + 1) for v in node.values)
    if isinstance(node, ast.Call):                         # which("git"), get("X", "git")
        return any(
            isinstance(a, (ast.Constant, ast.BoolOp)) and _resolves_to_git_exe_at(a, depth + 1)
            for a in node.args
        )
    return False


def _git_exe_names(scope: ast.AST, nodes: list[ast.AST] | None = None) -> set[str]:
    """Names bound in this scope to the git executable."""
    names: set[str] = set()
    for node in nodes if nodes is not None else ast.walk(scope):
        if isinstance(node, ast.Assign) and _resolves_to_git_exe(node.value):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return names


def _module_level_nodes(tree: ast.AST) -> list[ast.AST]:
    """Every node in the module EXCEPT those inside a function body.

    ``ast.walk(module)`` descends into every function, so a module scanned as one scope
    inherits the local names of every function in the file. That is not how Python scoping
    works and it produced false positives: a file where one function binds
    ``cmd = ["git", "log"]`` and an unrelated one binds ``cmd = ["curl", url]`` reported the
    *curl* call as a git site missing ``encoding="utf-8"`` — advice that makes no sense at
    the line it points to.
    """
    nodes: list[ast.AST] = []
    stack: list[ast.AST] = list(getattr(tree, "body", []))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue                       # its own scope handles it
        nodes.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return nodes


def _git_command_names(scope: ast.AST) -> tuple[set[str], set[str]]:
    """Names bound in this scope to a command list whose head is git.

    ``cmd = ["git", "log", "--format=%H|%s|%an|%ai"]`` then ``subprocess.run(cmd, …)`` is
    the dominant shape here, and it is the one reading commit SUBJECTS and AUTHOR NAMES —
    the fields most likely to be non-ASCII. An inline-only matcher misses exactly the
    calls that matter: it missed 6, including that one.

    The head may itself be a name (``cmd = [git_exe, *args]``), which is why
    ``_git_exe_names`` is consulted here too.

    Resolution is one scope deep and literal-only. No cross-function or cross-module
    inference; that would trade a checkable rule for a guess.
    """
    nodes = list(scope) if isinstance(scope, list) else list(ast.walk(scope))
    exe_names = _git_exe_names(scope, nodes)
    names: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.Assign):
            if _is_git_token(_head_token(node.value)):
                names.update(t.id for t in node.targets if isinstance(t, ast.Name))
                continue
            # cmd = [<git-exe name>, *args]
            value = node.value
            if isinstance(value, (ast.List, ast.Tuple)) and value.elts:
                head = value.elts[0]
                if isinstance(head, ast.Name) and head.id in exe_names:
                    names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            if _is_git_token(_head_token(node.value)) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
    return names, exe_names


def _scopes(tree: ast.AST):
    """``(nodes to scan, nodes that bind names)`` for each lexical scope.

    Module level first, then every function body. A function inherits module-level names —
    ``CMD = ["git", "log"]`` at module scope used by a call inside a function is a real
    shape — but NOT the locals of its siblings.
    """
    module_nodes = _module_level_nodes(tree)
    yield module_nodes, module_nodes
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            own = list(ast.walk(node))
            yield own, own + module_nodes


def _runs_git(call: ast.Call, git_names: set[str], exe_names: set[str]) -> bool:
    if not call.args:
        return False
    arg = call.args[0]
    if _is_git_token(_head_token(arg)):
        return True
    # An inline list whose head is the git executable bound to a name:
    # `subprocess.run([git_exe, *args], text=True)`.
    if isinstance(arg, (ast.List, ast.Tuple)) and arg.elts:
        head = arg.elts[0]
        if isinstance(head, ast.Name) and head.id in exe_names:
            return True
    return isinstance(arg, ast.Name) and arg.id in git_names


def _is_text_mode(call: ast.Call) -> bool:
    for keyword in call.keywords:
        if keyword.arg in ("text", "universal_newlines"):
            if isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                return True
    return False


def _kwarg_names(call: ast.Call) -> set[str]:
    return {k.arg for k in call.keywords if k.arg}


def _offending(call: ast.Call, git_names: set[str], exe_names: set[str]) -> str | None:
    """The missing-kwarg label, or None when this call is fine."""
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr not in _CALLS:
        return None
    if getattr(func.value, "id", None) not in ("subprocess", "sp"):
        return None
    if any(k.arg is None for k in call.keywords):
        return None                       # **kwargs may carry it; cannot prove absence
    if not _is_text_mode(call) or not _runs_git(call, git_names, exe_names):
        return None
    names = _kwarg_names(call)
    if "encoding" not in names:
        return 'missing encoding="utf-8"'
    if "errors" not in names:
        return 'missing errors="replace"'
    return None


def _offenders_in(tree: ast.AST) -> dict[int, str]:
    """{lineno: reason}, walking each scope with its own resolved git names."""
    found: dict[int, str] = {}
    for scan_nodes, binding_nodes in _scopes(tree):
        git_names, exe_names = _git_command_names(binding_nodes)
        for node in scan_nodes:
            if isinstance(node, ast.Call) and (why := _offending(node, git_names, exe_names)):
                found[node.lineno] = why
    return found


def _offenders() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in _python_files():
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        # Cheap pre-filter before parsing: a file that never enables text mode cannot hold
        # an offender, so there is nothing to gain from building its AST. Measured, this
        # takes the whole guard from 39s to 10s — below even the 30s it cost before it
        # learned to resolve names — on a step that runs on every push.
        #
        # Whitespace-tolerant deliberately. `subprocess.run(cmd, text = True)` is legal
        # Python and a plain `"text=True" in source` would skip that file silently, which
        # is the one way a pre-filter can turn into a false negative. Verified against the
        # AST when this landed: zero files are text-mode-true yet invisible to this regex.
        if not _TEXT_MODE_HINT.search(source):
            continue
        try:
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = path.relative_to(REPO).as_posix()
        for lineno, why in _offenders_in(tree).items():
            found[f"{rel}:{lineno}"] = why
    return found


def test_git_subprocess_names_its_encoding() -> None:
    offenders = {k: v for k, v in _offenders().items() if k not in _ALLOWLIST}
    assert not offenders, (
        "A text-mode subprocess running git decodes with the locale code page, so every "
        "non-ASCII character in a commit subject, author name or path comes back wrong — "
        "silently, because cp1251 maps almost every byte:\n  "
        + "\n  ".join(f"{site}  ({why})" for site, why in sorted(offenders.items()))
        + '\n\nAdd encoding="utf-8", errors="replace".'
    )


def test_allowlist_entries_still_match() -> None:
    stale = sorted(set(_ALLOWLIST) - set(_offenders()))
    assert not stale, (
        f"_ALLOWLIST names sites that no longer offend: {stale}. Delete them, or the "
        "exemption will cover a future offender at the same line."
    )


def test_the_scan_actually_reads_the_tree() -> None:
    """A scope that resolves to nothing passes every assertion above."""
    roots = [r.name for r in _scanned_roots()]
    assert any(r.startswith("navig-") for r in roots), (
        f"no plugin package in scope — navig-github holds most git calls: {roots}"
    )
    assert len(_python_files()) > 500, "roots moved; this guard is checking almost nothing"


def test_the_detector_matches_the_shapes_it_claims_to() -> None:
    """Both directions on synthetic source. The negatives carry the design decisions."""
    def why(src: str) -> list[str]:
        return list(_offenders_in(ast.parse(src)).values())

    assert why('subprocess.run(["git", "log"], text=True)')
    assert why('subprocess.check_output("git status", universal_newlines=True)')
    assert why('subprocess.run(["git"], text=True, encoding="utf-8")') == [
        'missing errors="replace"'
    ]

    ok = 'subprocess.run(["git", "log"], text=True, encoding="utf-8", errors="replace")'
    assert why(ok) == []
    # Byte mode: bytes come back undecoded and `.decode()` defaults to utf-8 — correct.
    assert why('subprocess.run(["git", "log"], capture_output=True)') == []
    # A Windows OEM tool is deliberately OUT of scope; forcing utf-8 there can raise.
    assert why('subprocess.run(["tasklist"], text=True)') == []
    assert why('subprocess.run(["powershell", "-Command", "x"], text=True)') == []
    # A command bound to a local IS resolved — this is the shape that reads commit
    # subjects, and an inline-only matcher missed six of them.
    assert why(
        'def f():\n'
        '    cmd = ["git", "log", "--format=%H|%s|%an"]\n'
        '    subprocess.run(cmd, text=True)\n'
    ) == ['missing encoding="utf-8"']
    assert why(
        'def f():\n'
        '    cmd = ["git", "log"]\n'
        '    subprocess.run(cmd, text=True, encoding="utf-8", errors="replace")\n'
    ) == []
    # The git EXECUTABLE may itself be a name. This is the shape that hid
    # agent/tools/git_tools.py::_run_git — eight agent tools behind one helper.
    assert why(
        'def f():\n'
        '    git_exe = shutil.which("git") or "git"\n'
        '    cmd = [git_exe, *args]\n'
        '    subprocess.run(cmd, text=True)\n'
    ) == ['missing encoding="utf-8"']
    # ...and inline, without the intermediate `cmd`.
    assert why(
        'def f():\n'
        '    git_exe = shutil.which("git")\n'
        '    subprocess.run([git_exe, "log"], text=True)\n'
    ) == ['missing encoding="utf-8"']
    assert why(
        'def f():\n'
        '    git_exe = shutil.which("git") or "git"\n'
        '    subprocess.run([git_exe, "log"], text=True, encoding="utf-8", errors="replace")\n'
    ) == []
    # A different executable resolved the same way must NOT be dragged in.
    assert why(
        'def f():\n'
        '    exe = shutil.which("docker") or "docker"\n'
        '    subprocess.run([exe, "ps"], text=True)\n'
    ) == []
    # ...but a name bound to something else, or to nothing visible, is not guessed at.
    assert why("subprocess.run(cmd, text=True)") == []
    assert why(
        'def f():\n'
        '    cmd = ["tasklist"]\n'
        '    subprocess.run(cmd, text=True)\n'
    ) == []
    # asyncio spawns are bytes-only and are not subprocess.* calls at all.
    assert why('asyncio.create_subprocess_exec("git", "log")') == []
