"""Rich parses ``[...]`` as markup, so bracketed text in a Rich string misbehaves.

Three shapes, each of which has already shipped:

1. a LITERAL marker — ``ch.dim("[dry-run] Would execute …")`` renders as
   ``" Would execute …"`` (#755). ``dry-run`` is not a style, so Rich drops the tag and
   prints nothing in its place, and the preview then reads exactly like a report of
   something that already happened — on ``navig history replay``, whose whole job in
   that mode is to NOT run the command. Thirteen call sites used the swallowed spelling;
   seventeen used ``[yellow]DRY RUN:[/yellow]``, which renders.
2. an INTERPOLATED value — ``f"[{op_id}] {cmd}"`` in `approve list` / `queue list`
   (#771) and in `memory list`, `space list`, `trigger show`, `matrix status` and
   `connect list`. The id, scope or type is simply gone, and in `memory list` that is
   the id you need to copy for ``navig memory forget <id>``. Whether it survives depends
   on the VALUE: ``[C:/]`` renders (the ``/`` stops Rich reading it as a style) while
   ``[a1b2c3d4]`` vanishes — worse than a consistent failure, because it works in
   testing and disappears on real data.
3. an ORPHAN CLOSING TAG — this one RAISES rather than mis-rendering. A style opened in
   one ``print()`` does not carry into the next, so splitting ``[cyan]…[/cyan]`` across
   two calls leaves the second holding a closing tag with no opener and Rich raises
   ``MarkupError``. ``navig github template-show`` crashed on 4 of its 12 SHIPPED
   builtin templates — every one that had both a schedule interval and a time.

The fix for 1 and 2 is Rich's escape, a backslash before the bracket in source (the
repo already does this in `block.py`, `cdp.py` and `gateway.py`); for 3 it is opening
and closing the tag in the same string.

Scope: every tree that renders through the same helpers — ``core/navig`` plus
``plugins/``, ``apps/`` and ``services/`` (shape 3 lived in a plugin, outside the
original core-only scope). Only the Rich path is checked: ``typer.echo``, logging and
strings returned to a caller keep their brackets and are fine. Calls are recognised by
receiver (``console.print``, ``ch.info``, ``table.add_row``) and by the bare-name
wrappers in ``_BARE_RICH_HELPERS``, which embed the caller's string into a Rich print.
"""

from __future__ import annotations

import ast
import re
from functools import lru_cache
from pathlib import Path

CORE = Path(__file__).resolve().parents[2] / "navig"
_SKIP = {"__pycache__", "builtin", "scaffold-templates"}

#: calls whose string arguments go through Rich markup
_RICH_CALLS = {
    "info", "warning", "error", "success", "dim", "header", "panel", "print", "rule",
}
_RICH_RECEIVERS = {"ch", "console", "con", "_console"}


#: Every tree that renders through the same Rich helpers. `core/navig` was the original
#: scope, but plugins import `console_helper` exactly as core does — and the crash this
#: guard now also catches lived in `plugins/navig-github`, outside the old scope.
_EXTRA_ROOTS = ("plugins", "apps", "services")
_SKIP_DIRS = _SKIP | {"node_modules", ".venv", "build", "dist", "site-packages"}


def _roots() -> list[Path]:
    repo = CORE.parent.parent          # <repo>/core/navig -> <repo>
    roots = [CORE]
    roots += [repo / name for name in _EXTRA_ROOTS if (repo / name).is_dir()]
    return roots


def _rel(path: Path) -> str:
    """Repo-relative display path — sources now span core/, plugins/, apps/, services/."""
    repo = CORE.parent.parent
    try:
        return path.relative_to(repo).as_posix()
    except ValueError:
        return path.as_posix()


def _sources() -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for root in _roots():
        for p in sorted(root.rglob("*.py")):
            if _SKIP_DIRS & set(p.parts) or p in seen:
                continue
            seen.add(p)
            out.append(p)
    return out


def _bad_literals(tree: ast.AST) -> list[tuple[int, str]]:
    """(lineno, text) for Rich-rendered strings containing a bracketed dry-run tag."""
    hits: list[tuple[int, str]] = []

    def strings_of(node: ast.AST):
        """Every string constant inside an expression, including f-string parts."""
        for sub in ast.walk(node):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                yield sub.value

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr not in _RICH_CALLS:
            continue
        recv = func.value
        recv_name = recv.id if isinstance(recv, ast.Name) else getattr(recv, "attr", "")
        if recv_name not in _RICH_RECEIVERS:
            continue
        for value in node.args:
            for text in strings_of(value):
                if "[dry-run]" in text.lower():
                    hits.append((node.lineno, text[:70]))
        for kw in node.keywords:
            if kw.arg in ("title", "subtitle"):
                for text in strings_of(kw.value):
                    if "[dry-run]" in text.lower():
                        hits.append((node.lineno, text[:70]))
    return hits


def test_no_rich_string_carries_a_bracketed_dry_run_tag() -> None:
    # `_scan()` is defined at the bottom, after every detector it feeds; it walks the four
    # trees ONCE for all of them (1632 files, ~6s a pass — four separate passes was most
    # of this file's runtime, and it runs on every core, plugin, app and service change).
    findings = _scan()["literals"]

    assert not findings, (
        "Rich parses [...] as markup, so `[dry-run]` is dropped and the marker never "
        "prints — the preview then reads like a report of something that already "
        "happened. Use `[yellow]DRY RUN:[/yellow]` (or plain `DRY RUN:` inside an "
        "existing style tag):\n" + "\n".join(f"  {f}" for f in findings)
    )


def test_rich_really_swallows_the_tag() -> None:
    """Pin the premise, so this guard can never be silently invalidated."""
    from rich.console import Console

    console = Console(force_terminal=False, no_color=True, width=200)
    with console.capture() as cap:
        console.print("[dry-run] Would execute")
    assert "dry-run" not in cap.get()

    with console.capture() as cap:
        console.print("[yellow]DRY RUN:[/yellow] Would execute")
    assert "DRY RUN:" in cap.get()


def test_guard_detects_a_planted_violation() -> None:
    """Teeth: the exact shape that shipped in commands/history.py."""
    tree = ast.parse('ch.dim("[dry-run] Would execute the command above")\n')
    assert _bad_literals(tree) == [(1, "[dry-run] Would execute the command above")]


def test_guard_ignores_plain_output() -> None:
    """typer.echo does not parse markup — the brackets survive there and are fine."""
    tree = ast.parse('typer.echo("[dry-run] Would write")\n')
    assert _bad_literals(tree) == []


def test_guard_accepts_the_house_style() -> None:
    tree = ast.parse('ch.info("[yellow]DRY RUN:[/yellow] Would clear 2 operations")\n')
    assert _bad_literals(tree) == []


# ── the second shape: an interpolated value inside unescaped brackets ────────────

_RICH_RECEIVERS = {"ch", "console", "con", "_console", "table", "t", "bt"}
_RICH_METHODS = _RICH_CALLS | {"add_row", "add_column"}


#: Bare-name wrappers that embed the caller's string INSIDE a Rich `console.print`, e.g.
#: `print_error(msg)` -> `console.print(f"[red]❌ {msg}[/red]")`. The caller's markup is
#: parsed just the same, so a bracketed value is swallowed and an orphan tag still raises
#: — but the call is an `ast.Name`, not an attribute, so the receiver check misses it.
#: `test_bare_helpers_really_render_through_rich` proves each name earns its place here.
_BARE_RICH_HELPERS = {"print_error", "print_success", "print_warning", "print_info"}


def _is_rich_call(node: ast.Call) -> bool:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id in _BARE_RICH_HELPERS
    if not isinstance(func, ast.Attribute) or func.attr not in _RICH_METHODS:
        return False
    recv = func.value
    name = recv.id if isinstance(recv, ast.Name) else getattr(recv, "attr", "")
    return name in _RICH_RECEIVERS


def _bracketed_interpolations(tree: ast.AST) -> list[tuple[int, str]]:
    """(lineno, text) for `f"[{value}]"` in a Rich call, unescaped and not a style tag."""
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_rich_call(node)):
            continue
        args = list(node.args) + [k.value for k in node.keywords if k.arg in ("title", "subtitle")]
        for arg in args:
            for fstring in [n for n in ast.walk(arg) if isinstance(n, ast.JoinedStr)]:
                parts = fstring.values
                rendered = "".join(
                    p.value if isinstance(p, ast.Constant) and isinstance(p.value, str) else "{}"
                    for p in parts
                )
                # `[{style}]text[/{style}]` interpolates a STYLE NAME — the closing tag
                # proves it, and that usage is correct.
                if "[/" in rendered:
                    continue
                for i, part in enumerate(parts):
                    if not isinstance(part, ast.FormattedValue):
                        continue
                    before = parts[i - 1] if i > 0 else None
                    after = parts[i + 1] if i + 1 < len(parts) else None
                    if not (isinstance(before, ast.Constant) and isinstance(before.value, str)):
                        continue
                    if not (isinstance(after, ast.Constant) and isinstance(after.value, str)):
                        continue
                    if not (before.value.endswith("[") and after.value.startswith("]")):
                        continue
                    if before.value.endswith(chr(92) + "["):
                        continue  # already escaped — this is the fix, not the bug
                    hits.append((fstring.lineno, rendered[:70]))
                    break
    return hits


def test_no_rich_string_interpolates_a_value_inside_brackets() -> None:
    findings = _scan()["interpolations"]

    assert not findings, (
        "Rich reads [...] as markup, so the interpolated value is swallowed and the id / "
        "scope / type never prints. Escape the bracket the way block.py and cdp.py do "
        "(a doubled backslash in source):\n" + "\n".join(f"  {f}" for f in findings)
    )


def test_rich_really_swallows_an_interpolated_id() -> None:
    """Pin the premise with real Rich output, and show the escape restores it."""
    from rich.console import Console

    console = Console(force_terminal=False, no_color=True, width=200)
    with console.capture() as cap:
        console.print("  [a1b2c3d4] I prefer dark mode")
    assert "a1b2c3d4" not in cap.get()

    with console.capture() as cap:
        console.print("  " + chr(92) + "[a1b2c3d4] I prefer dark mode")
    assert "[a1b2c3d4]" in cap.get()


def test_interpolation_guard_detects_a_planted_violation() -> None:
    """Teeth: the exact shape that shipped in commands/memory.py."""
    tree = ast.parse('ch.console.print(f"  [{fact.id}] {fact.content}")\n')
    assert _bracketed_interpolations(tree) == [(1, "  [{}] {}")]


def test_interpolation_guard_accepts_the_escape_and_style_tags() -> None:
    escaped = ast.parse('ch.console.print(f"  ' + chr(92) + chr(92) + '[{fact.id}] {x}")\n')
    assert _bracketed_interpolations(escaped) == []

    styled = ast.parse('ch.console.print(f"[{colour}]{text}[/{colour}]")\n')
    assert _bracketed_interpolations(styled) == []


# ── the third shape: a closing tag with no opener, which RAISES ─────────────────

#: `[link=url]`, `[color(3)]` etc. carry an attribute — the tag NAME is what pairs with
#: the closing tag, so match up to the first "=" or "(".
_TAG_RE = re.compile(r"(?<!\\)\[(/?)([a-z][a-z0-9_ .#]*)(?:[=(][^\]]*)?\]")


def _flatten(node: ast.AST) -> str | None:
    """A literal or f-string as text, with {} standing in for each placeholder."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            p.value if isinstance(p, ast.Constant) and isinstance(p.value, str) else "{}"
            for p in node.values
        )
    return None


def _orphan_closing_tags(tree: ast.AST) -> list[tuple[int, str]]:
    """(lineno, text) for a Rich string whose closing tag has no matching opener."""
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_rich_call(node)):
            continue
        args = list(node.args) + [k.value for k in node.keywords if k.arg in ("title", "subtitle")]
        for arg in args:
            text = _flatten(arg)
            if not text or "[/" not in text:
                continue
            open_tags: list[str] = []
            for match in _TAG_RE.finditer(text):
                closing, name = match.group(1), match.group(2).strip()
                if not closing:
                    open_tags.append(name)
                elif not name:                  # bare [/] closes the most recent
                    if not open_tags:
                        hits.append((getattr(arg, "lineno", node.lineno), text[:70]))
                        break
                    open_tags.pop()
                elif name in open_tags:
                    open_tags.remove(name)
                else:
                    hits.append((getattr(arg, "lineno", node.lineno), text[:70]))
                    break
    return hits


def test_no_rich_string_closes_a_tag_it_never_opened() -> None:
    """Unlike a swallow, this one RAISES — it takes the command down.

    `plugins/navig-github` split one style across two print() calls::

        console.print(f"\\n   [cyan]Schedule: {interval}")
        console.print(f"      At: {time}[/cyan]")

    Rich parses each print independently, so the second held a closing tag with no
    opener and raised `MarkupError`. `navig github template-show` crashed for any
    template that had both an interval and a time. An unclosed OPEN tag is harmless —
    Rich just applies the style to the end — so only the closing direction is checked.
    """
    findings = _scan()["orphans"]

    assert not findings, (
        "Rich raises MarkupError on a closing tag with no matching opener, so these "
        "lines crash the command rather than mis-rendering. A style does not carry "
        "between print() calls — open and close it in the same string:\n"
        + "\n".join(f"  {f}" for f in findings)
    )


def test_rich_really_raises_on_an_orphan_closing_tag() -> None:
    """Pin the premise: this is a crash, not a swallow."""
    import pytest
    from rich.console import Console
    from rich.errors import MarkupError

    console = Console(force_terminal=False, no_color=True, width=200)
    with pytest.raises(MarkupError):
        with console.capture():
            console.print("      At: 09:00[/cyan]")

    # an unclosed OPENING tag is fine — the style simply runs to the end
    with console.capture() as cap:
        console.print("[cyan]Schedule: every 6h")
    assert "Schedule: every 6h" in cap.get()


def test_orphan_guard_detects_the_real_regression() -> None:
    """Teeth: the exact line that shipped in plugins/navig-github."""
    tree = ast.parse('console.print(f"      At: {t}[/cyan]")\n')
    assert _orphan_closing_tags(tree) == [(1, "      At: {}[/cyan]")]


def test_orphan_guard_accepts_balanced_and_attribute_tags() -> None:
    balanced = ast.parse('console.print("[bold green]ok[/bold green]")\n')
    assert _orphan_closing_tags(balanced) == []

    # [link=url] carries an attribute; the NAME is what pairs with [/link]
    attributed = ast.parse('console.print(f"[link={u}]{u}[/link]")\n')
    assert _orphan_closing_tags(attributed) == []

    # a bare [/] closes the most recent open tag
    bare = ast.parse('console.print("[cyan]text[/]")\n')
    assert _orphan_closing_tags(bare) == []


def test_scan_actually_reaches_outside_core() -> None:
    """Anti-vacuity: the crash this guard catches lived in `plugins/`, not `core/`.

    A guard that quietly stops scanning a tree still passes — it just stops finding
    anything. Pin that every root is really walked.
    """
    scanned = {_rel(p).split("/", 1)[0] for p in _sources()}
    assert "core" in scanned
    for root in _EXTRA_ROOTS:
        if (CORE.parent.parent / root).is_dir():
            assert root in scanned, f"{root}/ is on disk but nothing under it was scanned"


def test_bare_helpers_really_render_through_rich() -> None:
    """A name only counts as Rich if its definition actually prints through Rich.

    `print_error` is treated as a Rich call by name alone — there is no receiver to
    check. If someone later defines a helper with one of these names that just calls
    `print()`, the guard would start flagging harmless brackets in its callers. So every
    definition found in the scanned trees has to render through Rich, or the name comes
    off the list.
    """
    scan = _scan()
    assert scan["helper_definitions"], (
        "no definition of any bare Rich helper was found — _BARE_RICH_HELPERS is stale, "
        "so every call it was meant to cover is now unchecked"
    )
    assert not scan["plain_helpers"], (
        "these are treated as Rich-rendering by name but do not print through Rich, so "
        "the guard would flag their callers wrongly. Drop the name from "
        "_BARE_RICH_HELPERS or rename the helper:\n"
        + "\n".join(f"  {p}" for p in scan["plain_helpers"])
    )


def test_bare_helper_calls_are_checked() -> None:
    """Teeth: the same two defects, behind a bare helper instead of console.print."""
    swallowed = ast.parse('print_error(f"[{repo}] backup failed")\n')
    assert _bracketed_interpolations(swallowed) == [(1, "[{}] backup failed")]

    orphan = ast.parse('print_info(f"      At: {t}[/cyan]")\n')
    assert _orphan_closing_tags(orphan) == [(1, "      At: {}[/cyan]")]

    # a helper NOT on the list is untouched — its brackets survive
    plain = ast.parse('log_line(f"[{repo}] backup failed")\n')
    assert _bracketed_interpolations(plain) == []


# ── one pass over the trees, feeding every whole-tree check above ───────────────


def _renders_through_rich(fn: ast.FunctionDef) -> bool:
    """True when a function body prints through a Rich console."""
    for sub in ast.walk(fn):
        if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute):
            recv = sub.func.value
            name = recv.id if isinstance(recv, ast.Name) else getattr(recv, "attr", "")
            if sub.func.attr in _RICH_METHODS and name in _RICH_RECEIVERS:
                return True
    return False


@lru_cache(maxsize=1)
def _scan() -> dict[str, tuple]:
    """Every whole-tree finding, from ONE walk + parse of the four source trees.

    Each check used to walk and parse the tree itself. That is 1632 files and ~6s a pass,
    four passes, twice per `npm run ci` run (this file is both a source guard and a tree
    scanner) — for findings that are identical every time. Parsed trees are dropped as we
    go, so only the (normally empty) finding lists are held.
    """
    literals: list[str] = []
    interpolations: list[str] = []
    orphans: list[str] = []
    plain_helpers: list[str] = []
    helper_definitions = 0

    for path in _sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        rel = _rel(path)
        literals += [f"{rel}:{ln}: {t!r}" for ln, t in _bad_literals(tree)]
        interpolations += [f"{rel}:{ln}: {t!r}" for ln, t in _bracketed_interpolations(tree)]
        orphans += [f"{rel}:{ln}: {t!r}" for ln, t in _orphan_closing_tags(tree)]
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name in _BARE_RICH_HELPERS:
                helper_definitions += 1
                if not _renders_through_rich(node):
                    plain_helpers.append(f"{rel}:{node.lineno}: {node.name}")

    return {
        "literals": tuple(literals),
        "interpolations": tuple(interpolations),
        "orphans": tuple(orphans),
        "plain_helpers": tuple(plain_helpers),
        "helper_definitions": helper_definitions,
    }


def test_scan_wiring_reports_every_shape(tmp_path, monkeypatch) -> None:
    """Teeth for the shared pass: a planted file must surface under all three keys.

    The three whole-tree tests now read `_scan()` rather than walking themselves, so a
    scan that quietly returned nothing — a broken root, an exception swallowed by the
    parse guard — would turn all three green while checking nothing.
    """
    import sys

    planted = tmp_path / "planted.py"
    planted.write_text(
        'ch.dim("[dry-run] Would execute")\n'
        'ch.info(f"[{op_id}] {cmd}")\n'
        'console.print(f"      At: {t}[/cyan]")\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(sys.modules[__name__], "_sources", lambda: [planted])
    _scan.cache_clear()
    try:
        scan = _scan()
        assert scan["literals"], "the literal check is not wired to the shared scan"
        assert scan["interpolations"], "the interpolation check is not wired to the shared scan"
        assert scan["orphans"], "the orphan check is not wired to the shared scan"
    finally:
        _scan.cache_clear()   # the real trees are what every other test asserts on
