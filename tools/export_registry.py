#!/usr/bin/env python3
"""
Export NAVIG command registry artifacts.

Regenerate the committed manifest under **Python 3.13** (the canonical navig
interpreter) with the first-party plugins installed — any other interpreter
silently SHRINKS the catalog and the guard below will refuse the write.

Usage:
    # THE one that regenerates the committed artifacts. These flags are not a
    # preference — they are the flags the freshness gate itself regenerates with
    # (scripts/ci-local.mjs, `manifestFreshness`), and the committed files must
    # match what the gate produces or it fails on a diff you did not cause.
    python tools/export_registry.py --format both --deprecations-report --output-dir generated

    # Is the committed manifest fresh? Writes NOTHING: exit 0 = every artifact matches
    # a regen (timestamp aside), exit 1 = stale, each stale file named.
    python tools/export_registry.py --check --format both --deprecations-report

    # ⚠ --validate is NOT a dry run. It validates the metadata AND THEN WRITES the
    # artifacts like any other invocation (2026-09-19: run "to check", it regenerated all
    # four tracked files under a session that then had to revert them). --check is the
    # read-only verb.
    python tools/export_registry.py --validate --format both --deprecations-report
    # override the interpreter guard (must equal the running major.minor):
    python tools/export_registry.py --allow-interpreter 3.14 --format both

⚠ Do NOT regenerate the committed manifest with ``--include-hidden``. It is for
ad-hoc inspection of the full catalog, aliases and internal commands included:
it emits ~1784 commands against the gate's ~1379, so committing its output adds
~405 entries the gate's own regeneration would never produce and makes
generated/commands.json diverge from the thing that checks it.
"""

from __future__ import annotations

import argparse
import contextlib
import importlib.metadata
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

# The manifest must reflect THIS source tree, not whatever `navig` a stale editable
# install resolves to. Run as a script, sys.path[0] is tools/ (not the core root), so a
# PEP 660 editable finder pointing at a *different* checkout — or a static finder whose
# module map predates newer command modules — silently drops core commands (navig
# ledger/audit/undo/skill distill/space books were all missing: a 1285->1276 shrink that
# passed the cross-artifact guards). Prepend the core root so `import navig` binds to the
# co-located source. MUST run before importing navig.
#
# The first-party PLUGINS are pinned the same way. Their commands arrive through the
# `navig.commands` entry-point group, whose module targets (`navig_email.commands:…`)
# resolve through sys.path — so an installed plugin that has drifted ahead of or behind
# this checkout leaks foreign commands into the manifest even when `navig` itself is
# correctly bound. Prepending each `plugins/navig-*/` dir makes the entry-point targets
# load THIS tree's plugin source, closing the last import seam.
_CORE_ROOT = Path(__file__).resolve().parent.parent
_REPO_ROOT = _CORE_ROOT.parent
_PINNED_PATHS: list[str] = [str(_CORE_ROOT)]
_PLUGINS_DIR = _REPO_ROOT / "plugins"
if _PLUGINS_DIR.is_dir():
    _PINNED_PATHS.extend(
        str(p) for p in sorted(_PLUGINS_DIR.glob("navig-*")) if p.is_dir()
    )
if sys.path[: len(_PINNED_PATHS)] != _PINNED_PATHS:
    # Drop any existing occurrences so the pinned order is exact, then move them first.
    for _d in _PINNED_PATHS:
        while _d in sys.path:
            sys.path.remove(_d)
    sys.path[:0] = _PINNED_PATHS

# ── tree scope for the `navig.commands` entry-point group ───────────────────────────
#
# The pin above settles WHICH source a plugin loads from, but it cannot uninstall a package
# this repo does not ship: a `navig-leaks` pulled from PyPI, an editable checkout of another
# worktree, a plugin dir that was never added to git. Those entry points are real at runtime
# and must stay — `navig` registers every installed plugin — but the COMMITTED artifact is a
# different thing. It has to be reproducible from this commit alone, or every machine
# regenerates a manifest no other machine reproduces, and the freshness gate fails on paths
# the author never touched. That is exactly what happened on 2026-10-25: 6 `navig display`
# and 5 `navig signing` commands, from plugin dirs absent from every commit, were baked into
# generated/commands.json — a clean clone could not regenerate its own manifest.
#
# So the exporter scopes the group to entry points whose resolved module file is TRACKED IN
# THIS CHECKOUT. The rule is path-based on purpose: nothing to maintain, any checkout of this
# repo is accepted, and precisely what no commit ships is rejected. Outside a git checkout
# (an sdist, a tarball) discovery is left untouched — there is no commit to scope to.

_ENTRY_POINT_GROUP = "navig.commands"


def _clean_git_env() -> dict[str, str]:
    """The environment for our own git queries, without any inherited repository binding.

    The pre-commit hook runs this script from inside a commit, so the process inherits
    ``GIT_DIR`` / ``GIT_WORK_TREE`` / ``GIT_INDEX_FILE`` describing the repository git is
    calling out FROM. Those make ``rev-parse --show-toplevel`` answer for the wrong tree:
    measured while wiring this filter, ``GIT_DIR`` alone turned all 65 installed entry points
    into "not part of this checkout" and produced a core-only manifest. Every query here names
    the path it is about, so the binding is dropped.
    """
    return {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}


def _git_capture(args: list[str], cwd: Path) -> str | None:
    """Run git in ``cwd`` and return stdout, or None when it is unavailable or fails."""
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            check=False,
            env=_clean_git_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.stdout if proc.returncode == 0 else None


def _tracked_in_this_checkout(root: Path) -> set[str] | None:
    """Every repo-relative path in this checkout's index, or None outside git."""
    raw = _git_capture(["ls-files", "-z"], root)
    if raw is None:
        return None
    return {part.replace("\\", "/") for part in raw.split("\0") if part}


def _entry_point_source(ep: object) -> Path | None:
    """The file — or package directory — an entry point's target resolves to right now."""
    module = getattr(ep, "module", None) or str(getattr(ep, "value", "")).split(":")[0]
    if not module:
        return None
    try:
        spec = importlib.util.find_spec(module)
    except (ImportError, AttributeError, ValueError):
        return None
    if spec is None:
        return None
    origin = getattr(spec, "origin", None)
    if origin and origin not in {"built-in", "frozen"}:
        path = Path(origin)
        if path.is_file():
            return path
    for location in getattr(spec, "submodule_search_locations", None) or ():
        return Path(location)
    return None


def _repo_relative(path: Path, toplevels: dict[str, str | None]) -> str | None:
    """``path`` relative to the repository that contains it, cached per directory."""
    key = str(path.parent)
    if key not in toplevels:
        raw = _git_capture(["rev-parse", "--show-toplevel"], path.parent)
        toplevels[key] = raw.strip() if raw else None
    toplevel = toplevels[key]
    if not toplevel:
        return None
    try:
        return path.resolve().relative_to(Path(toplevel).resolve()).as_posix()
    except (OSError, ValueError):
        return None


def entry_point_is_in_this_tree(
    ep: object, tracked: set[str], toplevels: dict[str, str | None]
) -> bool:
    """True when the entry point's module lives in a file this checkout tracks."""
    source = _entry_point_source(ep)
    if source is None:
        return False
    relative = _repo_relative(source, toplevels)
    if not relative:
        return False
    if relative in tracked:
        return True
    if source.is_dir():
        # A package target: tracked when any file below it is (git tracks files, not dirs).
        prefix = f"{relative}/"
        return any(path.startswith(prefix) for path in tracked)
    return False


def _scoped(container: object, eps: list, tracked: set[str], toplevels: dict, dropped: list[str]):
    """Keep the in-tree entry points of one group result, recording what it discarded."""
    kept = []
    for ep in eps:
        if entry_point_is_in_this_tree(ep, tracked, toplevels):
            kept.append(ep)
        else:
            dropped.append(f"{getattr(ep, 'name', '?')} -> {getattr(ep, 'value', '?')}")
    try:
        return type(container)(kept)
    except TypeError:
        return kept


@contextlib.contextmanager
def tree_scoped_entry_points(root: Path = _REPO_ROOT):
    """Scope ``entry_points(group="navig.commands")`` to this checkout while inside.

    Installed before the first manifest build (a fresh export process), so
    ``navig.cli.registration``'s ``_ENTRY_POINT_CMDS`` cache is still cold. Yields the list
    of dropped ``name -> value`` pairs; outside a git checkout it yields nothing and changes
    no discovery at all.
    """
    tracked = _tracked_in_this_checkout(root)
    if tracked is None:
        yield []
        return
    original = importlib.metadata.entry_points
    toplevels: dict[str, str | None] = {}
    dropped: list[str] = []

    def wants_scope(args: tuple, kwargs: dict) -> bool:
        groups = set()
        group = kwargs.get("group")
        if isinstance(group, str):
            groups.add(group)
        for extra in kwargs.get("groups") or ():
            groups.add(str(extra))
        for extra in args:
            if isinstance(extra, str):
                groups.add(extra)
        return _ENTRY_POINT_GROUP in groups

    def filtered_entry_points(*args: object, **kwargs: object):
        result = original(*args, **kwargs)  # type: ignore[arg-type]
        if not wants_scope(args, kwargs):  # type: ignore[arg-type]
            return result
        if isinstance(result, dict):  # the legacy mapping API
            return {
                group: (
                    _scoped(eps, list(eps), tracked, toplevels, dropped)
                    if group == _ENTRY_POINT_GROUP
                    else eps
                )
                for group, eps in result.items()
            }
        return _scoped(result, list(result), tracked, toplevels, dropped)

    importlib.metadata.entry_points = filtered_entry_points  # type: ignore[assignment]
    try:
        yield dropped
    finally:
        importlib.metadata.entry_points = original  # type: ignore[assignment]


def _report_scope(dropped: list[str]) -> None:
    """One stderr line naming what the scope removed — never silence about the leak."""
    if not dropped:
        return
    names = sorted({entry.split(" -> ", 1)[0] for entry in dropped})
    print(
        f"[SCOPE] {len(dropped)} installed command entry point(s) are not part of this "
        f"checkout and are excluded from the manifest: {', '.join(names)}",
        file=sys.stderr,
    )


from navig.registry.manifest import (  # noqa: E402 — must follow the sys.path pin above
    build_full_manifest,
    build_public_manifest,
    deprecations_report,
    render_markdown,
    validate_manifest,
)

# The interpreter that produced the committed manifest. A regen under any other version,
# or with the first-party plugins absent, shrinks the catalog — see _interpreter_guard.
CANONICAL_INTERPRETER = "3.13"


def _emit_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _emit_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _completions_text(manifest: dict) -> str:
    commands = [
        str(c.get("path", "")).strip()
        for c in manifest.get("commands", [])
        if isinstance(c, dict)
    ]
    commands = sorted(c for c in commands if c)
    return "\n".join(commands) + ("\n" if commands else "")


def _emit_completions(path: Path, manifest: dict) -> None:
    _emit_text(path, _completions_text(manifest))


def _render_artifacts(manifest: dict, fmt: str, deprecations: bool) -> dict[str, str]:
    """Every artifact a run with these flags would write, keyed by path relative to the
    output dir — the ONE place that knows the set, so the writer and ``--check`` cannot
    disagree about what "the artifacts" are."""
    out: dict[str, str] = {}
    if fmt in {"json", "both"}:
        out["commands.json"] = json.dumps(manifest, indent=2, sort_keys=True)
    if fmt in {"markdown", "both"}:
        out["commands.md"] = render_markdown(manifest)
    out["completions/commands.txt"] = _completions_text(manifest)
    if deprecations:
        out["deprecations.json"] = json.dumps(deprecations_report(manifest), indent=2, sort_keys=True)
    return out


def _check_artifacts(output_dir: Path, manifest: dict, fmt: str, deprecations: bool) -> int:
    """Compare what a regen WOULD write against ``output_dir``; write nothing.

    The clock is the one thing a regen always changes (``generated_at`` in the JSON, the
    ``_Generated …_`` line in the markdown, the deprecations report), so the committed
    timestamp is carried over before rendering: ``--check`` answers "would a regen change
    anything real", not "was it regenerated just now". Returns 0 when every artifact
    matches, 1 when any is missing or differs — each one named, with the regenerate
    command. Never writes.
    """
    stamp = None
    try:
        stamp = json.loads((output_dir / "commands.json").read_text(encoding="utf-8")).get("generated_at")
    except (OSError, ValueError):
        pass
    if stamp:
        manifest = dict(manifest, generated_at=stamp)
    stale: list[str] = []
    for rel, want in _render_artifacts(manifest, fmt, deprecations).items():
        path = output_dir / rel
        try:
            have = path.read_text(encoding="utf-8")
        except OSError:
            stale.append(f"{path}: missing")
            continue
        if have.replace("\r\n", "\n") != want.replace("\r\n", "\n"):
            stale.append(f"{path}: differs from a fresh regen")
    if not stale:
        print(f"[OK] {output_dir} matches a fresh regen ({manifest.get('total', 0)} commands); nothing written")
        return 0
    print(f"[STALE] {len(stale)} artifact(s) under {output_dir} would change on a regen:", file=sys.stderr)
    for line in stale:
        print(f"  ! {line}", file=sys.stderr)
    print(
        "  Regenerate & commit: python tools/export_registry.py --format both --deprecations-report"
        f" --output-dir {output_dir}",
        file=sys.stderr,
    )
    return 1


def _plugin_command_count(manifest: dict) -> int:
    """Number of commands contributed by first-party plugin packages.

    A plugin command's ``module`` sits under its own top-level package (``navig_download``,
    ``navig_github``, …); core commands are under ``navig``. Zero here means no plugins
    loaded — the strongest positive signal that this interpreter would ship a bare-core
    (shrunken) catalog, independent of the plugin *set* (which varies by install).
    """
    n = 0
    for cmd in manifest.get("commands", []):
        if (cmd.get("module") or "navig").split(".", 1)[0] != "navig":
            n += 1
    return n


def _writing_to_tracked(output_dir: Path) -> bool:
    """True when the export targets the committed ``core/generated`` artifacts.

    Scoped deliberately: a custom ``--output-dir`` (a temp/dry-run compare, a test) is a
    deliberate sandbox write that can never become the committed catalog, so the guard
    never engages there — only a write to the real tracked location is gated.
    """
    try:
        return output_dir.resolve() == (_CORE_ROOT / "generated").resolve()
    except OSError:
        return False


def _interpreter_guard(manifest: dict, allow_interpreter: str | None) -> int:
    """Refuse to overwrite the tracked manifest from an interpreter that would SHRINK it.

    Two silent, previously-committable shrink modes:

    * **wrong Python** — the first-party plugins are installed in 3.13's site-packages, so
      a 3.14 regen drops ~160 plugin commands (measured 1285->1122).
    * **no plugins** — a bare-core interpreter exports 0 plugin commands.

    Soft by design: a matching ``--allow-interpreter <running-version>`` overrides, and a
    non-tracked ``--output-dir`` never reaches this check (so ``--validate`` against a temp
    dir, or any dry-run compare, is never blocked). Returns 0 to proceed, 2 to refuse.
    """
    run_ver = f"{sys.version_info.major}.{sys.version_info.minor}"
    plugin_cmds = _plugin_command_count(manifest)

    concerns: list[str] = []
    if run_ver != CANONICAL_INTERPRETER:
        concerns.append(
            f"running Python {run_ver}; the committed manifest is built under "
            f"{CANONICAL_INTERPRETER} (its first-party plugins live in {CANONICAL_INTERPRETER}'s "
            "site-packages)"
        )
    if plugin_cmds == 0:
        concerns.append(
            f"exported {manifest.get('total', 0)} commands but 0 come from first-party plugin "
            "packages (navig_*) - the plugins are not installed in this interpreter"
        )

    if not concerns:
        return 0

    print(
        "[INTERPRETER GUARD] this export would overwrite the tracked command manifest:",
        file=sys.stderr,
    )
    for concern in concerns:
        print(f"  ! {concern}", file=sys.stderr)
    print(
        "  Writing now would SHRINK the committed catalog and could be committed unnoticed "
        "(the cross-artifact freshness guards stay green on a consistently-shrunken manifest).",
        file=sys.stderr,
    )
    print(
        f"  Fix: regenerate under Python {CANONICAL_INTERPRETER} with the first-party plugins "
        "installed.",
        file=sys.stderr,
    )
    print(
        f"  Override (you accept this interpreter): --allow-interpreter {run_ver}",
        file=sys.stderr,
    )

    if allow_interpreter == run_ver:
        print(
            f"[INTERPRETER GUARD] override accepted (--allow-interpreter {run_ver}); writing anyway.",
            file=sys.stderr,
        )
        return 0
    if allow_interpreter:
        print(
            f"[INTERPRETER GUARD] --allow-interpreter {allow_interpreter} does not match the "
            f"running interpreter {run_ver}; refusing to write.",
            file=sys.stderr,
        )
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--validate",
        action="store_true",
        help=(
            "Exit non-zero if command metadata validation fails. NOT a dry run: the "
            "artifacts are still written afterwards — use --check for a no-write compare."
        ),
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help=(
            "Compare what this run WOULD write against --output-dir and write nothing: "
            "exit 0 when every artifact matches a fresh regen (timestamp aside), 1 when any "
            "is missing or differs. Combine with --validate to validate first."
        ),
    )
    parser.add_argument(
        "--format",
        choices=["json", "markdown", "both"],
        default="json",
        help="Artifacts to generate.",
    )
    parser.add_argument(
        "--include-hidden",
        action="store_true",
        help=(
            "Include hidden/internal commands and aliases — for INSPECTION only. "
            "Never use it to regenerate the committed manifest: the freshness "
            "gate regenerates without it, so the output will not match."
        ),
    )
    parser.add_argument(
        "--output-dir",
        default="generated",
        help="Output directory for generated artifacts.",
    )
    parser.add_argument(
        "--deprecations-report",
        action="store_true",
        help="Emit generated/deprecations.json report.",
    )
    parser.add_argument(
        "--allow-interpreter",
        metavar="X.Y",
        default=None,
        help=(
            "Acknowledge a non-canonical interpreter (must equal the running major.minor, "
            f"e.g. --allow-interpreter 3.14) to write the tracked manifest anyway. Canonical is "
            f"Python {CANONICAL_INTERPRETER}; a mismatch (or missing plugins) silently shrinks the "
            "catalog. Ignored when --output-dir is not the tracked generated/ directory."
        ),
    )

    args = parser.parse_args(argv)

    # Scope the plugin entry points to this checkout BEFORE the first manifest build: that
    # build is what populates navig.cli.registration's entry-point cache, and a command that
    # reaches the cache reaches the committed artifact.
    with tree_scoped_entry_points() as dropped_entry_points:
        manifest = (
            build_full_manifest(validate=False)
            if args.include_hidden
            else build_public_manifest(validate=False)
        )
    _report_scope(dropped_entry_points)

    if args.validate:
        try:
            validate_manifest(manifest)
        except ValueError as exc:
            print("[REGISTRY ERROR] validation failed", file=sys.stderr)
            for line in str(exc).splitlines():
                print(f"[REGISTRY ERROR] {line}", file=sys.stderr)
            return 1

    output_dir = Path(args.output_dir)

    # --check never writes, so it never reaches the interpreter guard either: it compares
    # THIS interpreter's catalog with the committed one, and a shrunken catalog simply
    # reads as stale (which it is, from where this interpreter stands).
    if args.check:
        return _check_artifacts(output_dir, manifest, args.format, args.deprecations_report)

    # Guard the tracked catalog only: a write to core/generated from an interpreter that
    # would shrink the manifest is refused unless explicitly acknowledged. Runs AFTER
    # validation so --validate still reports on its own merits; a temp/custom output-dir
    # (dry-run compare, tests) is never gated.
    if _writing_to_tracked(output_dir):
        guard_rc = _interpreter_guard(manifest, args.allow_interpreter)
        if guard_rc != 0:
            return guard_rc

    if args.format in {"json", "both"}:
        json_path = output_dir / "commands.json"
        _emit_json(json_path, manifest)
        print(f"[OK] Exported {manifest['total']} commands -> {json_path}")

    if args.format in {"markdown", "both"}:
        markdown_path = output_dir / "commands.md"
        _emit_text(markdown_path, render_markdown(manifest))
        print(f"[OK] Markdown reference -> {markdown_path}")

    completions_path = output_dir / "completions" / "commands.txt"
    _emit_completions(completions_path, manifest)
    print(f"[OK] Completion source -> {completions_path}")

    if args.deprecations_report:
        report = deprecations_report(manifest)
        report_path = output_dir / "deprecations.json"
        _emit_json(report_path, report)
        print(f"[OK] Deprecations report -> {report_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
