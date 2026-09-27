"""A path the operator TYPES must be resolved where they typed it.

``main.py`` chdir's into the ACTIVE SPACE before any command runs, so ``Path(x)`` on a
relative, operator-typed ``x`` silently answers a different directory. navig.run/proof
printed ``navig block verify-receipt receipt.json`` and it said "receipt not found" from
the very folder holding the file; ``navig ledger verify --path operations.jsonl`` said
"nothing recorded yet" and exited 0. A sweep then found **59 more** sites of the same
shape across core commands and four plugins (``media``, ``memory export``, ``inbox``,
``mount``, ``explore photos …``, ``beat gen -o`` …).

This guard fails when a CLI parameter declared with ``typer.Option``/``typer.Argument``
and typed ``str``/``Path`` is handed straight to ``Path(...)`` inside that command.
Resolve it with ``navig.platform.paths.resolve_user_path`` (core) or the plugin's
``_typed_path.resolve_user_path`` shim (plugins, which must also run against a core
older than the helper).

Scope, measured rather than assumed: it sees ``Path(<param>)`` only. A ``Path``-typed
parameter used without a ``Path(...)`` call, or a string passed through to another
function, is not visible to it (``cdp screenshot/record -o`` were that shape and are
fixed by hand). The baseline is EMPTY apart from documented non-paths.
"""

from __future__ import annotations

import ast
import functools
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

# (file, function, parameter) -> why a bare Path() is correct there.
_EXEMPT: dict[tuple[str, str, str], str] = {
    ("core/navig/commands/wiki.py", "cmd_edit", "page"): (
        "a wiki page NAME like 'guides/setup', joined under the wiki dir — never a "
        "filesystem path relative to where the operator stands"
    ),
    ("core/navig/commands/cdp.py", "cdp_screenshot", "out"): (
        "inspects the typed string's SHAPE (bare name vs path) to decide whether to "
        "resolve it — the resolution itself goes through resolve_user_path"
    ),
}


def _roots() -> list[Path]:
    roots = [REPO / "core" / "navig" / "commands", *sorted((REPO / "plugins").glob("navig-*"))]
    harbor = REPO / "private" / "harbor"
    if harbor.is_dir():
        roots.append(harbor)
    return roots


def _typed_params(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    args = fn.args.args + fn.args.kwonlyargs
    defaults = (
        [None] * (len(fn.args.args) - len(fn.args.defaults))
        + list(fn.args.defaults)
        + list(fn.args.kw_defaults)
    )
    out: set[str] = set()
    for arg, default in zip(args, defaults):
        if not (
            isinstance(default, ast.Call)
            and isinstance(default.func, ast.Attribute)
            and default.func.attr in ("Option", "Argument")
            and getattr(default.func.value, "id", "") == "typer"
        ):
            continue
        ann = ast.unparse(arg.annotation) if arg.annotation else ""
        if "str" in ann or "Path" in ann:
            out.add(arg.arg)
    return out


def _findings(source: str, rel: str) -> list[tuple[str, str, str, int]]:
    found = []
    for fn in ast.walk(ast.parse(source)):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        params = _typed_params(fn)
        if not params:
            continue
        for node in ast.walk(fn):
            if (
                isinstance(node, ast.Call)
                and getattr(node.func, "id", "") == "Path"
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in params
            ):
                found.append((rel, fn.name, node.args[0].id, node.lineno))
    return found


@functools.lru_cache(maxsize=1)
def _scan() -> tuple[list[tuple[str, str, str, int]], int, int]:
    findings: list[tuple[str, str, str, int]] = []
    files = plugin_files = 0
    for root in _roots():
        for path in root.rglob("*.py"):
            parts = path.relative_to(REPO).parts
            if any(p.startswith(".") or p in ("tests", "site-packages", "node_modules") for p in parts):
                continue
            rel = path.relative_to(REPO).as_posix()
            files += 1
            plugin_files += rel.startswith("plugins/")
            # utf-8-sig: two harbor scripts start with a BOM, which Python runs but ast rejects.
            findings.extend(_findings(path.read_text(encoding="utf-8-sig"), rel))
    return findings, files, plugin_files


def test_scan_actually_covers_core_and_plugins() -> None:
    # A miscomputed root makes "0 findings" meaningless while looking like a pass —
    # so assert the walk reached both trees, counted as a PRESENCE.
    assert (REPO / "core" / "navig").is_dir() and (REPO / "plugins").is_dir()
    _, files, plugin_files = _scan()
    assert files >= 150, f"scanned only {files} files — the walk is not seeing the command tree"
    assert plugin_files >= 100, f"scanned only {plugin_files} plugin files"


def test_typed_paths_are_resolved_where_typed() -> None:
    findings, _, _ = _scan()
    live = [f for f in findings if f[:3] not in _EXEMPT]
    assert not live, (
        "A CLI parameter the operator types as a path reaches Path(...) unresolved — "
        "navig has chdir'd into the active space, so a relative value resolves THERE.\n"
        "Use resolve_user_path (core: navig.platform.paths; plugins: <pkg>._typed_path).\n"
        + "\n".join(f"  {rel}:{line}  {fn}({param})" for rel, fn, param, line in live)
    )


def test_exemptions_are_still_live() -> None:
    # An exemption whose site moved or was fixed is a hole waiting for a new finding.
    findings = {f[:3] for f in _scan()[0]}
    stale = [key for key in _EXEMPT if key not in findings]
    assert not stale, f"remove stale exemptions: {stale}"


def test_the_guard_catches_the_shape() -> None:
    src = (
        "import typer\nfrom pathlib import Path\n"
        "def cmd(path: str = typer.Argument(...)):\n    return Path(path).read_text()\n"
    )
    assert _findings(src, "x.py") == [("x.py", "cmd", "path", 4)]
    fixed = src.replace("Path(path)", "resolve_user_path(path)")
    assert _findings(fixed, "x.py") == []
