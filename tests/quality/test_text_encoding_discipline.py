"""Text file I/O must name its encoding.

One of three sibling guards over the same root cause — an implicit codec on this machine is
cp1251. This one covers files we write and read ourselves (answer: UTF-8, strictly).
``test_git_subprocess_encoding.py`` covers a child process whose contract is UTF-8.
``test_console_subprocess_encoding.py`` covers a Windows console tool, whose contract is the
CONSOLE code page — so its answer is the opposite one, and UTF-8 is an offence there.


``open(p)``, ``Path.read_text()`` and ``Path.write_text(s)`` with no ``encoding=`` use
``locale.getpreferredencoding(False)``. That is UTF-8 on most Linux boxes and it is NOT on
Windows, which this project targets first. Measured on the operator's own machine
(2026-08-09, CPython 3.13, ``PYTHONUTF8`` unset, ``sys.flags.utf8_mode == 0``)::

    locale.getpreferredencoding(False)          -> 'cp1251'
    Path.write_text('- \U0001f41b crash')       -> UnicodeEncodeError: 'charmap' codec
    Path(utf8_file).read_text()                 -> MOJIBAKE, no exception at all

Both halves matter and the second is worse. A write of anything outside the code page
raises, which at least fails loudly; a read of a UTF-8 file *succeeds* and returns
corrupted text, because cp1251 maps almost every byte to some character. Nothing errors,
the data is simply wrong from then on — and if that text is written back, the corruption
is now on disk.

This is not hypothetical for this codebase: the guarded trees read and write GitHub issue
titles, PR bodies, commit messages, transcriptions, notification templates and generated
markdown reports. Emoji and non-Latin names are ordinary content there.

**Why a guard and not a lint rule.** Ruff implements this as ``PLW1514``
(unspecified-encoding), but it is a *preview* rule: enabling it means turning on ruff's
whole preview channel repo-wide, which is a far larger change than this one invariant.
Measured on the same baseline, ruff's rule found 37 sites where this check finds 49 — it
does not resolve ``Path.read_text``/``write_text`` the same way. If preview mode is ever
adopted for other reasons, delete this file and select ``PLW1514`` instead.

**Binary mode is excluded, deliberately.** ``open(p, "rb")`` correctly takes no encoding,
and treating it as an offender is the single biggest false-positive source here.

**Resolution is by SHAPE, not by name.** ``pathlib.Path.read_text`` takes no data
positional and ``write_text`` takes exactly one, so a call with more positionals is a
different method that merely shares the name. Three of them exist in this tree:
``adapter.read_text(selector, control_id)`` is an AutoHotkey UI-control read. A name-only
matcher reported all three as defects.
"""

from __future__ import annotations

import ast
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "navig"
REPO = CORE.parents[1]
PLUGINS = REPO / "plugins"

# Keyed "<repo-relative path>:<line>" -> reason. Ratcheted by the staleness test below:
# when a site is fixed its entry must be deleted, so an exemption can never grow to cover
# a NEW offender. Keep it empty unless there is a real one.
_ALLOWLIST: dict[str, str] = {}

# Directories that are not shipped code: vendored corpora, build output, generated
# scaffolding, and tests (a test may deliberately write a mis-encoded fixture).
_SKIP_PARTS = {
    "__pycache__", ".lab", ".backup", ".dev", "node_modules", "site-packages",
    "scaffold-templates", ".archive", "tests", "test", "build", "dist",
}


def _scanned_roots() -> list[Path]:
    """Core plus every first-party plugin package.

    Plugins are in scope because they do the most of this: the GitHub engine alone writes
    markdown reports built from issue titles.
    """
    roots = [CORE]
    if PLUGINS.is_dir():
        roots += sorted(p for p in PLUGINS.glob("navig-*") if p.is_dir())
    harbor = REPO / "private" / "harbor"
    if harbor.is_dir():
        roots.append(harbor)
    return roots


def _python_files() -> list[Path]:
    files: list[Path] = []
    for root in _scanned_roots():
        for f in root.rglob("*.py"):
            if _SKIP_PARTS & set(f.relative_to(root).parts):
                continue
            files.append(f)
    return files


def _has_kwarg(call: ast.Call, name: str) -> bool:
    return any(k.arg == name for k in call.keywords)


def _has_star_kwargs(call: ast.Call) -> bool:
    """``**kw`` may carry encoding; absence cannot be proven, so do not flag."""
    return any(k.arg is None for k in call.keywords)


def _literal_mode(call: ast.Call, pos: int) -> str | None:
    if len(call.args) > pos and isinstance(call.args[pos], ast.Constant):
        value = call.args[pos].value
        return value if isinstance(value, str) else None
    for keyword in call.keywords:
        if keyword.arg == "mode" and isinstance(keyword.value, ast.Constant):
            value = keyword.value.value
            return value if isinstance(value, str) else None
    return None


def _needs_encoding(call: ast.Call) -> str | None:
    """The offending shape's label, or None when this call is fine."""
    func = call.func
    attr = func.attr if isinstance(func, ast.Attribute) else None
    bare = func.id if isinstance(func, ast.Name) else None

    if attr == "read_text":
        if call.args:                      # not Path.read_text — see the module docstring
            return None
        label = "Path.read_text()"
    elif attr == "write_text":
        if len(call.args) != 1:
            return None
        label = "Path.write_text()"
    elif bare == "open" or attr == "open":
        is_builtin = bare == "open"
        mode = _literal_mode(call, 1 if is_builtin else 0)
        if mode is not None and "b" in mode:
            return None                    # binary: encoding is correctly absent
        if mode is None and call.args and not is_builtin:
            return None                    # Path.open(<non-literal mode>) — unknowable
        if is_builtin and not call.args:
            return None                    # open() with no path is not a file open here
        label = "open()" if is_builtin else "Path.open()"
    else:
        return None

    if _has_kwarg(call, "encoding") or _has_star_kwargs(call):
        return None
    return label


def _offenders() -> dict[str, str]:
    found: dict[str, str] = {}
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and (label := _needs_encoding(node)):
                rel = path.relative_to(REPO).as_posix()
                found[f"{rel}:{node.lineno}"] = label
    return found


def test_text_io_names_its_encoding() -> None:
    offenders = {k: v for k, v in _offenders().items() if k not in _ALLOWLIST}
    assert not offenders, (
        "Text file I/O with no encoding= uses the locale code page — cp1251 on the "
        "operator's machine, where a read of a UTF-8 file MOJIBAKES with no exception "
        "and a write of anything outside the code page raises:\n  "
        + "\n  ".join(f"{site}  {label}" for site, label in sorted(offenders.items()))
        + '\n\nAdd encoding="utf-8". For binary use mode "rb"/"wb", which is exempt.'
    )


def test_allowlist_entries_still_match() -> None:
    """An exemption for a site that no longer offends quietly grants itself forever."""
    stale = sorted(set(_ALLOWLIST) - set(_offenders()))
    assert not stale, (
        f"_ALLOWLIST names sites that are no longer offenders: {stale}. Delete them, or "
        "the exemption will cover a future offender at the same line."
    )


def test_the_scan_actually_reads_the_tree() -> None:
    """A scope that silently resolves to nothing passes every assertion above."""
    roots = [r.name for r in _scanned_roots()]
    assert any(r.startswith("navig-") for r in roots), (
        f"no plugin package is in scope, so this guard only checks core: {roots}"
    )
    files = _python_files()
    assert len(files) > 500, (
        f"only {len(files)} python files found — the roots moved and this guard is "
        "checking almost nothing."
    )


def test_the_detector_finds_the_shapes_it_claims_to() -> None:
    """Both directions, on synthetic source, so a broken detector cannot read as clean.

    The negative cases are the ones that matter: binary mode and the same-named methods
    that are not ``pathlib`` calls at all.
    """
    def labels(src: str) -> list[str]:
        tree = ast.parse(src)
        return [
            label
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and (label := _needs_encoding(node))
        ]

    assert labels("p.read_text()") == ["Path.read_text()"]
    assert labels('p.write_text("x")') == ["Path.write_text()"]
    assert labels('open(p)') == ["open()"]
    assert labels('open(p, "w")') == ["open()"]
    assert labels('p.open("r")') == ["Path.open()"]
    assert labels('p.read_text(errors="replace")') == ["Path.read_text()"]

    assert labels('p.read_text(encoding="utf-8")') == []
    assert labels('p.write_text("x", encoding="utf-8")') == []
    assert labels('open(p, "rb")') == []
    assert labels('open(p, "wb")') == []
    assert labels('p.open("rb")') == []
    assert labels('open(p, **kw)') == []
    # Not pathlib: an AutoHotkey UI-control read that merely shares the name.
    assert labels("adapter.read_text(selector, control_id)") == []
    assert labels("adapter.read_text(sel, control_id=c)") == []
