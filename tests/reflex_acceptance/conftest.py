"""Fixtures for the independent Reflex Unified acceptance harness.

Helpers/constants live in ``_support.py`` (imported by the test modules too); this file
only provides the isolated environment, the documented fake-HomeBridge injection and the
projection modules.

TDD state: the implementations land in parallel (task-4 endpoints, task-5 router,
task-7 Answer, task-6 plugin wiring). Until then these tests are RED on purpose and must
never be weakened — missing implementations fail via ``_support.require*``.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import _support as S  # noqa: E402

pytestmark = pytest.mark.integration


@pytest.fixture
def cli_env(tmp_path, monkeypatch):
    """Isolated config/data/home dirs + registered core CLI (reference test idiom)."""
    home = tmp_path / "home"
    config = tmp_path / "config"
    data = tmp_path / "data"
    for directory in (home, config, data):
        directory.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("NAVIG_CONFIG_DIR", str(config))
    monkeypatch.setenv("NAVIG_DATA_DIR", str(data))
    monkeypatch.chdir(tmp_path)

    from navig.config import reset_config_manager

    reset_config_manager()

    import navig.cli as cli_mod

    cli_mod._config_manager = None
    cli_mod._NO_CACHE = False
    cli_mod._register_external_commands(register_all=True)
    return tmp_path


@pytest.fixture
def fake_home(cli_env, monkeypatch):
    """Install :class:`_support.FakeHome` through the documented injectable bridge seam.

    The endpoints CLI documents its seam explicitly (``navig.commands.endpoints._make_store``
    — "Tests patch this to inject a fake bridge"), and ``EndpointStore(bridge=…)`` is the
    documented constructor. The reflex modules get the same store through their own factory
    names once they land; the class/runner patches cover a default-constructed bridge.
    """
    client = S.require("navig.endpoints.client")
    store_module = S.require("navig.endpoints.store")
    fake = S.FakeHome()
    real_store = store_module.EndpointStore(bridge=fake)
    patched: list[str] = []

    def _fake_store(*_args, **_kwargs):
        return real_store

    for module_name in (
        "navig.commands.endpoints",
        "navig.reflex.service",
        "navig.reflex.preclassify",
        "navig.reflex.router",
    ):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        for attr in ("_make_store", "make_store", "get_store", "_store"):
            if hasattr(module, attr):
                monkeypatch.setattr(module, attr, _fake_store, raising=False)
                patched.append(f"{module_name}.{attr}")
        if hasattr(module, "HomeBridge"):
            monkeypatch.setattr(module, "HomeBridge", lambda *a, **k: fake, raising=False)
            patched.append(f"{module_name}.HomeBridge")

    if hasattr(client, "HomeBridge"):
        monkeypatch.setattr(client, "HomeBridge", lambda *a, **k: fake, raising=False)
        patched.append("navig.endpoints.client.HomeBridge")
    for runner_name in ("run_cli", "run_command", "default_runner"):
        candidate = getattr(client, runner_name, None)
        if callable(candidate):
            monkeypatch.setattr(client, runner_name, fake.run, raising=False)
            patched.append(f"navig.endpoints.client.{runner_name}")
    if hasattr(client, "subprocess"):
        monkeypatch.setattr(client.subprocess, "run", fake.run, raising=False)
        patched.append("navig.endpoints.client.subprocess.run")

    if not patched:
        pytest.fail(
            "no injectable HomeBridge seam found — plan §2.3 requires "
            "``HomeBridge(…, runner=…)`` and the endpoints CLI documents "
            "``_make_store()`` as the test injection point.",
            pytrace=False,
        )

    # Reflex seams (documented in navig/reflex/service.py): the resolver reads EndpointStore,
    # the effect phase takes an injectable CommandRunner.
    try:
        resolve_module = importlib.import_module("navig.reflex.resolve")
    except ImportError:
        resolve_module = None
    if resolve_module is not None and hasattr(resolve_module, "StoreResolver"):
        monkeypatch.setattr(
            resolve_module.StoreResolver,
            "_endpoint_store",
            lambda self: real_store,
            raising=False,
        )
        patched.append("navig.reflex.resolve.StoreResolver._endpoint_store")

    fake_runner = S.FakeCommandRunner(fake)
    for module_name in ("navig.reflex.service", "navig.reflex.executor"):
        try:
            module = importlib.import_module(module_name)
        except ImportError:
            continue
        if hasattr(module, "SubprocessRunner"):
            monkeypatch.setattr(
                module, "SubprocessRunner", lambda *a, **k: fake_runner, raising=False
            )
            patched.append(f"{module_name}.SubprocessRunner")

    fake.store = real_store
    return fake


@pytest.fixture
def endpoints_projection():
    """The pure projection contract from plan §2.3 (``project`` + ``validate_endpoint``)."""
    model = S.require("navig.endpoints.model")
    projection = S.require("navig.endpoints.projection")
    return projection, model
