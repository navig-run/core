"""An explicitly configured active host that cannot be verified is a STOP, not a fallback.

The incident (2026-09-15): `.navig/config.yaml` named `cybesis-vps`, another navig
process was rewriting the config directory at that second, `host_exists()` raised
and was read as "missing", and the resolution chain quietly fell through to the
GLOBAL config's `active_host` — a different LAN box. `navig run` executed there and
printed that box's output under the production host's name. Read-only that time.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from navig.core.context import ContextManager


class _Provider:
    """Just enough of ContextConfigProvider for get_active_host()."""

    def __init__(self, tmp: Path, *, existing: set[str], local_active: str | None, global_active: str | None):
        self._tmp = tmp
        self._existing = existing
        self._local = {"active_host": local_active} if local_active else {}
        self._global = {"active_host": global_active} if global_active else {}
        self.checked: list[str] = []

    @property
    def base_dir(self) -> Path:
        return self._tmp

    @property
    def active_host_file(self) -> Path:
        return self._tmp / "cache" / "active_host.txt"

    @property
    def active_app_file(self) -> Path:
        return self._tmp / "cache" / "active_app.txt"

    @property
    def global_config(self) -> dict[str, Any]:
        return self._global

    @property
    def verbose(self) -> bool:
        return False

    def host_exists(self, host_name: str) -> bool:
        self.checked.append(host_name)
        return host_name in self._existing

    def app_exists(self, host_name: str, app_name: str) -> bool:
        return False

    def list_apps(self, host_name: str) -> list:
        return []

    def load_host_config(self, host_name: str) -> dict[str, Any]:
        return {}

    def get_local_config(self, directory: Path | None = None) -> dict[str, Any]:
        return dict(self._local)

    def set_local_config(self, config: dict[str, Any], directory: Path | None = None) -> None:
        self._local = dict(config)


@pytest.fixture
def project_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / ".navig").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NAVIG_ACTIVE_HOST", raising=False)
    return tmp_path


def test_named_host_that_does_not_verify_stops_the_chain(project_dir: Path) -> None:
    # The project names cybesis-vps; the global config names navig-cloud; only
    # navig-cloud verifies right now. The old chain answered navig-cloud.
    p = _Provider(project_dir, existing={"navig-cloud"}, local_active="cybesis-vps", global_active="navig-cloud")
    ctx = ContextManager(p)

    assert ctx.get_active_host(return_source=True) == (None, "unresolvable")
    assert ctx.get_active_host() is None
    # And it did not even ask whether the fallback exists — it never got there.
    assert "navig-cloud" not in p.checked


def test_named_host_that_verifies_is_returned(project_dir: Path) -> None:
    p = _Provider(project_dir, existing={"cybesis-vps", "navig-cloud"}, local_active="cybesis-vps", global_active="navig-cloud")
    assert ContextManager(p).get_active_host(return_source=True) == ("cybesis-vps", "project")


def test_env_var_host_that_does_not_verify_stops_too(project_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("NAVIG_ACTIVE_HOST", "ghost")
    p = _Provider(project_dir, existing={"cybesis-vps"}, local_active="cybesis-vps", global_active=None)
    assert ContextManager(p).get_active_host(return_source=True) == (None, "unresolvable")


def test_nothing_named_still_falls_back_to_the_global_config(project_dir: Path) -> None:
    # No explicit choice anywhere → the compatibility fallback is legitimate.
    p = _Provider(project_dir, existing={"navig-cloud"}, local_active=None, global_active="navig-cloud")
    assert ContextManager(p).get_active_host(return_source=True) == ("navig-cloud", "config")


def test_cached_choice_that_does_not_verify_stops_the_chain(project_dir: Path) -> None:
    p = _Provider(project_dir, existing={"navig-cloud"}, local_active=None, global_active="navig-cloud")
    p.active_host_file.parent.mkdir(parents=True)
    p.active_host_file.write_text("cybesis-vps\n", encoding="utf-8")
    assert ContextManager(p).get_active_host(return_source=True) == (None, "unresolvable")
