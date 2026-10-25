"""What a failed plugin load says, and what it does not say.

An editable install pins a package to a checkout. Delete the checkout — a
worktree under ``.dev/worktrees`` is how it happens — and the distribution and
its entry point survive while the import resolves nothing. Every NAVIG command
then warned ``navig plugin 'router' failed to load: No module named
'navig_router'``: no distribution, no path, nothing to act on.

The hint names the distribution and, for an editable install, the checkout it
was installed from. It must stay attached to import failures only: a plugin that
raises on its own is the plugin's bug, and telling the operator to reinstall it
would send them the wrong way.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace

import pytest


def _dist(name: str, direct_url: dict | None) -> SimpleNamespace:
    return SimpleNamespace(
        name=name,
        read_text=lambda filename: None if direct_url is None else json.dumps(direct_url),
    )


def _ep(name: str, *, error: BaseException, dist=None) -> SimpleNamespace:
    def load() -> None:
        raise error

    return SimpleNamespace(name=name, dist=dist, load=load)


def _load(monkeypatch: pytest.MonkeyPatch, eps: list[SimpleNamespace]) -> None:
    import importlib.metadata

    from navig.core.plugins import load_entry_point_plugins

    monkeypatch.setattr(importlib.metadata, "entry_points", lambda group=None: eps)
    load_entry_point_plugins()


def _warnings(caplog: pytest.LogCaptureFixture) -> str:
    return "\n".join(r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING)


_EDITABLE = {
    "url": "file:///E:/projects/apps/navig/.dev/worktrees/distribution/plugins/navig-router",
    "dir_info": {"editable": True},
}


def test_an_editable_install_that_is_gone_names_the_checkout_and_the_way_out(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="navig.core.plugins")
    ep = _ep(
        "router",
        error=ModuleNotFoundError("No module named 'navig_router'"),
        dist=_dist("navig-router", _EDITABLE),
    )

    _load(monkeypatch, [ep])

    message = _warnings(caplog)
    assert "navig-router" in message, "the distribution is what the operator can act on"
    assert "E:/projects/apps/navig/.dev/worktrees/distribution/plugins/navig-router" in message
    assert "pip uninstall navig-router" in message


def test_a_plain_install_says_reinstall_or_remove_without_inventing_a_path(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="navig.core.plugins")
    ep = _ep(
        "tasks",
        error=ModuleNotFoundError("No module named 'navig_tasks'"),
        dist=_dist("navig-tasks", None),
    )

    _load(monkeypatch, [ep])

    message = _warnings(caplog)
    assert "navig-tasks" in message
    assert "pip uninstall navig-tasks" in message
    assert "editable" not in message


def test_a_non_editable_install_is_not_described_as_one(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="navig.core.plugins")
    ep = _ep(
        "router",
        error=ModuleNotFoundError("No module named 'navig_router'"),
        dist=_dist("navig-router", {"url": "file:///tmp/navig-router", "dir_info": {}}),
    )

    _load(monkeypatch, [ep])

    assert "installed editable" not in _warnings(caplog)


def test_a_plugin_that_raises_on_its_own_gets_no_install_advice(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="navig.core.plugins")
    ep = _ep("router", error=RuntimeError("boom"), dist=_dist("navig-router", _EDITABLE))

    _load(monkeypatch, [ep])

    message = _warnings(caplog)
    assert "boom" in message
    assert "pip uninstall" not in message, "a plugin's own crash is not a stale install"


def test_an_entry_point_without_a_distribution_falls_back_to_its_name() -> None:
    from navig.core.plugins import _load_hint

    assert "pip uninstall router" in _load_hint(SimpleNamespace(name="router", dist=None))
