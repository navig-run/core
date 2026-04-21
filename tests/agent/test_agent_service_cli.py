from __future__ import annotations

from typer.testing import CliRunner

from navig.commands.agent import agent_app
import pytest

pytestmark = pytest.mark.integration

runner = CliRunner()


def test_agent_start_background_exits_cleanly_with_guidance(monkeypatch, tmp_path):
    cfg_dir = tmp_path / "agent"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "config.yaml"
    cfg_path.write_text("agent: {}", encoding="utf-8")

    fake_cfg = type(
        "Cfg",
        (),
        {
            "enabled": True,
            "mode": "supervised",
            "personality": type("Personality", (), {"profile": "friendly"})(),
        },
    )()

    monkeypatch.setattr("navig.commands.agent._get_config_path", lambda: cfg_path)
    monkeypatch.setattr("navig.agent.AgentConfig.load", lambda _path: fake_cfg)

    result = runner.invoke(agent_app, ["start", "--background"])

    assert result.exit_code == 0
    assert "Background mode is not yet supported" in result.output
    assert "navig service start" in result.output


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
