"""JSON stdout tripwire — every ``--json`` verb, current or future, keeps stdout pure.

PR #239 fixed the bootstrap layer (onboarding/migration narration off stdout —
see test_bootstrap_stdout_purity.py). This file generalizes that guarantee into
a guard for the whole CLI surface, in two layers:

**Layer 1 (static, complete coverage).** The command schema (PR #219 —
``navig.registry.manifest`` walks the live Typer tree and records every verb's
option flags) enumerates ALL core-owned verbs that declare ``--json`` — no verb
is invoked. For each handler, its source plus everything reachable through
plain function calls (deferred ``from navig... import`` included) is scanned:

- BANNED emitters fail the build. Proven empirically (Rich 13, piped stdout):
  ``console.print(json.dumps(...))`` hard-wraps at the console width — 80 when
  piped — inserting newlines inside string values, so any payload with a line
  longer than the terminal no longer parses. ``console_helper.print_json``
  (Rich Syntax, ``word_wrap=True``) corrupts the same way. Both look fine on
  short payloads and break in the consumer's parser months later.
- Every handler must reach an APPROVED pure emitter — the house helper
  ``navig.console_helper.emit_json`` (preferred for new code), a bare
  ``print(json.dumps(...))`` / ``typer.echo(json.dumps(...))``, Rich's
  ``console.print_json`` (``soft_wrap=True`` — verified safe), or
  ``ch.raw_print``. A verb that declares ``--json`` but never produces JSON is
  a schema lie and fails here too.

**Layer 2 (dynamic, sampled).** A matrix of fast, hermetic, daemon-free verbs
runs as REAL subprocesses on a virgin ``NAVIG_CONFIG_DIR`` (onboarding armed,
exactly like a fresh install): stdout must parse as exactly one JSON document,
carry no narration markers, and exit sanely.

If this file fails on your new verb: route the ``--json`` branch through
``navig.console_helper.emit_json(payload)`` and keep every human-facing line
out of stdout while the flag is set.
"""

from __future__ import annotations

import ast
import importlib
import inspect
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any, NamedTuple

import pytest

pytestmark = pytest.mark.integration

ROOT = Path(__file__).resolve().parent.parent.parent


# =============================================================================
# Layer 1 — static schema-driven analysis
# =============================================================================

#: Emitters that CORRUPT piped JSON (empirically verified — see module docstring).
_BANNED_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"\.print\(\s*\w*json\w*\.dumps"),
        "Rich console.print(json.dumps(...)) hard-wraps at the console width when piped",
    ),
    (
        re.compile(r"(?:\bch|console_helper)\.print_json\("),
        "console_helper.print_json renders via Rich Syntax(word_wrap=True) and wraps",
    ),
)

#: Emitters that keep stdout parseable. ``\w*json\w*`` covers the alias zoo:
#: ``json``, ``_json``, ``jsonlib``, ``json_module``, ``json_mod``.
_APPROVED_PATTERN = re.compile(
    r"(?<!\w)emit_json\("  # navig.console_helper.emit_json — the house helper
    r"|(?<![\w.])print\(\s*\w*json\w*\.dumps"  # builtin print(json.dumps(...))
    r"|echo\(\s*\w*json\w*\.dumps"  # typer.echo(json.dumps(...))
    r"|console\.print_json\("  # Rich Console.print_json (soft_wrap=True)
    r"|raw_print\("  # console_helper.raw_print (plain print)
    r"|(?<![\w.])\w*json\w*\.dumps\("  # payload built via json.dumps, emitted via a name
)

#: Verbs that declare --json but whose JSON emission is not statically visible.
#: This list may only SHRINK. A new verb does not get an entry here — it gets
#: routed through navig.console_helper.emit_json instead.
_KNOWN_GAPS: dict[tuple[str, str], str] = {
    ("navig.commands.skills", "skills_run"): (
        "forwards --json to the executed skill via ctx.obj; stdout belongs to the "
        "skill process, so there is no statically checkable emitter in core"
    ),
}

_DEFERRED_IMPORT = re.compile(r"^\s*from\s+([\w.]+)\s+import\s+([^\n(]+)$", re.M)
_CALLEE = re.compile(r"(?<![\w.])(\w+)\s*\(")


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    """Remove docstrings so prose (e.g. a docstring QUOTING an anti-pattern)
    cannot trip the scanner — only what the code does counts."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                node.body = body[1:] or [ast.Pass()]
    return tree


def _scannable_source(fn: Any) -> str:
    """Comment- and docstring-free source of *fn*; "" when unavailable."""
    try:
        raw = inspect.getsource(inspect.unwrap(fn))
    except (OSError, TypeError):
        return ""
    try:
        return ast.unparse(_strip_docstrings(ast.parse(textwrap.dedent(raw))))
    except (SyntaxError, ValueError):  # pragma: no cover — defensive
        return raw


def _reachable_source(fn: Any, depth: int = 3, seen: set | None = None) -> str:
    """Source of *fn* plus everything reachable through plain function calls.

    Follows one namespace at a time: names bound by deferred
    ``from navig... import X`` statements inside the body (the house lazy-import
    style), then module attributes. Method calls (``obj.method(...)``) are not
    followed — statically unresolvable — which is exactly what _KNOWN_GAPS is for.
    """
    if seen is None:
        seen = set()
    key = (getattr(fn, "__module__", ""), getattr(fn, "__qualname__", ""))
    if key in seen or depth < 0:
        return ""
    seen.add(key)
    src = _scannable_source(fn)
    if not src:
        return ""

    deferred: dict[str, Any] = {}
    for m in _DEFERRED_IMPORT.finditer(src):
        mod_name, names = m.groups()
        if not mod_name.startswith("navig"):
            continue
        for part in names.split(","):
            part = part.strip()
            if not part:
                continue
            bits = part.split(" as ")
            target, local = bits[0].strip(), bits[-1].strip()
            try:
                obj = getattr(importlib.import_module(mod_name), target, None)
            except Exception:  # noqa: BLE001 — optional deps must not break the scan
                obj = None
            if obj is not None:
                deferred[local] = obj

    module = sys.modules.get(getattr(fn, "__module__", ""), None)
    pieces = [src]
    for name in sorted(set(_CALLEE.findall(src))):
        obj = deferred.get(name)
        if obj is None and module is not None:
            obj = getattr(module, name, None)
        if obj is None or not inspect.isfunction(obj):
            continue
        if not getattr(obj, "__module__", "").startswith("navig"):
            continue
        pieces.append(_reachable_source(obj, depth - 1, seen))
    return "\n".join(pieces)


class _Analysis(NamedTuple):
    handlers: dict[tuple[str, str], tuple[Any, list[str]]]  # key -> (callback, paths)
    banned: list[str]  # human-readable findings
    gap_keys: set[tuple[str, str]]


@pytest.fixture(scope="module")
def analysis() -> _Analysis:
    """Enumerate every core-owned --json verb from the live Typer tree and scan it.

    Uses the SAME walk that builds the command schema (navig.registry.manifest),
    so coverage is exactly "what the schema declares" — the contract agents and
    the deck consume. Plugin-provided verbs (module not under ``navig.``) are
    excluded: their source lives outside core/.
    """
    import navig.cli as cli_mod
    from navig.registry.manifest import _extract_options, _iter_typer_commands

    cli_mod._register_external_commands(register_all=True)

    handlers: dict[tuple[str, str], tuple[Any, list[str]]] = {}
    for item in _iter_typer_commands(cli_mod.app, ["navig"]):
        cb = item["callback"]
        mod = getattr(cb, "__module__", "")
        if not (mod == "navig" or mod.startswith("navig.")):
            continue
        flags = {f for opt in _extract_options(cb) for f in opt.get("flags", [])}
        if "--json" not in flags:
            continue
        key = (mod, getattr(cb, "__qualname__", cb.__name__))
        if key in handlers:
            handlers[key][1].append(item["path"])
        else:
            handlers[key] = (cb, [item["path"]])

    banned: list[str] = []
    gap_keys: set[tuple[str, str]] = set()
    for key, (cb, paths) in sorted(handlers.items()):
        src = _reachable_source(cb)
        if not src:
            gap_keys.add(key)
            continue
        for pattern, why in _BANNED_PATTERNS:
            match = pattern.search(src)
            if match:
                banned.append(f"{paths[0]} ({key[0]}.{key[1]}): {match.group(0)!r} — {why}")
        if not _APPROVED_PATTERN.search(src):
            gap_keys.add(key)

    return _Analysis(handlers=handlers, banned=banned, gap_keys=gap_keys)


def test_schema_enumerates_a_healthy_population_of_json_verbs(analysis: _Analysis):
    """Self-check of the coverage engine: if the schema walk or the option
    extraction silently breaks, this fails instead of the guards passing on an
    empty set. 194 unique core handlers at the time of writing."""
    assert len(analysis.handlers) >= 150, (
        f"only {len(analysis.handlers)} --json handlers enumerated — the schema walk "
        "(navig.registry.manifest) or option extraction has regressed"
    )


def test_no_json_verb_routes_through_a_corrupting_rich_emitter(analysis: _Analysis):
    """The class PR #239's follow-up killed: JSON piped through the Rich console.

    Rich hard-wraps at the console width (80 when piped) and interprets markup,
    so the output stops parsing the day a value grows past the terminal width.
    Emit machine output with navig.console_helper.emit_json instead.
    """
    assert not analysis.banned, (
        "corrupting Rich emitter(s) reachable from --json verbs "
        "(fix: route the --json branch through navig.console_helper.emit_json):\n  "
        + "\n  ".join(analysis.banned)
    )


def test_every_json_verb_reaches_an_approved_pure_emitter(analysis: _Analysis):
    """A verb that declares --json must visibly produce JSON on stdout.

    New verbs: emit through navig.console_helper.emit_json(payload). Do NOT add
    entries to _KNOWN_GAPS — it exists only for delegation cases where stdout
    belongs to another process.
    """
    unexplained = analysis.gap_keys - set(_KNOWN_GAPS)
    lines = [f"{analysis.handlers[key][1][0]} ({key[0]}.{key[1]})" for key in sorted(unexplained)]
    assert not unexplained, (
        "--json verbs with no approved JSON emitter reachable from the handler "
        "(fix: emit via navig.console_helper.emit_json in the --json branch):\n  "
        + "\n  ".join(lines)
    )


def test_known_gaps_allowlist_only_shrinks(analysis: _Analysis):
    """The ratchet: entries must describe verbs that still exist and still need
    the exemption. A fixed or deleted verb leaves the list in the same change."""
    stale = [key for key in _KNOWN_GAPS if key not in analysis.handlers]
    assert not stale, f"_KNOWN_GAPS entries for verbs that no longer exist: {stale}"

    healed = [key for key in _KNOWN_GAPS if key not in analysis.gap_keys]
    assert not healed, (
        f"_KNOWN_GAPS entries whose verbs now pass the static check — remove them: {healed}"
    )


# =============================================================================
# emit_json — the house helper's own contract
# =============================================================================


def test_emit_json_prints_exactly_one_parseable_document(capsys):
    from navig.console_helper import emit_json

    payload = {
        "path": "E:/very/long/segment/" + "x" * 300,  # would wrap under Rich at width 80
        "note": "[red]looks like markup[/red]",  # would be eaten by Rich markup
        "unicode": "naïve — ✓",
        "n": 3,
    }
    emit_json(payload)
    out = capsys.readouterr().out
    assert json.loads(out) == payload
    assert out.endswith("\n")


def test_emit_json_compact_mode_is_a_single_line(capsys):
    from navig.console_helper import emit_json

    emit_json({"running": False, "pid": None}, indent=None)
    out = capsys.readouterr().out
    assert out == '{"running": false, "pid": null}\n'


# =============================================================================
# Layer 2 — dynamic sampled matrix (real subprocesses, virgin config dir)
# =============================================================================

# Keep in sync with test_bootstrap_stdout_purity.py.
_NARRATION_MARKERS = (
    "first-time setup",
    "Welcome to NAVIG",
    "Verification summary",
    "configuration migrations",
    "NAVIG_SKIP_ONBOARDING",
)

# Fast, hermetic, daemon-free verbs (each measured ≤ ~5s on a virgin dir).
# (args, allowed exit codes). `navig doctor --json` is covered by
# test_bootstrap_stdout_purity.py and not duplicated here.
_MATRIX: tuple[tuple[tuple[str, ...], frozenset[int]], ...] = (
    (("version", "--json"), frozenset({0})),
    (("help", "--json"), frozenset({0})),
    (("block", "list", "--json"), frozenset({0})),
    (("work", "list", "--json"), frozenset({0})),
    (("mode", "list", "--json"), frozenset({0})),
    # Regression: emitted via Rich console.print(json.dumps(...)) before the
    # tripwire (corrupt once any value outgrew the console width).
    (("connector", "list", "--json"), frozenset({0})),
    (("quick", "list", "--json"), frozenset({0})),
    # Error path stays pure too: without a docs/ dir this exits 1 — the --json
    # payload must still be a JSON document, not narration.
    (("docs", "--json"), frozenset({0, 1})),
)


def _virgin_env(tmp_path: Path) -> dict[str, str]:
    """Env for a pristine install: isolated config/data dirs, NO skip flag."""
    env = os.environ.copy()
    env.pop("NAVIG_SKIP_ONBOARDING", None)
    env.pop("NAVIG_ONBOARDING_ACTIVE", None)
    config_dir = tmp_path / "navig-config"
    config_dir.mkdir()
    env["NAVIG_CONFIG_DIR"] = str(config_dir)
    env["NAVIG_DATA_DIR"] = str(tmp_path / "navig-data")
    env["HOME"] = str(tmp_path)
    env["USERPROFILE"] = str(tmp_path)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


@pytest.mark.parametrize(
    ("args", "allowed_exit"),
    _MATRIX,
    ids=["-".join(args[:-1]) or args[0] for args, _ in _MATRIX],
)
def test_json_verb_stdout_is_pure_on_a_virgin_config_dir(
    tmp_path, args: tuple[str, ...], allowed_exit: frozenset[int]
):
    """stdout of `navig <verb> --json` parses as exactly one JSON document —
    no onboarding banner, no migration narration, no in-command chatter."""
    env = _virgin_env(tmp_path)

    result = subprocess.run(
        [sys.executable, "-m", "navig", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        stdin=subprocess.DEVNULL,
        timeout=300,
    )

    assert result.returncode in allowed_exit, (
        f"navig {' '.join(args)} exited {result.returncode}; stderr: {result.stderr[:2000]}"
    )

    # Parsing the WHOLE of stdout enforces "exactly one document" — any stray
    # line before, between, or after the payload breaks it.
    try:
        json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(
            f"navig {' '.join(args)}: stdout is not one JSON document ({exc});\n"
            f"stdout[:2000]: {result.stdout[:2000]!r}"
        )

    for marker in _NARRATION_MARKERS:
        assert marker not in result.stdout, (
            f"narration marker {marker!r} leaked into stdout of navig {' '.join(args)}"
        )
