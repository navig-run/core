"""``PluginAPI``'s remote surface — the whole of it was dead on arrival.

Every plugin reaches remote hosts through ``PluginAPI.run_remote`` / ``upload_file`` /
``download_file``. All three built ``RemoteOperations(host_config)`` — the constructor
takes a **ConfigManager**, and the host config is an argument to each call — and then
invoked ``.execute()`` / ``.upload()`` / ``.download()``, none of which exist (the real
names are ``execute_command`` / ``upload_file`` / ``download_file``). ``run_remote`` then
read ``result.success`` / ``.stdout`` / ``.stderr`` from what is a
``subprocess.CompletedProcess``.

Each call site sits in ``except Exception as e: return (False, …, str(e))``, so the
AttributeError was reported as an ordinary remote failure. A plugin author saw
``(False, "", "'RemoteOperations' object has no attribute 'execute'")`` and would
reasonably conclude their SSH config was wrong.

The boundary is patched with ``create_autospec``: a plain MagicMock happily invents
``.execute()`` and would keep this bug green forever.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import create_autospec, patch

import pytest

from navig.plugins.base import PluginAPI
from navig.remote import RemoteOperations

HOST = {"name": "web1", "host": "10.0.0.5", "user": "deploy"}


def _completed(returncode: int = 0, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["ssh"], returncode=returncode, stdout=stdout, stderr=stderr)


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> PluginAPI:
    with patch("navig.core.Config"):
        instance = PluginAPI()
    monkeypatch.setattr(instance, "get_active_host", lambda: "web1")
    monkeypatch.setattr(instance, "get_host_config", lambda host_name=None: HOST)
    return instance


@pytest.fixture
def remote():
    """Autospec'd RemoteOperations — calling a method it doesn't have raises."""
    fake = create_autospec(RemoteOperations, instance=True)
    with patch("navig.remote.RemoteOperations", return_value=fake), patch(
        "navig.config.get_config_manager"
    ):
        yield fake


def test_run_remote_returns_stdout_on_success(api: PluginAPI, remote) -> None:
    remote.execute_command.return_value = _completed(0, stdout="total 0\n")

    ok, stdout, stderr = api.run_remote("ls -la")

    assert (ok, stdout, stderr) == (True, "total 0\n", "")


def test_run_remote_passes_the_host_config_as_an_argument(api: PluginAPI, remote) -> None:
    """The host config belongs in the call, not the constructor."""
    remote.execute_command.return_value = _completed(0)

    api.run_remote("uptime")

    args, kwargs = remote.execute_command.call_args
    assert args[0] == "uptime"
    assert args[1] is HOST


def test_run_remote_reports_a_nonzero_exit_as_failure(api: PluginAPI, remote) -> None:
    """CompletedProcess has returncode, not .success — reading .success is how this hid."""
    remote.execute_command.return_value = _completed(1, stderr="No such file\n")

    ok, _stdout, stderr = api.run_remote("cat /nope")

    assert ok is False
    assert stderr == "No such file\n"


def test_run_remote_honours_the_documented_timeout(api: PluginAPI, remote) -> None:
    remote.execute_command.return_value = _completed(0)

    api.run_remote("sleep 1", timeout=5)

    assert remote.execute_command.call_args.kwargs["timeout"] == 5


def test_run_remote_surfaces_a_transport_error(api: PluginAPI, remote) -> None:
    remote.execute_command.side_effect = RuntimeError("SSH connection timed out")

    ok, _stdout, stderr = api.run_remote("ls")

    assert ok is False
    assert "SSH connection timed out" in stderr


def test_run_remote_without_an_active_host_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    with patch("navig.core.Config"):
        instance = PluginAPI()
    monkeypatch.setattr(instance, "get_active_host", lambda: None)

    assert instance.run_remote("ls") == (False, "", "No active host")


def test_upload_file_calls_the_real_api_and_reports_success(api: PluginAPI, remote, tmp_path: Path) -> None:
    remote.upload_file.return_value = True
    local = tmp_path / "app.tar.gz"
    local.write_text("x", encoding="utf-8")

    assert api.upload_file(str(local), "/srv/app.tar.gz") == (True, "")

    args, _ = remote.upload_file.call_args
    assert args[0] == Path(str(local))
    assert args[1] == "/srv/app.tar.gz"
    assert args[2] is HOST


def test_upload_file_reports_a_failed_transfer(api: PluginAPI, remote, tmp_path: Path) -> None:
    """upload_file returns a bool — ignoring it made every failure look like success."""
    remote.upload_file.return_value = False

    ok, error = api.upload_file(str(tmp_path / "app.tar.gz"), "/srv/app.tar.gz")

    assert ok is False
    assert error


def test_download_file_calls_the_real_api(api: PluginAPI, remote, tmp_path: Path) -> None:
    remote.download_file.return_value = True
    local = tmp_path / "out.log"

    assert api.download_file("/var/log/app.log", str(local)) == (True, "")

    args, _ = remote.download_file.call_args
    assert args[0] == "/var/log/app.log"
    assert args[1] == Path(str(local))
    assert args[2] is HOST


def test_download_file_reports_a_failed_transfer(api: PluginAPI, remote, tmp_path: Path) -> None:
    remote.download_file.return_value = False

    ok, error = api.download_file("/var/log/app.log", str(tmp_path / "out.log"))

    assert ok is False
    assert error


def test_remote_operations_still_has_the_api_this_depends_on() -> None:
    """Pin the contract, so a rename upstream fails here instead of at a user's terminal."""
    for name in ("execute_command", "upload_file", "download_file"):
        assert hasattr(RemoteOperations, name)
    for gone in ("execute", "upload", "download"):
        assert not hasattr(RemoteOperations, gone)
