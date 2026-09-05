"""A subprocess whose command is not statically known must CHOOSE how to decode it.

The sibling guards handle the two cases where the contract is knowable:
``test_git_subprocess_encoding.py`` (git is UTF-8) and
``test_console_subprocess_encoding.py`` (a Windows console tool is the console code page).
This one covers the rest — a command assembled at run time, where neither guard can say
what the child writes.

For those, ``text=True`` is never a decision, it is a default: it decodes with
``locale.getpreferredencoding(False)``, the **ANSI** code page on Windows, which is right
for essentially nothing. Measured on this machine, the three plausible children disagree::

    git, node, most cross-platform CLIs   ->  UTF-8
    tasklist / icacls / powershell        ->  console code page (866 here)
    a Python child on a redirected pipe   ->  ANSI (1251 here)

So there is no single codec this guard can demand. What it demands is that the choice be
made: name an ``encoding=``, or capture bytes and hand them to
``navig.core.proc_text.decode_console_result`` (UTF-8 strictly, then the console page),
which is the right answer when the command really is unknown — that is what the local
command executors and the ``desktop_powershell`` MCP tool use.

⚠ Scope is deliberately "captures AND decodes". A call with no ``capture_output``/``stdout``
streams straight to the terminal and decodes nothing, so text mode there is inert; flagging
it would be noise. Measured at the time of writing: 2 such sites.

The allowlist is PRE-EXISTING debt, counted rather than hidden. Sweeping all of it blindly
would be the speculative rewrite this rule exists to prevent — the correct codec differs per
call site and several of these run a Python child, where the ANSI default is actually right.
The point of the guard is that the list can only shrink.
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
_DECODE_HINT = re.compile(r"\btext\s*=|\buniversal_newlines\s*=|\bencoding\s*=")


def _scanned_roots() -> list[Path]:
    roots = [CORE, REPO / "core" / "tools", REPO / "plugins", REPO / "private"]
    return [r for r in roots if r.exists()]


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in _scanned_roots():
        for f in root.rglob("*.py"):
            # relative_to(root), never the absolute path — every agent works under
            # `.dev/worktrees/`, and matching `.dev` on absolute parts skips the whole tree
            # while reporting a clean pass. That has happened three times in this repo.
            if _SKIP_PARTS & set(f.relative_to(root).parts):
                continue
            files.append(f)
    return files


def _head_is_static(node: ast.AST) -> bool:
    """True when the command word is visible in the source at this call."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return True
    if isinstance(node, (ast.List, ast.Tuple)) and node.elts:
        head = node.elts[0]
        return isinstance(head, ast.Constant) and isinstance(head.value, str)
    return False


def _decodes_and_captures(call: ast.Call) -> bool:
    kw = {k.arg for k in call.keywords if k.arg}
    text_mode = any(
        k.arg in ("text", "universal_newlines")
        and isinstance(k.value, ast.Constant)
        and k.value.value is True
        for k in call.keywords
    )
    if not (text_mode or "encoding" in kw):
        return False
    return bool({"capture_output", "stdout"} & kw)


def _offending(call: ast.Call) -> bool:
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr not in _CALLS:
        return False
    if getattr(func.value, "id", None) not in ("subprocess", "sp"):
        return False
    if any(k.arg is None for k in call.keywords):
        return False  # **kwargs may carry it; cannot prove absence
    if not call.args or _head_is_static(call.args[0]):
        return False  # a sibling guard's business, or knowable
    if not _decodes_and_captures(call):
        return False
    # An explicit `encoding=` IS the decision this guard asks for, whatever its value.
    return not any(k.arg == "encoding" for k in call.keywords)


def _offenders() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in _python_files():
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not _DECODE_HINT.search(source):
            continue
        try:
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = path.relative_to(REPO).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _offending(node):
                found[f"{rel}:{node.lineno}"] = "text=True on a run-time command"
    return found


def _load_allowlist() -> set[str]:
    """Pre-existing sites, kept as data next to this file so the diff of a fix is one line."""
    path = Path(__file__).with_name("dynamic_subprocess_allowlist.txt")
    if not path.exists():
        return set()
    out = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.add(line)
    return out


def test_a_runtime_command_names_its_encoding() -> None:
    offenders = set(_offenders()) - _load_allowlist()
    assert not offenders, (
        "a subprocess with a run-time command decodes with the locale code page:\n"
        + "\n".join(f"  {s}" for s in sorted(offenders))
        + "\n\nName the codec the child actually writes, or capture bytes and use\n"
        "navig.core.proc_text.decode_console_result when the command is genuinely unknown."
    )


def test_the_allowlist_only_shrinks() -> None:
    """Every allowlisted site must still exist and still offend.

    A stale entry is worse than no entry: it silently exempts whatever ends up at that line
    later. This is what turns the list into a debt that can only go down.
    """
    current = set(_offenders())
    stale = sorted(_load_allowlist() - current)
    assert not stale, (
        f"{len(stale)} allowlist entries no longer offend — delete them:\n"
        + "\n".join(f"  {s}" for s in stale)
    )


def test_the_scan_actually_reads_the_tree() -> None:
    """Anti-vacuity: a scan that silently reads nothing looks exactly like a clean pass."""
    files = _python_files()
    assert len(files) > 1400, f"only {len(files)} python files reached the scanner"
    assert any(f.name == "connection.py" for f in files)
    # 10, measured against 15. This started at 61 and was 40 when the register was pure
    # debt; the sites have since been given the codec their child writes, so the number
    # legitimately came down. It is a floor against a SILENT collapse (a scanner that
    # stops matching reports zero offenders and an empty list looks like success), not a
    # target — lower it again the same way, by fixing sites and re-measuring.
    assert len(_load_allowlist()) >= 10, (
        "the allowlist collapsed — either the scanner stopped seeing the tree or someone "
        "emptied it without fixing the sites"
    )


def test_the_detector_matches_the_shapes_it_claims_to() -> None:
    def offends(src: str) -> bool:
        return any(
            isinstance(n, ast.Call) and _offending(n) for n in ast.walk(ast.parse(src))
        )

    # --- caught -------------------------------------------------------------------
    assert offends("import subprocess\nsubprocess.run(cmd, capture_output=True, text=True)")
    assert offends(
        "import subprocess\nsubprocess.run(argv, stdout=subprocess.PIPE, text=True)"
    )

    # --- not caught ---------------------------------------------------------------
    assert not offends(
        "import subprocess\n"
        "subprocess.run(cmd, capture_output=True, encoding='utf-8', errors='replace')"
    ), "an explicit encoding IS the decision this guard asks for"
    assert not offends("import subprocess\nsubprocess.run(cmd, text=True)"), (
        "no capture: it streams to the terminal and decodes nothing"
    )
    assert not offends("import subprocess\nsubprocess.run(cmd, capture_output=True)"), (
        "bytes mode is the recommended fix, not an offence"
    )
    assert not offends(
        'import subprocess\nsubprocess.run(["git", "log"], capture_output=True, text=True)'
    ), "a statically known head belongs to a sibling guard"
    assert not offends("import subprocess\nsubprocess.run(cmd, **kw)"), (
        "**kwargs may carry the encoding; absence cannot be proven"
    )
