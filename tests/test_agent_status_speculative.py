"""Regression tests for `navig agent status` speculative telemetry output."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from navig.commands.agent import agent_status


def test_agent_status_plain_includes_speculative(monkeypatch, capsys, tmp_path: Path):
    cfg_dir = tmp_path / "agent"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "config.yaml"
    cfg_path.write_text("agent: {}", encoding="utf-8")

    monkeypatch.setattr("navig.commands.agent._get_agent_config_dir", lambda: cfg_dir)
    monkeypatch.setattr("navig.commands.agent._get_config_path", lambda: cfg_path)

    fake_cfg = SimpleNamespace(
        enabled=True,
        mode="supervised",
        personality=SimpleNamespace(profile="friendly"),
    )
    monkeypatch.setattr("navig.agent.AgentConfig.load", lambda _path: fake_cfg)
    monkeypatch.setattr(
        "navig.agent.speculative.get_speculative_runtime_snapshot",
        lambda: {
            "enabled": True,
            "has_live_executor": False,
            "effective": {"min_hit_rate": 0.2},
            "live": None,
        },
    )

    agent_status(plain=True)
    out = capsys.readouterr().out.strip()
    payload = json.loads(out)

    assert payload["installed"] is True
    assert payload["enabled"] is True
    assert "speculative" in payload
    assert payload["speculative"]["enabled"] is True
