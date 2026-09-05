"""A text-mode subprocess running a Windows console tool must use ``console_encoding()``.

One of three sibling guards over the same root cause — an implicit codec on this machine is
cp1251. This one covers a Windows console tool, whose contract is the CONSOLE code page.
``test_git_subprocess_encoding.py`` covers a child whose contract IS UTF-8, so the two point
in opposite directions on purpose. ``test_text_encoding_discipline.py`` covers files we read
and write ourselves.


Windows console tools (``tasklist``, ``icacls``, ``whoami``, ``sc``, ``schtasks``,
``netstat``, ``cmd``, ``powershell`` …) write the **console output** code page. Python's
``text=True`` decodes with ``locale.getpreferredencoding(False)`` — the **ANSI** one. On a
stock Russian-locale Windows those are two different encodings and neither is UTF-8::

    GetACP() = 1251     GetOEMCP() = GetConsoleOutputCP() = 866

Measured against a real ``whoami /groups`` on this machine (2026-08-10, CPython 3.13)::

    raw          b'BUILTIN\\\\\\x80\\xa4\\xac\\xa8\\xad\\xa8\\xe1\\xe2\\xe0\\xa0\\xe2\\xae\\xe0\\xeb'
    oem          the real localized group name       <- correct
    text=True    14 wrong characters, NO exception   <- what the tree did
    utf-8        UnicodeDecodeError at byte 1000     <- what "just add utf-8" produces

This guard is the MIRROR of ``test_git_subprocess_encoding.py`` and the two deliberately
point in opposite directions: git's output contract is UTF-8, a Windows console tool's is
the console code page. Naming ``encoding="utf-8"`` here is an offence, not a fix — which is
why the label says so explicitly rather than just "missing encoding".

⚠ Forcing UTF-8 does not fail the way it looks like it will. Measured under ``PYTHONUTF8=1``
(which the documented pytest invocation sets, and which PEP 686 may make the default):
``subprocess.run(["icacls", …], text=True)`` **returns normally with stdout=None** and the
``UnicodeDecodeError`` surfaces on a background reader thread nobody is watching. Callers
then hit ``AttributeError: 'NoneType'`` far from the cause, or — where the result is only
tested for truth — silently conclude "not found".

**When output is captured but never read, take no text mode at all.** Not decoding has no
failure mode; that is what ``core/file_permissions.py`` and ``commands/doctor.py`` do. And
when the only question is whether an ASCII needle appears, compare bytes
(``browser/targets.py``) — those sites are correct and this guard leaves them alone.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "navig"
REPO = CORE.parents[1]

_CALLS = {"run", "check_output", "Popen", "call", "check_call"}
_SKIP_PARTS = {
    "__pycache__", ".lab", ".backup", ".dev", "node_modules", "site-packages",
    "scaffold-templates", ".archive", "build", "dist", "tests", "test", ".venv",
}

# Windows-native console tools. Each writes the console code page, never UTF-8 by contract.
# `pwsh` is deliberately absent: PowerShell 7+ defaults its output to UTF-8, so it is not a
# member of this class and forcing the console page on it would be the same bug reversed.
_CONSOLE_TOOLS = frozenset({
    "arp", "assoc", "attrib", "bcdedit", "cacls", "chcp", "cmd", "diskpart", "dism",
    "driverquery", "findstr", "fsutil", "ftype", "getmac", "gpresult", "hostname",
    "icacls", "ipconfig", "net", "netsh", "netstat", "nslookup", "openfiles", "ping",
    "powershell", "qwinsta", "query", "reagentc", "reg", "route", "sc", "schtasks",
    "sfc", "systeminfo", "taskkill", "tasklist", "tracert", "wevtutil", "where",
    "whoami", "wmic",
})

# {site: why it is exempt}. An entry must say what makes the site safe, not that it is old.
_ALLOWLIST: dict[str, str] = {}

# Cheap pre-filter: a file that never names a console tool cannot hold an offender, and a
# file that never decodes cannot either. Both must match before the AST is built — the same
# technique that took the sibling git guard from 39s to 10s on a step that runs every push.
_TOOL_HINT = re.compile(r"[\"']\s*(" + "|".join(sorted(_CONSOLE_TOOLS)) + r")(\.exe)?\s*[\"']")
_DECODE_HINT = re.compile(r"\btext\s*=|\buniversal_newlines\s*=|\bencoding\s*=")


def _scanned_roots() -> list[Path]:
    roots = [CORE, REPO / "core" / "tools", REPO / "plugins", REPO / "private"]
    return [r for r in roots if r.exists()]


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in _scanned_roots():
        for f in root.rglob("*.py"):
            # relative_to(root), never the absolute path: this worktree lives under
            # `.dev/worktrees/`, and matching `.dev` against absolute parts skips the
            # ENTIRE tree while reporting a clean pass. That has now happened three times
            # in this repo; the floor assertion below is what catches it.
            if _SKIP_PARTS & set(f.relative_to(root).parts):
                continue
            files.append(f)
    return files


def _tool_of(token: str | None) -> str | None:
    """The console tool a command word names, if it is one."""
    if not token:
        return None
    base = token.replace("\\", "/").split("/")[-1].lower()
    if base.endswith(".exe"):
        base = base[:-4]
    return base if base in _CONSOLE_TOOLS else None


def _head_token(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return re.split(r"\s", node.value.strip())[0]
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        head = node.elts[0]
        if isinstance(head, ast.Constant) and isinstance(head.value, str):
            return head.value
    return None


def _module_level_nodes(tree: ast.AST) -> list[ast.AST]:
    """Every node in the module EXCEPT those inside a function body.

    ``ast.walk(module)`` descends into functions, so treating the module as one scope makes
    every function's locals visible to every other function. That is not Python scoping and
    it is a live false-positive source: while writing this guard it reported
    ``navig-msstore``'s ``subprocess.run(argv, …)`` — where ``argv`` is a *navig CLI* call —
    as a PowerShell site, because a different function 190 lines away binds its own ``argv``
    to a PowerShell command.
    """
    nodes: list[ast.AST] = []
    stack: list[ast.AST] = list(getattr(tree, "body", []))
    while stack:
        node = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        nodes.append(node)
        stack.extend(ast.iter_child_nodes(node))
    return nodes


def _scopes(tree: ast.AST):
    """``(nodes to scan, nodes that bind names)`` per lexical scope.

    A function inherits module-level names — ``CMD = ["tasklist", "/FO", "CSV"]`` at module
    scope used inside a function is a real shape — but not its siblings' locals.
    """
    module_nodes = _module_level_nodes(tree)
    yield module_nodes, module_nodes
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            own = list(ast.walk(node))
            yield own, own + module_nodes


def _command_names(nodes: list[ast.AST]) -> dict[str, str]:
    """{local name: tool} for names bound to a command list headed by a console tool.

    ``cmd = ["powershell", "-NoProfile", "-Command", script]`` then
    ``subprocess.run(cmd, text=True)`` is the dominant shape in this tree — an inline-only
    matcher would miss most of the class. Resolution is one scope deep and literal-only; a
    head that is itself a variable is not guessed at.
    """
    names: dict[str, str] = {}
    for node in nodes:
        if isinstance(node, ast.Assign):
            tool = _tool_of(_head_token(node.value))
            if tool:
                names.update(
                    {t.id: tool for t in node.targets if isinstance(t, ast.Name)}
                )
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            tool = _tool_of(_head_token(node.value))
            if tool and isinstance(node.target, ast.Name):
                names[node.target.id] = tool
    return names


def _runs_console_tool(call: ast.Call, names: dict[str, str]) -> str | None:
    if not call.args:
        return None
    arg = call.args[0]
    tool = _tool_of(_head_token(arg))
    if tool:
        return tool
    if isinstance(arg, ast.Name):
        return names.get(arg.id)
    return None


def _is_console_encoding_call(node: ast.AST) -> bool:
    """``console_encoding()`` — bare, or reached through a module attribute."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Name):
        return func.id == "console_encoding"
    return isinstance(func, ast.Attribute) and func.attr == "console_encoding"


def _decodes_text(call: ast.Call) -> bool:
    for keyword in call.keywords:
        if keyword.arg in ("text", "universal_newlines"):
            if isinstance(keyword.value, ast.Constant) and keyword.value.value is True:
                return True
        if keyword.arg == "encoding":
            return True
    return False


def _kwarg(call: ast.Call, name: str) -> ast.AST | None:
    for keyword in call.keywords:
        if keyword.arg == name:
            return keyword.value
    return None


def _offending(call: ast.Call, names: dict[str, str]) -> str | None:
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr not in _CALLS:
        return None
    if getattr(func.value, "id", None) not in ("subprocess", "sp"):
        return None
    if any(k.arg is None for k in call.keywords):
        return None                        # **kwargs may carry it; cannot prove absence
    tool = _runs_console_tool(call, names)
    if tool is None or not _decodes_text(call):
        return None

    encoding = _kwarg(call, "encoding")
    if encoding is None:
        return (
            f"`{tool}` writes the console code page, but text mode decodes with the ANSI "
            f"one. Fix by naming encoding=console_encoding(), errors=\"replace\"; or, if "
            f"this runs a command the USER chose, capture bytes and decode with "
            f"proc_text.decode_console_result (no codec is right for both a UTF-8 tool and "
            f"a console tool); or drop text mode if the output is never read"
        )
    if not _is_console_encoding_call(encoding):
        literal = ast.unparse(encoding)
        return (
            f"`{tool}` writes the console code page, not {literal}; use "
            f"encoding=console_encoding() (navig.core.proc_text)"
        )
    if _kwarg(call, "errors") is None:
        return f"`{tool}`: missing errors=\"replace\" alongside console_encoding()"
    return None


def _offenders_in(tree: ast.AST) -> dict[int, str]:
    found: dict[int, str] = {}
    for scan_nodes, binding_nodes in _scopes(tree):
        names = _command_names(binding_nodes)
        for node in scan_nodes:
            if isinstance(node, ast.Call) and (why := _offending(node, names)):
                found[node.lineno] = why
    return found


def _offenders() -> tuple[dict[str, str], int]:
    """({site: reason}, files actually parsed)."""
    found: dict[str, str] = {}
    parsed = 0
    for path in _python_files():
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not (_TOOL_HINT.search(source) and _DECODE_HINT.search(source)):
            continue
        try:
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            continue
        parsed += 1
        rel = path.relative_to(REPO).as_posix()
        for lineno, why in _offenders_in(tree).items():
            found[f"{rel}:{lineno}"] = why
    return found, parsed


def test_console_subprocess_uses_the_console_encoding() -> None:
    offenders, _ = _offenders()
    offenders = {k: v for k, v in offenders.items() if k not in _ALLOWLIST}
    assert not offenders, (
        "Windows console output decoded with the wrong code page:\n"
        + "\n".join(f"  {site}\n      {why}" for site, why in sorted(offenders.items()))
        + "\n\nUse: from navig.core.proc_text import console_encoding"
    )


def test_allowlist_entries_still_match() -> None:
    offenders, _ = _offenders()
    stale = sorted(set(_ALLOWLIST) - set(offenders))
    assert not stale, (
        f"_ALLOWLIST names sites that no longer offend: {stale}. Delete them, or the "
        "next real defect at those lines is exempt by accident."
    )


def test_the_scan_actually_reads_the_tree() -> None:
    """Anti-vacuity. A scan that silently reads nothing looks exactly like a clean pass.

    The floors are measured, not round. On 2026-08-10 the scanner reached 1605 python
    files and the pre-filter left 24 of them to parse; the assertions sit just below both,
    so real growth never trips them but a collapsed scan does.
    """
    files = _python_files()
    assert len(files) > 1400, f"only {len(files)} python files reached the scanner"
    assert any(f.name == "connection.py" for f in files)

    # Per ROOT, not just in total: this guard claims to scan plugins and private, and a
    # total-only floor stays green while one root silently contributes nothing. Measured
    # 2026-08-10: core/navig 1287 · plugins 268 · private 41 · core/tools 9.
    reached = {root.name: sum(1 for f in files if root in f.parents) for root in _scanned_roots()}
    for root, count in reached.items():
        assert count > 0, f"the {root!r} root contributed no files: {reached}"
    assert reached.get("plugins", 0) > 150, reached

    _, parsed = _offenders()
    assert parsed >= 18, f"pre-filter left only {parsed} files to parse"


def test_the_detector_matches_the_shapes_it_claims_to() -> None:
    def why(src: str) -> list[str]:
        return list(_offenders_in(ast.parse(src)).values())

    # --- caught -------------------------------------------------------------------
    assert why('import subprocess\nsubprocess.run(["tasklist"], text=True)')
    assert why('import subprocess\nsubprocess.run("whoami /groups", text=True)')
    assert why('import subprocess\nsubprocess.check_output(["netstat", "-ano"], text=True)')
    assert why(
        "import subprocess\n"
        "def f():\n"
        '    cmd = ["powershell", "-Command", "x"]\n'
        "    subprocess.run(cmd, text=True)\n"
    ), "a command list bound to a local is the dominant shape and must be resolved"
    assert why(
        'import subprocess\nsubprocess.run(["icacls", p], encoding="utf-8", errors="replace")'
    ), "utf-8 is an offence here, not a fix — that is the whole point of this guard"
    assert why(
        "import subprocess\nsubprocess.run(['sc', 'query'], encoding=console_encoding())"
    ), 'errors="replace" is required alongside'

    # --- not caught ---------------------------------------------------------------
    assert not why(
        "import subprocess\n"
        'subprocess.run(["tasklist"], encoding=console_encoding(), errors="replace")'
    )
    assert not why('import subprocess\nsubprocess.run(["tasklist"], capture_output=True)'), (
        "bytes mode is the recommended fix when the output is never decoded"
    )
    assert not why('import subprocess\nsubprocess.run(["git", "log"], text=True)'), (
        "git is the sibling guard's business and its answer is the opposite one"
    )
    assert not why('import subprocess\nsubprocess.run(["pwsh", "-c", "x"], text=True)'), (
        "PowerShell 7+ writes UTF-8; it is not a member of this class"
    )
    assert not why("import subprocess\nsubprocess.run(cmd, text=True)"), (
        "an unresolvable head is not guessed at"
    )
    assert not why(
        "import subprocess\n"
        "def a():\n"
        '    cmd = ["tasklist"]\n'
        "    subprocess.run(cmd, text=True)\n"
        "def b():\n"
        "    cmd = [other, url]\n"
        "    subprocess.run(cmd, text=True)\n"
    )[1:], "a sibling function's local must not leak into this scope"


def test_module_level_command_is_visible_inside_a_function() -> None:
    src = (
        "import subprocess\n"
        'CMD = ["tasklist", "/FO", "CSV"]\n'
        "def f():\n"
        "    return subprocess.run(CMD, text=True)\n"
    )
    assert _offenders_in(ast.parse(src)), "module-level command names must still resolve"


def test_the_prefilter_cannot_hide_an_offender() -> None:
    """The pre-filter is the one place this guard could silently stop working."""
    for src in (
        'subprocess.run(["tasklist"], text = True)',
        "subprocess.run(['tasklist'], universal_newlines=True)",
        'subprocess.run(["tasklist"], encoding="utf-8")',
    ):
        assert _TOOL_HINT.search(src) and _DECODE_HINT.search(src), src
