"""A production desktop build must never compile in a remote-debugging port.

**CROSS-LANGUAGE.** The policy is enforced by three Rust build scripts
(``apps/*/src-tauri/build.rs`` → ``refuse_debug_port_in_release``) plus the JSON configs
they scan, while the only place that legitimately *injects* a debug port is a TypeScript
dev launcher (``apps/os/scripts/tauri-dev.ts``, through ``TAURI_CONFIG``). ``cargo test``
covers the RUNTIME gate in ``webview_pane.rs`` (``cdp_allowed``); nothing covered the
COMPILE-TIME one until this file, and the guard's value is that it fails loudly if one of
the three shells loses it or a config grows the flag.

**What it protects.** A remote-debugging port on an end user's machine lets any local
process drive their logged-in window. The dev launcher injects
``--remote-debugging-port`` via the ``TAURI_CONFIG`` JSON merge patch, which
``tauri_build::build()`` applies at compile time — so a release built from a shell that
still carries that variable would bake the port into the shipped binary. ``build.rs``
panics instead; this test pins that all three shells still panic, that no committed config
carries the flag, and that the dev launcher is still the sole injector.

Wired into ``INVARIANT_GUARDS`` (``scripts/ci-local.mjs``): the selection matches changed
``core/navig/**`` modules against test names, so a change under ``apps/`` reaches nothing
by that route.

The walk prunes directories (and, at the root, the sibling worktrees under ``.dev/``):
this test itself runs from ``.dev/worktrees/<slug>/`` during a land, and a filter applied
to ABSOLUTE paths would then skip every file in the repo and turn all three tests into a
vacuous green.
"""

from __future__ import annotations

import fnmatch
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Iterator

REPO = Path(__file__).resolve().parents[3]

# Never descend into these — at the repo root (sibling worktrees, backups, vendored
# research) and everywhere (installed/built trees a conf file could still live in).
_ROOT_SKIP = {".dev", ".backup", ".lab", ".git"}
_DIR_SKIP = {"node_modules", "target", "dist"}

_SOURCE_SUFFIXES = (".ts", ".tsx", ".mjs", ".js", ".sh", ".ps1", ".py")


@lru_cache(maxsize=1)
def _walk_cached() -> tuple[Path, ...]:
    """Every tracked-looking file in the repo, with heavy trees pruned, walked once.

    Three tests share one walk: a full repo walk per test turned a source guard into a
    15 s suite. The list is immutable and nothing in these tests writes, so caching it
    cannot hide a change the tests would otherwise see.
    """
    return tuple(_walk_uncached())


def _walk_uncached() -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(REPO):
        rel_dir = Path(dirpath).relative_to(REPO)
        if rel_dir == Path("."):
            dirnames[:] = [d for d in dirnames if d not in _ROOT_SKIP]
        else:
            dirnames[:] = [d for d in dirnames if d not in _DIR_SKIP]
        for name in filenames:
            yield Path(dirpath) / name


def _walk() -> tuple[Path, ...]:
    return _walk_cached()


def _files_named(glob: str) -> list[Path]:
    return sorted(p for p in _walk() if fnmatch.fnmatch(p.name, glob))


def _floor(paths: list[Path], expected: str, what: str) -> None:
    """Assert the walk actually found the repo — a scan floor, not a skip."""
    rels = {str(p.relative_to(REPO)).replace("\\", "/") for p in paths}
    assert expected in rels, f"{what} not found (walked {len(paths)} files): expected {expected}"


def test_no_shipped_tauri_config_carries_a_debugging_port() -> None:
    """Nothing committed may hand a window a remote-debugging port.

    Dev injects it through ``TAURI_CONFIG`` at build time precisely BECAUSE it must not
    live in a config file. A config that carries it ships in every profile — debug and
    release alike — and ``build.rs`` then refuses the release build rather than shipping it.
    """
    configs = _files_named("tauri*.conf.json") + _files_named("tauri*.conf.json5")
    _floor(configs, "apps/os/src-tauri/tauri.conf.json", "the OS shell's tauri.conf.json")
    for conf in configs:
        text = conf.read_text(encoding="utf-8")
        assert "remote-debugging" not in text, (
            f"{conf.relative_to(REPO)} carries a remote-debugging flag. Dev injects its "
            "port through TAURI_CONFIG (see apps/os/scripts/tauri-dev.ts); a config file "
            "ships it in every profile. Remove it and let the dev launcher inject it."
        )


def test_every_desktop_shell_build_refuses_the_release_debug_port() -> None:
    """Each shell's build script must fail a release that would carry a debug port.

    ``apps/os`` gained this after measuring the leak (its dev launcher sets
    ``TAURI_CONFIG``); ``apps/anchor`` and ``apps/echo`` are built by the same
    ``tauri_build`` and take the same environment, so an inherited variable bakes a port
    into them exactly as it would into the OS shell.
    """
    build_scripts = [
        p
        for p in _files_named("build.rs")
        if "src-tauri" in p.relative_to(REPO).parts
    ]
    _floor(build_scripts, "apps/os/src-tauri/build.rs", "the OS shell's build.rs")
    assert len(build_scripts) >= 3, (
        "expected a build.rs per desktop shell under apps/*, found "
        f"{[str(p.relative_to(REPO)) for p in build_scripts]}"
    )
    for build_rs in build_scripts:
        text = build_rs.read_text(encoding="utf-8")
        rel = build_rs.relative_to(REPO)
        assert "refuse_debug_port_in_release" in text, (
            f"{rel} has no release refusal. Copy apps/os/src-tauri/build.rs: a release "
            "built with TAURI_CONFIG still set would silently ship CDP."
        )
        # The refusal must be WIRED into main(), not just defined next to it.
        assert re.search(r"fn main\(\)\s*\{\s*refuse_debug_port_in_release\(\)", text), (
            f"{rel} defines the refusal but main() does not call it first"
        )
        # Both injection vectors, read from the environment the build actually sees.
        assert 'env::var("TAURI_CONFIG")' in text, f"{rel} no longer reads TAURI_CONFIG"
        assert 'env::var("PROFILE")' in text, f"{rel} no longer gates on the release profile"


def test_every_shell_strips_a_debug_env_before_building_a_webview() -> None:
    """Each shell must drop a debug-port `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS` at startup.

    Measured on the packaged release (2026-10-19): with that variable set, the shipped
    `navig-os.exe` opened a live CDP endpoint — `msedgewebview2.exe … --remote-debugging-port=9332`
    answering `/json/version` — while a debug flag on the app's own command line opened
    nothing. A release carries no `additional_browser_args` in its Tauri config, so the
    environment reaches the browser process; `debug_env::strip_remote_debugging_env()`
    removes it first. Three shells, one WebView2 toolchain, three copies of the guard —
    this is what keeps them wired in.
    """
    libs = [
        p
        for p in _files_named("lib.rs")
        if set(p.relative_to(REPO).parts[-3:-1]) == {"src-tauri", "src"}
    ]
    _floor(libs, "apps/os/src-tauri/src/lib.rs", "the OS shell's lib.rs")
    assert len(libs) >= 3, f"expected one lib.rs per desktop shell, found {len(libs)}"

    for lib in libs:
        rel = lib.relative_to(REPO)
        text = lib.read_text(encoding="utf-8")
        assert "mod debug_env;" in text, f"{rel} no longer declares the debug_env module"
        assert (lib.parent / "debug_env.rs").exists(), f"{rel} lost src/debug_env.rs"

        idx = text.find("pub fn run()")
        assert idx != -1, f"{rel} has no `pub fn run()` entry point"
        window = text[idx : idx + 400]
        assert "strip_remote_debugging_env()" in window, (
            f"{rel} declares debug_env but never calls strip_remote_debugging_env() "
            "before the builder runs — the guard has to fire before the first webview "
            "exists, i.e. in the first lines of run()"
        )


def test_the_only_tauri_config_injector_is_the_dev_launcher() -> None:
    """Exactly one script sets ``TAURI_CONFIG`` — and it is a dev launcher.

    The whole escape hatch is that variable: any other setter is an unreviewed path from
    a build environment to a shipped debug port, and the build script only refuses the
    release profile, not a debug profile someone later ships.
    """
    setters: list[Path] = []
    for path in _walk():
        if path.suffix not in _SOURCE_SUFFIXES or path.name == "build.rs":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:  # pragma: no cover — unreadable entry in the walk
            continue
        if re.search(r"\bTAURI_CONFIG\s*=", text) or re.search(r'"TAURI_CONFIG"\s*:', text):
            setters.append(path)

    rels = {str(p.relative_to(REPO)).replace("\\", "/") for p in setters}
    assert "apps/os/scripts/tauri-dev.ts" in rels, (
        "the dev launcher no longer sets TAURI_CONFIG — it is the one place a debug port "
        f"may be injected from (found {sorted(rels)})"
    )
    assert rels == {"apps/os/scripts/tauri-dev.ts"}, (
        "TAURI_CONFIG is now set from more than the dev launcher: "
        f"{sorted(rels)}. Every setter is a path from a build environment to a debug "
        "port in a shipped binary — route it through the dev launcher or make it read "
        "the release gate first."
    )
