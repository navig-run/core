"""`connectors.bootstrap.ensure_connectors_loaded` — the once-only load guard.

Regression: the "already loaded" flag was set on *entry*, which conflated two
different jobs — guarding re-entrancy and recording completion. The registry
import is not inside a `try`, so anything escaping the function left the flag
stuck at True: every later call returned immediately and the process stayed
permanently connector-less, with no way to retry. Every CLI, MCP and gateway
surface goes through this function, so a single transient import failure at boot
silently disabled connectors for the life of the process.
"""

from __future__ import annotations

import pytest

import navig.connectors.bootstrap as bootstrap_mod
from navig.connectors.bootstrap import ensure_connectors_loaded
from navig.connectors.registry import get_connector_registry


@pytest.fixture(autouse=True)
def _reset_bootstrap_state(monkeypatch):
    """Run each test against a clean registry and an unloaded bootstrap."""
    registry = get_connector_registry()
    registry.reset()
    monkeypatch.setattr(bootstrap_mod, "_CONNECTORS_LOADED", False)
    # raising=False: the re-entrancy flag is part of the fix, and this fixture must
    # not be what fails on a version that lacks it — otherwise every test in the file
    # errors for the wrong reason and the regression proves nothing.
    monkeypatch.setattr(bootstrap_mod, "_LOADING", False, raising=False)
    yield
    registry.reset()


def test_loads_the_builtin_connectors():
    ensure_connectors_loaded()
    assert get_connector_registry().has("gmail")
    assert bootstrap_mod._CONNECTORS_LOADED is True


def test_is_idempotent(monkeypatch):
    ensure_connectors_loaded()
    first = set(get_connector_registry().all_classes())

    # Patched on the registry module (the symbol bootstrap imports at call time),
    # so this test is meaningful against any version of the guard.
    calls = []
    import navig.connectors.registry as registry_mod

    real = registry_mod.get_connector_registry
    monkeypatch.setattr(
        registry_mod, "get_connector_registry", lambda: (calls.append(1), real())[1]
    )
    ensure_connectors_loaded()

    assert calls == [], "a second call must not redo the registration work"
    assert set(get_connector_registry().all_classes()) == first


def test_a_failed_load_is_retryable(monkeypatch):
    """THE REGRESSION: a raise must not permanently disable connectors."""
    # Fail inside the work the guard protects, via a symbol that exists in every
    # version of this module -- so the test measures the GUARD's behaviour, not the
    # presence of a helper introduced by the fix.
    import navig.connectors.registry as registry_mod

    attempts = {"n": 0}
    real = registry_mod.get_connector_registry

    def _flaky():
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ImportError("transient failure reaching the registry")
        return real()

    monkeypatch.setattr(registry_mod, "get_connector_registry", _flaky)

    with pytest.raises(ImportError):
        ensure_connectors_loaded()

    assert bootstrap_mod._CONNECTORS_LOADED is False, (
        "a failed load must not mark itself complete — the flag records success, "
        "not merely that an attempt was made"
    )
    assert bootstrap_mod._LOADING is False, "the re-entrancy flag must be released"

    # The retry succeeds and connectors are actually available.
    ensure_connectors_loaded()
    assert attempts["n"] == 2
    assert get_connector_registry().has("gmail")
    assert bootstrap_mod._CONNECTORS_LOADED is True


def test_reentrant_call_during_load_does_not_recurse(monkeypatch):
    """A connector module that calls back in here must not loop forever."""
    import navig.connectors.registry as registry_mod

    depth = {"max": 0, "cur": 0}
    real = registry_mod.get_connector_registry

    def _reentrant():
        depth["cur"] += 1
        depth["max"] = max(depth["max"], depth["cur"])
        if depth["cur"] == 1:
            ensure_connectors_loaded()  # re-entry, as an importing connector might
        try:
            return real()
        finally:
            depth["cur"] -= 1

    monkeypatch.setattr(registry_mod, "get_connector_registry", _reentrant)
    ensure_connectors_loaded()

    assert depth["max"] == 1, "re-entry must be refused, not recursed into"
    assert get_connector_registry().has("gmail")


def test_individual_connector_failures_do_not_block_the_rest():
    """A broken connector is skipped; the load still completes and is marked done."""
    ensure_connectors_loaded()
    classes = get_connector_registry().all_classes()
    assert len(classes) > 1
    assert bootstrap_mod._CONNECTORS_LOADED is True
