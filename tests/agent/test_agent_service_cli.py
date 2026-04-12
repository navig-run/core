from __future__ import annotations

from typer.testing import CliRunner

from navig.commands.agent import agent_app
import pytest

pytestmark = pytest.mark.integration

runner = CliRunner()


def test_agent_service_install_no_start_invokes_service_installer_once(monkeypatch):
    calls: list[bool] = []

    class FakeServiceInstaller:
        def install(self, start_now=True):
            calls.append(bool(start_now))
            return True, "installed"

        def uninstall(self):
            return True, "uninstalled"

        def status(self):
            return True, "running"

    monkeypatch.setattr("navig.agent.service.ServiceInstaller", FakeServiceInstaller)

    result = runner.invoke(agent_app, ["service", "install", "--no-start"])

    assert result.exit_code == 0
    assert calls == [False]
