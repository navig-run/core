"""Core must import, boot, and degrade cleanly with ZERO plugins installed.

Every navig-* plugin is standalone and *uninstallable*. That is only true if core
never hard-depends on one. It regressed silently: ``navig/gateway/routes/voice.py``
imported ``navig.voice.wake_word`` (→ the navig-audio plugin) at module level, so the
module — and the ``PENDING_WAKES`` queue the plugin itself imports back out of it —
could not be imported without navig-audio. Nothing caught it, because in a dev
checkout every plugin *is* installed.

These tests simulate the uninstalled world with a ``sys.meta_path`` blocker, which is
the only way to see what a user who ran `pip install navig` (and nothing else) sees.
"""

from __future__ import annotations

import importlib
import pkgutil
import sys
from pathlib import Path

import pytest

from tests.fixtures.module_eviction import evicted_modules, matches

# Every first-party plugin distribution's import package.
PLUGIN_PACKAGES = (
    "navig_audio", "navig_calendar", "navig_devhost", "navig_download", "navig_email",
    "navig_explore", "navig_games", "navig_generate", "navig_github", "navig_mobile",
    "navig_msstore", "navig_pipeline", "navig_social", "navig_text",
    "navig_harbor", "navig_bot", "navig_deck",
)

# Core modules that are FORWARDING SHIMS: a thin re-export of a plugin's
# implementation, kept so `from navig.voice.stt import STT` works unchanged. Importing
# one WITHOUT its plugin raises ImportError *by design* — that is the documented
# degradation contract, and ~40 call-sites guard it with try/except ImportError.
#
# Anything NOT on this list must import cleanly with no plugins installed.
EXPECTED_SHIMS = (
    "navig.voice",
    "navig.telegram.tiktok_actions",
    "navig.telegram.music_actions",
)


class _PluginBlocker:
    """A meta_path finder that makes every plugin package look uninstalled."""

    def find_spec(self, name, path=None, target=None):  # noqa: ANN001, ANN201
        if any(name == p or name.startswith(p + ".") for p in PLUGIN_PACKAGES):
            raise ImportError(f"No module named {name!r} (simulated: plugin not installed)")
        return None


# Core modules these tests import to assert their IMPORT-TIME behaviour. They must be
# evicted from sys.modules alongside the plugins, or `import_module` hands back the
# cached module — already imported by some earlier test, with the plugins present — and
# every assertion below passes vacuously without executing a single import.
UNDER_TEST = (
    "navig.gateway.routes",
    "navig.gateway.server",
    "navig.agent.voice_input",
)


# The ONE definition of what the simulated install hides. Eviction and the teardown
# purge below must cover exactly the same set: a module the purge misses is a module
# that outlives the world it was born in.
_EVICTED_PREFIXES = PLUGIN_PACKAGES + EXPECTED_SHIMS + UNDER_TEST


def _matches(mod: str, prefixes: tuple[str, ...]) -> bool:
    return matches(mod, prefixes)


@pytest.fixture
def no_plugins(monkeypatch: pytest.MonkeyPatch):
    """Simulate an install with core only — no plugins."""
    blocker = _PluginBlocker()
    monkeypatch.setattr(sys, "meta_path", [blocker, *sys.meta_path])
    with evicted_modules(monkeypatch, _EVICTED_PREFIXES):
        yield blocker


def test_eviction_leaves_no_duplicate_module_behind() -> None:
    """After teardown, `sys.modules[m]` and `parent.leaf` must be the SAME object.

    When they diverge, `monkeypatch.setattr("pkg.mod.attr", …)` — which resolves by
    attribute traversal — patches one module while the code under test reads the other,
    and the patch silently does nothing. That is a bug with no error message: it surfaces
    as an unrelated test failing only when this file ran first in the same worker.

    Driven with an explicit MonkeyPatch so the assertion runs AFTER teardown inside one
    test, instead of depending on which test pytest happens to run next.
    """
    assert UNDER_TEST, "nothing is under test — this guard would pass vacuously"
    # Import them HERE rather than assuming an earlier test did: run alone, none of them
    # is in sys.modules yet, and a guard that only works in a particular order is the
    # same class of bug it exists to catch.
    for mod in UNDER_TEST:
        importlib.import_module(mod)
    before = {m: sys.modules[m] for m in UNDER_TEST}

    mp = pytest.MonkeyPatch()
    try:
        with evicted_modules(mp, _EVICTED_PREFIXES):
            for mod in UNDER_TEST:
                importlib.import_module(mod)      # the re-import every test here performs
    finally:
        mp.undo()

    for mod, original in before.items():
        parent_name, _, leaf = mod.rpartition(".")
        assert sys.modules[mod] is original, f"{mod} was not restored in sys.modules"
        assert getattr(sys.modules[parent_name], leaf) is original, (
            f"{parent_name}.{leaf} is a stale duplicate of {mod} — a later "
            "monkeypatch.setattr on it would patch the wrong module object"
        )


def test_no_module_imported_inside_outlives_the_no_plugins_world() -> None:
    """A module first imported INSIDE the simulated install must not survive its teardown.

    The sibling guard above covers the three names in ``UNDER_TEST`` — the modules that
    already existed when eviction ran. This covers the ones that did not:
    ``test_every_gateway_route_imports_without_plugins`` imports ~20 modules beneath
    ``navig.gateway.routes``, and a fresh xdist worker has loaded none of them. Nothing
    tracked those, so they outlived teardown while their parent was rolled back to the
    original object that never had them as attributes — leaving ``sys.modules`` holding a
    child its parent does not know.

    The damage lands somewhere else entirely: dotted ``monkeypatch.setattr`` resolves by
    attribute traversal, so every later ``setattr("navig.gateway.routes.<mod>.X", …)`` in
    that worker raises ``AttributeError: 'module' object at navig.gateway.routes.<mod> has
    no attribute '<mod>'``. Measured before the fix — ``pytest
    tests/plugins/test_core_standalone.py tests/gateway/test_gateway_core_routes.py``
    failed both WS-heartbeat tests every single time in that order and passed in the
    other, which is why four pre-push gate runs read it as flake.

    Driven with an explicit MonkeyPatch so the assertion runs AFTER teardown inside one
    test, rather than depending on which test pytest happens to run next.
    """
    routes = importlib.import_module("navig.gateway.routes")
    victim = "navig.gateway.routes.core"

    # Reproduce a fresh worker: the package loaded, this submodule not. Done BY HAND and
    # not through the MonkeyPatch below, whose undo would restore the victim and hide the
    # very orphan this asserts against.
    saved_mod = sys.modules.pop(victim, None)
    saved_attr = routes.__dict__.pop("core", None)
    try:
        mp = pytest.MonkeyPatch()
        # Drive the REAL fixture — its body AND its teardown — so this guards the WIRING,
        # not just the helper. The first version of this test called
        # the purge helper itself and passed with the fixture's use of it
        # deleted: it proved the function worked and nothing whatever about anything using
        # it. `__wrapped__` because pytest refuses a direct call to a fixture.
        fixture_fn = getattr(no_plugins, "__wrapped__", no_plugins)
        gen = fixture_fn(mp)
        try:
            next(gen)  # setup, up to the yield
            importlib.import_module(victim)  # born inside the no-plugins world
            next(gen, None)  # teardown, everything after the yield
        finally:
            mp.undo()

        assert victim not in sys.modules, (
            f"{victim} was imported inside the simulated no-plugins install and outlived "
            "its teardown. Its parent package has been rolled back to the original "
            "object, which has no such attribute, so the next dotted monkeypatch.setattr "
            "on it anywhere in this worker raises AttributeError."
        )

        orphans = [
            m
            for m in sys.modules
            if "." in m
            and _matches(m, _EVICTED_PREFIXES)
            and getattr(sys.modules.get(m.rpartition(".")[0]), m.rpartition(".")[2], None)
            is not sys.modules[m]
        ]
        assert not orphans, (
            "modules left in sys.modules that their parent package does not know about — "
            "a dotted monkeypatch.setattr on any of them will not resolve:\n  "
            + "\n  ".join(orphans)
        )
    finally:
        if saved_mod is not None:
            sys.modules[victim] = saved_mod
        if saved_attr is not None:
            routes.core = saved_attr


def test_blocker_actually_blocks(no_plugins) -> None:
    """Guard the guard — a broken blocker would make every test below vacuously pass."""
    with pytest.raises(ImportError):
        importlib.import_module("navig_audio")


def test_every_gateway_route_imports_without_plugins(no_plugins) -> None:
    """A gateway route that needs a plugin to be IMPORTED cannot be registered.

    This is the regression: routes/voice.py imported navig.voice.wake_word at module
    level purely to annotate PENDING_WAKES.
    """
    routes_pkg = importlib.import_module("navig.gateway.routes")

    broken: list[str] = []
    for mod in pkgutil.iter_modules(routes_pkg.__path__, prefix="navig.gateway.routes."):
        try:
            importlib.import_module(mod.name)
        except ImportError as exc:
            broken.append(f"{mod.name}: {exc}")

    assert not broken, "gateway route modules that need a plugin just to import:\n  " + "\n  ".join(
        broken
    )


def test_cli_registers_every_command_without_plugins(no_plugins) -> None:
    """`navig <anything>` must still work — registration imports every command module."""
    import typer

    cli = importlib.import_module("navig.cli")
    # Register onto a FRESH app, not the global `navig.cli.app`: under the blocker the
    # plugin sub-apps fail to load, and mutating the process-wide app would leak that
    # half-registered state into every test that runs after this one.
    cli._register_external_commands(register_all=True, target_app=typer.Typer())


def test_gateway_server_imports_without_plugins(no_plugins) -> None:
    importlib.import_module("navig.gateway.server")


def test_shims_are_the_only_modules_allowed_to_need_a_plugin(no_plugins) -> None:
    """Pin the contract: the shims raise ImportError, and that is intentional.

    If someone deletes a shim (moving the capability wholly into the plugin), this
    test tells them to drop it from EXPECTED_SHIMS rather than leaving a stale entry.
    """
    for shim in EXPECTED_SHIMS:
        with pytest.raises(ImportError):
            importlib.import_module(shim)


def test_requires_plugin_names_the_plugin_instead_of_the_module(no_plugins) -> None:
    """A user who asked for a capability gets an install line, not 'No module named'."""
    from navig.plugins.require import PluginRequired, requires_plugin

    with pytest.raises(PluginRequired) as exc:
        with requires_plugin("navig-audio", "Speech-to-text"):
            import navig.voice.stt  # noqa: F401

    err = exc.value
    assert err.plugin == "navig-audio"
    assert "Speech-to-text" in str(err)
    assert err.hint == "navig store install pip:navig-audio"
    # Subclasses ImportError so the ~40 existing `except ImportError` guards still catch it.
    assert isinstance(err, ImportError)


def test_transcribe_without_navig_audio_is_actionable(no_plugins) -> None:
    """`navig agent transcribe` used to raise a bare ImportError into the crash handler."""
    import asyncio

    from navig.agent.voice_input import (
        TranscriptionBackend,
        TranscriptionConfig,
        VoiceInputHandler,
    )
    from navig.plugins.require import PluginRequired

    handler = VoiceInputHandler(
        config=TranscriptionConfig(backend=TranscriptionBackend.DEEPGRAM)
    )
    with pytest.raises(PluginRequired) as exc:
        asyncio.run(handler._transcribe_via_stt(Path("nope.ogg"), None))

    assert exc.value.plugin == "navig-audio"


# ── Static gate: no NEW unguarded core→plugin import can slip in ──────────────────
#
# The tests above prove core boots WITHOUT plugins today. This one keeps it that way:
# it is a pure AST scan (no imports, milliseconds) that fails the build the moment any
# core file grows a module-level `import navig_x` / `from navig_x import …` outside the
# handful of documented forwarding shims. Runtime tests only cover what they happen to
# import (gateway routes, the CLI, the server); a new unguarded import in, say,
# `navig/notify/foo.py` would sail past them but be caught here.
#
# WHY module-level only: an import nested in a function or `try/except ImportError` is a
# GUARDED, lazy dependency — the legitimate degradation pattern used in ~40 places. Only
# a *top-level* plugin import makes the whole module unimportable without the plugin, and
# that is the failure this gate exists to prevent.


def _navig_package_dir() -> Path:
    import navig

    return Path(navig.__file__).resolve().parent


def _shim_paths() -> list[Path]:
    """Filesystem paths of the documented shims, derived from EXPECTED_SHIMS.

    Single source of truth: a shim added to EXPECTED_SHIMS is automatically allowed here,
    and a stale entry that no longer maps to a file is reported (see the test below).
    """
    pkg = _navig_package_dir()
    out: list[Path] = []
    for shim in EXPECTED_SHIMS:
        rel = shim.split(".", 1)[1].replace(".", "/")  # 'navig.voice' -> 'voice'
        as_pkg = pkg / rel  # a package dir (navig/voice/)
        as_mod = pkg / (rel + ".py")  # a single module (navig/telegram/xxx.py)
        out.append(as_pkg if as_pkg.is_dir() else as_mod)
    return out


def _module_level_plugin_imports(py: Path) -> list[tuple[int, str]]:
    """Top-level (never nested) imports of a `navig_*` package in one file."""
    import ast

    try:
        tree = ast.parse(py.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    hits: list[tuple[int, str]] = []
    for node in tree.body:  # tree.body == module scope only; nested imports are ignored
        names: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [node.module]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        for name in names:
            if name.split(".")[0].startswith("navig_"):
                hits.append((node.lineno, name))
    return hits


def test_no_unguarded_core_to_plugin_imports() -> None:
    """Core never hard-imports a plugin at module scope — except the documented shims."""
    pkg = _navig_package_dir()
    allowed = _shim_paths()

    violations: list[str] = []
    for py in pkg.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        if any(py == a or (a.is_dir() and a in py.parents) for a in allowed):
            continue  # a documented shim — its plugin import is the contract
        for lineno, mod in _module_level_plugin_imports(py):
            rel = py.relative_to(pkg.parent).as_posix()
            violations.append(f"{rel}:{lineno}  imports {mod} at module level")

    assert not violations, (
        "core hard-depends on a plugin at import time — this breaks a plugin-free install.\n"
        "Make it lazy (import inside the function) and guard it (except ImportError), or use\n"
        "navig.plugins.require.requires_plugin() if the capability was explicitly asked for.\n\n  "
        + "\n  ".join(violations)
    )


def test_every_expected_shim_maps_to_a_real_file() -> None:
    """A stale EXPECTED_SHIMS entry would silently widen the allowlist — catch it."""
    missing = [
        shim
        for shim, path in zip(EXPECTED_SHIMS, _shim_paths(), strict=True)
        if not path.exists()
    ]
    assert not missing, f"EXPECTED_SHIMS names with no file on disk (stale?): {missing}"
