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

⚠ Entries are keyed by ENCLOSING FUNCTION with a count — ``path::qualname x2`` — never by
line. A line key moves with every unrelated edit above it: shortening a docstring near line
79 of ``commands/skills.py`` moved its allowlisted call from 504 to 501 and this guard
failed twice at once (an "unknown" offender at 501, a "stale" entry at 504) for a change
that touched nothing about the call. A line key also exempts whatever unrelated code later
drifts INTO that line. The function survives both. The count is what makes the key honest:
a second offending call added to an already-allowlisted function is a NEW offence, which
a membership check would wave through.
"""

from __future__ import annotations

import ast
import re
from collections import Counter
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


def _offending_keys(tree: ast.AST, rel: str) -> Counter[str]:
    """``path::qualname`` for every offending call, counted per enclosing function.

    A stack walk rather than ``ast.walk``, because the key IS the enclosing scope:
    ``outer.inner`` for a nested def, ``Class.method`` for a method, ``<module>`` at top
    level. Nothing about a line number survives an edit above the call; the function does.
    """
    found: Counter[str] = Counter()

    def visit(node: ast.AST, stack: tuple[str, ...]) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            stack = (*stack, node.name)
        if isinstance(node, ast.Call) and _offending(node):
            found[f"{rel}::{'.'.join(stack) or '<module>'}"] += 1
        for child in ast.iter_child_nodes(node):
            visit(child, stack)

    visit(tree, ())
    return found


def _offenders() -> Counter[str]:
    found: Counter[str] = Counter()
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
        found.update(_offending_keys(tree, path.relative_to(REPO).as_posix()))
    return found


_ENTRY = re.compile(r"^(?P<key>\S+::\S+)(?:\s+x(?P<n>\d+))?$")


def _load_allowlist() -> Counter[str]:
    """Pre-existing sites, kept as data next to this file so the diff of a fix is one line.

    One entry per line: ``path::qualname``, with ``x<N>`` when that function holds more
    than one allowlisted call. A line that does not parse is an error, not a silent skip —
    a malformed entry that vanished would read as "fixed".
    """
    path = Path(__file__).with_name("dynamic_subprocess_allowlist.txt")
    if not path.exists():
        return Counter()
    out: Counter[str] = Counter()
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        m = _ENTRY.match(line)
        assert m, f"unparseable allowlist entry: {raw!r} (expected `path::qualname [xN]`)"
        out[m["key"]] += int(m["n"] or 1)
    return out


def test_a_runtime_command_names_its_encoding() -> None:
    offenders, allowed = _offenders(), _load_allowlist()
    # Counts, not membership: a second offending call in an already-allowlisted function
    # is a NEW offence, and a set difference would wave it through.
    new = {k: (n, allowed[k]) for k, n in offenders.items() if n > allowed[k]}
    assert not new, (
        "a subprocess with a run-time command decodes with the locale code page:\n"
        + "\n".join(f"  {k}  ({n} found, {a} allowlisted)" for k, (n, a) in sorted(new.items()))
        + "\n\nName the codec the child actually writes, or capture bytes and use\n"
        "navig.core.proc_text.decode_console_result when the command is genuinely unknown."
    )


def test_the_allowlist_only_shrinks() -> None:
    """Every allowlisted site must still exist and still offend.

    A stale entry is worse than no entry: it silently exempts whatever later drifts into
    that function. This is what turns the list into a debt that can only go down.
    """
    offenders, allowed = _offenders(), _load_allowlist()
    stale = {k: (a, offenders[k]) for k, a in allowed.items() if a > offenders[k]}
    assert not stale, (
        f"{len(stale)} allowlist entries no longer offend — delete or decrement them:\n"
        + "\n".join(f"  {k}  ({a} allowlisted, {n} found)" for k, (a, n) in sorted(stale.items()))
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
    assert sum(_load_allowlist().values()) >= 10, (
        "the allowlist collapsed — either the scanner stopped seeing the tree or someone "
        "emptied it without fixing the sites"
    )


def test_the_key_survives_an_edit_above_the_call() -> None:
    """The reason for keying by function: the SAME call must produce the SAME key after
    unrelated lines are added above it. A line key fails this by construction."""
    body = (
        "import subprocess\n"
        "def helper():\n"
        "    return subprocess.run(cmd, capture_output=True, text=True)\n"
    )
    shifted = "# three\n# new\n# lines\n" + body
    before = _offending_keys(ast.parse(body), "x/y.py")
    after = _offending_keys(ast.parse(shifted), "x/y.py")
    assert before == after == Counter({"x/y.py::helper": 1})


def test_a_second_call_in_an_allowlisted_function_is_a_new_offence() -> None:
    """The reason for the count: membership alone would exempt the whole function forever."""
    two = (
        "import subprocess\n"
        "class Runner:\n"
        "    def go(self):\n"
        "        subprocess.run(a, capture_output=True, text=True)\n"
        "        subprocess.run(b, capture_output=True, text=True)\n"
    )
    keys = _offending_keys(ast.parse(two), "x/y.py")
    assert keys == Counter({"x/y.py::Runner.go": 2})
    # One allowlisted, two found: the guard must see the second one.
    allowed = Counter({"x/y.py::Runner.go": 1})
    assert {k for k, n in keys.items() if n > allowed[k]} == {"x/y.py::Runner.go"}


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
