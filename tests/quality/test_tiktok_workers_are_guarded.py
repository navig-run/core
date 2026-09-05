"""Every way to start a TikTok action goes through the de-duplication guard.

`_IN_FLIGHT` stops a second tap from re-running work that is already in progress —
a duplicate re-downloads the clip, re-requests tiktok.com at exactly the moment it
is deciding whether we look like a bot, and (since 🔍 began reading slides) pays
for a second AI briefing.

The guard originally lived *inside* ``handle_callback``, so it covered the card's
buttons and neither of the other two entry points: an emoji reaction and the
reply-menu action both called ``_do_analyse`` directly. That is this repo's
most-repeated defect — a guard that protects a PATH rather than the SURFACE — so
the rule is enforced here rather than left as a sentence in a docstring.

Scope note: this scans the whole tree, so it can never be selected by "tests for
changed modules" (it names no module of its own). It is registered in
`sourceGuardArgs` in `scripts/ci-local.mjs`; without that it would run only in the
full suite and could go red on main unnoticed.
"""
from __future__ import annotations

import ast
from pathlib import Path

#: The workers themselves. Reaching one directly skips `run_action`.
_WORKER_NAMES = frozenset({
    "_do_analyse", "_do_download", "_do_transcript", "_do_audio", "_do_full_text",
    "_do_download_images", "_do_transcript_photo",
})

#: The module that OWNS them — it is allowed to call its own workers.
_OWNER = Path("navig") / "telegram" / "tiktok_actions.py"

_ROOTS = ("navig",)


def _repo_core() -> Path:
    return Path(__file__).resolve().parents[2]


def _python_files() -> list[Path]:
    core = _repo_core()
    out: list[Path] = []
    for root in _ROOTS:
        for path in (core / root).rglob("*.py"):
            rel = path.relative_to(core)
            if rel == _OWNER or ".dev" in rel.parts or "build" in rel.parts:
                continue
            out.append(path)
    return out


def _direct_worker_calls(path: Path) -> list[tuple[int, str]]:
    """`<anything>.<worker>(...)` call sites in *path*."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):  # not ours to police
        return []
    # Parse only what could possibly match. A whole-tree `ast.parse` is what makes
    # these guards expensive, and ~20 xdist workers each holding one is what pushes
    # the slowest of them past the suite's 30s per-test cap. A substring test cannot
    # narrow the guard's REACH — a call site must spell the name to be a call site —
    # so this is cost, not coverage. Measured: 5.0s -> 0.6s over the same 869 files.
    if not any(name in source for name in _WORKER_NAMES):
        return []
    try:
        tree = ast.parse(source)
    except SyntaxError:  # not ours to police
        return []
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        # Only attribute access — a bare `_do_analyse(...)` inside another module
        # cannot resolve to this one, so it is not this guard's business.
        if isinstance(func, ast.Attribute) and func.attr in _WORKER_NAMES:
            hits.append((node.lineno, func.attr))
    return hits


def test_no_module_calls_a_tiktok_worker_directly() -> None:
    core = _repo_core()
    offenders = [
        f"{path.relative_to(core)}:{line} -> {name}()"
        for path in _python_files()
        for line, name in _direct_worker_calls(path)
    ]
    assert not offenders, (
        "These call a TikTok worker directly and so skip `run_action`'s "
        "de-duplication — a second reaction/tap runs the whole action again "
        "(re-download, re-request tiktok.com, a second paid AI briefing). Use "
        "`tiktok_actions.analyse_link(...)` or `run_action(...)`:\n  "
        + "\n  ".join(offenders)
    )


def test_the_guard_is_actually_reading_files() -> None:
    """A scan that silently reads nothing passes for the wrong reason.

    Floor rather than an exact count, so adding modules never makes this fail.
    """
    files = _python_files()
    assert len(files) > 300, f"only scanned {len(files)} files — is the root wrong?"
    assert not any(p.relative_to(_repo_core()) == _OWNER for p in files), (
        "the owning module must be excluded, or it reports its own dispatch"
    )


def test_the_owner_still_defines_every_worker_this_guard_names() -> None:
    """The name list is the guard's whole reach — a renamed worker must not
    silently drop out of it."""
    from navig.telegram import tiktok_actions

    missing = sorted(n for n in _WORKER_NAMES if not hasattr(tiktok_actions, n))
    assert not missing, (
        f"tiktok_actions no longer defines {missing} — update _WORKER_NAMES here, "
        "or this guard is watching names that do not exist."
    )
