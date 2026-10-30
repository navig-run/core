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

# Import packages of first-party plugins that do NOT live under plugins/ (private homes and
# app-shipped packages), plus the floor used where plugins/ is absent (the public core
# mirror). Everything under plugins/navig-*/ is DERIVED below, so a new plugin is blocked
# the day it lands instead of the day someone remembers to list it.
_LISTED_PLUGIN_PACKAGES = (
    "navig_audio", "navig_calendar", "navig_devhost", "navig_download", "navig_email",
    "navig_explore", "navig_games", "navig_github", "navig_mobile",
    "navig_msstore", "navig_pipeline", "navig_social", "navig_text",
    "navig_harbor", "navig_bot", "navig_deck",
)

_PLUGINS_DIR = Path(__file__).resolve().parents[3] / "plugins"


def _core_hard_dependencies() -> frozenset[str]:
    """Import names of the `navig-*` distributions core/pyproject.toml declares as HARD
    dependencies — pip installs those with navig, so they are never simulated away."""
    import re

    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover - Python 3.10
        import tomli as tomllib

    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    out = set()
    for req in data["project"]["dependencies"]:
        name = re.split(r"[\s<>=!~;\[]", req, maxsplit=1)[0].strip().lower()
        if name.startswith("navig-"):
            out.add(name.replace("-", "_"))
    return frozenset(out)


def _first_party_plugin_packages() -> frozenset[str]:
    """Every `plugins/navig-*/navig_*/` import package in this checkout."""
    if not _PLUGINS_DIR.is_dir():
        return frozenset()
    return frozenset(p.parent.name for p in _PLUGINS_DIR.glob("navig-*/navig_*/__init__.py"))


# Every first-party plugin's import package, minus the hard core dependencies.
PLUGIN_PACKAGES = tuple(
    sorted((set(_LISTED_PLUGIN_PACKAGES) | _first_party_plugin_packages()) - _core_hard_dependencies())
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
    # The compat shims behind PLUGIN_BACKED_CORE_COMMANDS: each ALIASES its plugin's
    # module object into sys.modules, so a shim imported by an earlier test (plugins
    # present) leaves that object under the core key, and eviction that misses the key
    # hands the blocked world a REGISTERED telegram/matrix —
    # test_cli_registers_every_command_without_plugins then failed with stale=[...] only
    # when xdist put an importer before it. Listed HERE (raising shims, same contract)
    # so the fixture's prefix set evicts them and the blocked world re-imports fresh.
    "navig.commands.telegram",
    "navig.commands.matrix",
)


# Core command names whose module is a FORWARDING SHIM onto a plugin's Typer app and raises
# an ImportError naming the plugin when it is absent — the documented degradation. Every
# OTHER command in navig.cli.registration._EXTERNAL_CMD_MAP must register with no plugins.
PLUGIN_BACKED_CORE_COMMANDS = {
    "telegram": "navig-telegram",
    "tg": "navig-telegram",
    "matrix": "navig-matrix",
    "mx": "navig-matrix",
}


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
# Core command modules that BIND a plugin at import time and CACHE the result:
# `navig.commands.voice` imports navig_audio's Typer app, or — with the plugin blocked —
# builds a fallback that prints the install hint. Born inside the world as that fallback,
# it outlived teardown (this list did not cover it), and every later test in the same
# worker read the cached fallback: `voice_app is impl` failed and `speak` exited 2 with
# "Voice needs navig-audio 0.2 or newer". They DEGRADE instead of raising, so they are not
# EXPECTED_SHIMS — but they must be evicted exactly the same way.
PLUGIN_BOUND_COMMANDS = ("navig.commands.voice",)

_EVICTED_PREFIXES = PLUGIN_PACKAGES + EXPECTED_SHIMS + PLUGIN_BOUND_COMMANDS + UNDER_TEST


def _matches(mod: str, prefixes: tuple[str, ...]) -> bool:
    return matches(mod, prefixes)


@pytest.fixture
def no_plugins(monkeypatch: pytest.MonkeyPatch):
    """Simulate an install with core only — no plugins."""
    blocker = _PluginBlocker()
    monkeypatch.setattr(sys, "meta_path", [blocker, *sys.meta_path])
    with evicted_modules(monkeypatch, _EVICTED_PREFIXES):
        yield blocker


def test_a_fallback_command_module_does_not_outlive_the_world() -> None:
    """`navig.commands.voice` must be restored — not left as the no-plugins fallback.

    Same shape as the orphan guard below, for the one command module that binds a plugin
    at import time: imported inside the blocked world it caches the fallback app, and if
    that module outlives teardown every later test in the worker reads the fallback
    (measured: `test_assistant_voice_exit_honesty` exited 2 with
    "Voice needs navig-audio 0.2 or newer" whenever this file ran first).
    """
    victim = "navig.commands.voice"
    saved_mod = sys.modules.pop(victim, None)
    parent_name, _, leaf = victim.rpartition(".")
    parent = sys.modules.get(parent_name)
    saved_attr = getattr(parent, leaf, None) if parent is not None else None
    if parent is not None and hasattr(parent, leaf):
        delattr(parent, leaf)  # the seed case: neither entry nor attribute exists before
    mp = pytest.MonkeyPatch()
    try:
        fixture_fn = getattr(no_plugins, "__wrapped__", no_plugins)
        gen = fixture_fn(mp)
        try:
            next(gen)
            born = importlib.import_module(victim)  # fallback app, plugin is blocked
            assert born is not None and born.voice_app is not None
            next(gen, None)  # teardown
        finally:
            mp.undo()
        assert victim not in sys.modules, (
            f"{victim} was born inside the simulated no-plugins install and outlived its "
            "teardown — later tests in this worker read its cached fallback app."
        )
        if parent is not None:
            assert not hasattr(parent, leaf), (
                f"{parent_name}.{leaf} still points at the module the block dropped — the "
                "dangling attribute is what makes the next eviction cycle register a stale "
                "value to restore, leaving the pair (entry, attribute) diverged."
            )
    finally:
        if parent is not None and saved_attr is not None:
            setattr(parent, leaf, saved_attr)
        if saved_mod is not None:
            sys.modules[victim] = saved_mod


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
    from navig.cli import registration

    # Register onto a FRESH app, not the global `navig.cli.app`: under the blocker the
    # plugin sub-apps fail to load, and mutating the process-wide app would leak that
    # half-registered state into every test that runs after this one.
    target_app = typer.Typer()
    cli._register_external_commands(register_all=True, target_app=target_app)

    # Registration swallows every failure into a log warning, so calling it proves nothing
    # by itself: a core command module that grew a hard plugin import would be skipped and
    # this test would stay green. Assert WHICH core commands actually registered.
    registered = registration._registered_app_cmds.get(target_app, set())
    skipped = set(registration._EXTERNAL_CMD_MAP) - registered
    assert skipped == set(PLUGIN_BACKED_CORE_COMMANDS), (
        "core commands that failed to register without plugins (a skipped one is a new hard "
        f"plugin dependency; a missing one is a stale PLUGIN_BACKED_CORE_COMMANDS entry): "
        f"unexpected={sorted(skipped - set(PLUGIN_BACKED_CORE_COMMANDS))} "
        f"stale={sorted(set(PLUGIN_BACKED_CORE_COMMANDS) - skipped)}"
    )


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


def _hard_dependency_packages() -> frozenset[str]:
    """Import names of the `navig-*` packages core declares as HARD dependencies.

    Those are not plugins: pip installs them with navig and they cannot be missing, so core
    may import them at module scope (navig-sdk, navig-vault, navig-contacts own code core
    runs). Read from core/pyproject.toml, so a package demoted back to an extra is policed
    again the day it is.
    """
    return _core_hard_dependencies()


def test_hard_dependencies_are_known_and_never_simulated_as_plugins() -> None:
    hard = _hard_dependency_packages()
    # Anti-vacuity: navig-vault and navig-sdk are hard dependencies today.
    assert {"navig_vault", "navig_sdk"} <= hard, f"could not read core's dependencies: {sorted(hard)}"
    overlap = hard & set(_LISTED_PLUGIN_PACKAGES)
    assert not overlap, (
        f"{sorted(overlap)} is a HARD core dependency — it cannot be uninstalled, so blocking it "
        "in the no-plugins simulation tests a world no user can be in. Remove it from _LISTED_PLUGIN_PACKAGES."
    )


def test_every_first_party_plugin_package_is_blocked() -> None:
    """REGRESSION: the blocker listed 16 packages by hand while plugins/ shipped ~50, so a
    core import of e.g. navig_mini or navig_antivirus sailed through the simulation."""
    derived = _first_party_plugin_packages()
    if not derived:
        pytest.skip("no plugins/ tree here (the public core mirror) — nothing to derive")
    assert {"navig_mini", "navig_antivirus"} <= derived, f"derivation found {sorted(derived)}"
    unblocked = sorted(derived - set(PLUGIN_PACKAGES) - _core_hard_dependencies())
    assert not unblocked, f"first-party plugin packages the no-plugins world can import: {unblocked}"


def _module_level_plugin_imports(py: Path) -> list[tuple[int, str]]:
    """Top-level (never nested) imports of a `navig_*` plugin package in one file.

    A hard dependency (see `_hard_dependency_packages`) is not a plugin and is not reported.
    A forwarding shim (EXPECTED_SHIMS, or beneath one) IS reported: it raises ImportError
    without its plugin by design, so importing it at module scope from any other core file
    makes that file unimportable in a plugin-free install, exactly like `import navig_x`.
    """
    import ast

    try:
        tree = ast.parse(py.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    hard = _hard_dependency_packages()
    hits: list[tuple[int, str]] = []
    for node in tree.body:  # tree.body == module scope only; nested imports are ignored
        names: list[str] = []
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            # `from navig.telegram import music_actions` imports the shim as a NAME.
            names = [node.module, *(f"{node.module}.{a.name}" for a in node.names)]
        elif isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        for name in names:
            top = name.split(".")[0]
            if (top.startswith("navig_") and top not in hard) or matches(name, EXPECTED_SHIMS):
                hits.append((node.lineno, name))
                break
    return hits


def test_a_module_level_shim_import_is_reported(tmp_path) -> None:
    """REGRESSION: only `navig_*` roots were reported, so a top-level import of a shim that
    raises without its plugin slipped past the gate."""
    cases = {
        "from navig.voice.stt import STT\n": "navig.voice.stt",
        "from navig.telegram import music_actions\n": "navig.telegram.music_actions",
        "import navig.telegram.tiktok_actions\n": "navig.telegram.tiktok_actions",
    }
    f = tmp_path / "mod.py"
    for src, expected in cases.items():
        f.write_text(src, encoding="utf-8")
        assert [name for _, name in _module_level_plugin_imports(f)] == [expected], src
    # Precision: a non-shim sibling and a NESTED (lazy) shim import are not reported.
    f.write_text("from navig.telegram import other\ndef g():\n    import navig.voice\n",
                 encoding="utf-8")
    assert _module_level_plugin_imports(f) == []


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
