"""Guard: a plugin's `_compat` path fallback must resolve exactly what navig resolves.

A plugin that runs without navig routes its core touches through a `_compat` module that
delegates when navig is importable and falls back when it is not. The fallback is the
dangerous half, and its danger is structural: **it only executes when navig is absent**, so
nobody running a navig install ever sees it. It can be wrong for months in silence.

That is not hypothetical. `navig_blackbox/_compat.py` shipped a `_config_dir_fallback` that
omitted the system-service branch entirely. Measured under `NAVIG_SYSTEM_SERVICE=1` before
the fix:

    config_dir   core /etc/navig       fallback ~/.navig
    data_dir     core /var/lib/navig   fallback ~/.navig/data

A root-run standalone service therefore read and wrote in a directory navig would never
look at — no error, no warning, just a second empty state directory. `data_dir` was wrong
in a second way too: under a service it is `/var/lib/navig`, NOT `config_dir()/data`, so
deriving it from config_dir gives a different wrong answer rather than the right one.

So every `_*_dir_fallback` in every plugin is compared against its `navig.platform.paths`
twin across an environment matrix — not by reading the code, but by CALLING both and
comparing what comes out. Discovery is from disk, so a plugin that grows a `_compat`
tomorrow is covered the day it does.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

from navig.platform import paths as core_paths

REPO = Path(__file__).resolve().parents[3]
PLUGINS = REPO / "plugins"

# Every combination that changes the answer. The service legs are the ones that were
# missing, so they are the point of the matrix rather than an afterthought.
_ENV_MATRIX = [
    {},
    {"NAVIG_SYSTEM_SERVICE": "1"},
    {"NAVIG_CONFIG_DIR": "/tmp/navig-parity-cfg"},
    {"NAVIG_DATA_DIR": "/tmp/navig-parity-data"},
    {"NAVIG_CONFIG_DIR": "/tmp/navig-parity-cfg", "NAVIG_SYSTEM_SERVICE": "1"},
    {"NAVIG_DATA_DIR": "/tmp/navig-parity-data", "NAVIG_SYSTEM_SERVICE": "1"},
]
_CLEARED = ("NAVIG_CONFIG_DIR", "NAVIG_DATA_DIR", "NAVIG_SYSTEM_SERVICE", "NAVIG_VAULT_DIR",
            "NAVIG_LOG_DIR", "NAVIG_STORE_DIR")


def _compat_modules() -> list[tuple[str, object]]:
    """(plugin name, loaded module) for every plugin `_compat.py`, loaded from its path.

    Loaded rather than imported: these packages are not core dependencies and are not
    installed in CI, so an import-based guard would skip exactly where it must run.
    """
    out = []
    if not PLUGINS.is_dir():
        return out
    for f in sorted(PLUGINS.glob("navig-*/navig_*/_compat.py")):
        spec = importlib.util.spec_from_file_location(f"_compat_probe_{f.parts[-2]}", f)
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception as exc:  # noqa: BLE001 - a plugin that cannot load is its own problem
            pytest.fail(f"{f.relative_to(REPO)} failed to load standalone: {exc}")
        out.append((f.parts[-3], mod))
    return out


def _pairs(mod) -> list[tuple[str, object, object]]:
    """(name, fallback_fn, core_fn) for each `_<x>_dir_fallback` with a paths twin."""
    found = []
    for attr in dir(mod):
        m = re.fullmatch(r"_(\w+_dir)_fallback", attr)
        if not m:
            continue
        core_fn = getattr(core_paths, m.group(1), None)
        if callable(core_fn):
            found.append((m.group(1), getattr(mod, attr), core_fn))
    return found


def test_every_plugin_compat_path_fallback_matches_core(monkeypatch: pytest.MonkeyPatch) -> None:
    modules = _compat_modules()
    # Anti-vacuity: a discovery that silently finds nothing looks exactly like a clean pass.
    assert modules, f"no plugin _compat.py found under {PLUGINS} — the tree moved"

    checked, offenders = 0, []
    for plugin, mod in modules:
        for name, fallback, core_fn in _pairs(mod):
            for env in _ENV_MATRIX:
                for var in _CLEARED:
                    monkeypatch.delenv(var, raising=False)
                for k, v in env.items():
                    monkeypatch.setenv(k, v)
                checked += 1
                got, want = fallback(), core_fn()
                if got != want:
                    offenders.append(
                        f"{plugin}: _{name}_fallback() -> {got}  but navig resolves {want}"
                        f"   (env: {env or 'default'})"
                    )

    assert checked, (
        "no `_<x>_dir_fallback` matched a navig.platform.paths function — either the naming "
        "convention changed or the fallbacks were renamed; this guard is watching nothing."
    )
    assert not offenders, (
        f"{len(offenders)} plugin path fallback(s) disagree with navig.\n"
        "These run ONLY when navig is absent, so the divergence is silent: the plugin reads "
        "and writes somewhere navig never looks.\n  " + "\n  ".join(offenders)
    )


def test_the_service_branch_is_actually_exercised(monkeypatch: pytest.MonkeyPatch) -> None:
    """Teeth for the matrix: NAVIG_SYSTEM_SERVICE must change what core resolves.

    If it ever stops doing so (on a platform where the branch cannot trigger), the matrix
    above still runs but proves nothing about the leg that was missing — so say so here
    rather than let the main test pass vacuously.

    Via monkeypatch, not os.environ directly: a hand-rolled save/restore leaks into every
    later test in the same xdist worker, and `delenv` on a variable that was ABSENT restores
    nothing — so a direct write afterwards survives teardown. The suite has a guard for
    exactly this, and it caught the first version of this test.
    """
    monkeypatch.delenv("NAVIG_CONFIG_DIR", raising=False)
    monkeypatch.setenv("NAVIG_SYSTEM_SERVICE", "1")
    service = core_paths.config_dir()
    monkeypatch.delenv("NAVIG_SYSTEM_SERVICE", raising=False)
    user = core_paths.config_dir()

    assert service != user, (
        "NAVIG_SYSTEM_SERVICE no longer changes config_dir(), so the parity matrix cannot "
        "detect a missing service branch — the exact bug it was written for."
    )
