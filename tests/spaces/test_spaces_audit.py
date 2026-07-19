"""Tests for ``navig space audit`` — the cross-space integrity check.

Regression guard for the duplicate-space drift that accumulated real
``homelab`` beside ``homelab-space`` folders sharing one ``workspaceId``.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from navig.commands.space import space_app

pytestmark = pytest.mark.integration

runner = CliRunner()


def _write_events(space_dir: Path, workspace_id: str) -> None:
    space_dir.mkdir(parents=True, exist_ok=True)
    (space_dir / "events.jsonl").write_text(
        json.dumps({"id": "e1", "type": "SchedulerTick", "workspaceId": workspace_id}) + "\n",
        encoding="utf-8",
    )


def _point_config_at(monkeypatch, base: Path, spaces: Path) -> None:
    monkeypatch.setattr("navig.platform.paths.config_dir", lambda: base)
    import navig.spaces.resolver as _resolver

    monkeypatch.setattr(_resolver, "spaces_roots", lambda: [spaces])


def test_space_audit_flags_bare_pair_dupe_workspace_and_registry(tmp_path, monkeypatch):
    base = tmp_path
    spaces = base / "spaces"
    # bare + -space twin sharing one workspaceId — the exact drift we clean up
    _write_events(spaces / "homelab", "WID-1")
    _write_events(spaces / "homelab-space", "WID-1")
    # a clean, unique space that must NOT be flagged
    _write_events(spaces / "human-space", "WID-2")
    # registry: duplicate id + an orphan path
    (base / "spaces.json").write_text(
        json.dumps(
            {
                "version": 1,
                "active": None,
                "spaces": [
                    {"id": "dup", "path": str(spaces / "homelab"), "source": "global", "enabled": True},
                    {"id": "dup", "path": str(spaces / "homelab-space"), "source": "global", "enabled": True},
                    {"id": "gone", "path": str(spaces / "missing"), "source": "global", "enabled": True},
                ],
            }
        ),
        encoding="utf-8",
    )
    _point_config_at(monkeypatch, base, spaces)

    result = runner.invoke(space_app, ["audit", "--json"])
    assert result.exit_code == 1, result.stdout
    data = json.loads(result.stdout)

    assert {"root": str(spaces), "bare": "homelab", "spaced": "homelab-space"} in data[
        "bare_vs_space_pairs"
    ]
    assert "WID-1" in {d["workspace_id"] for d in data["duplicate_workspace_ids"]}
    assert "WID-2" not in {d["workspace_id"] for d in data["duplicate_workspace_ids"]}
    assert any(d["id"] == "dup" for d in data["duplicate_registry_ids"])
    assert any(o["path"].endswith("missing") for o in data["orphan_registry_paths"])
    assert data["issue_count"] >= 4


def test_space_audit_clean_passes(tmp_path, monkeypatch):
    base = tmp_path
    spaces = base / "spaces"
    _write_events(spaces / "homelab-space", "WID-9")
    _write_events(spaces / "human-space", "WID-8")
    (base / "spaces.json").write_text(
        json.dumps(
            {
                "version": 1,
                "active": None,
                "spaces": [
                    {
                        "id": "homelab-space",
                        "path": str(spaces / "homelab-space"),
                        "source": "global",
                        "enabled": True,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    _point_config_at(monkeypatch, base, spaces)

    result = runner.invoke(space_app, ["audit"])
    assert result.exit_code == 0, result.stdout
    assert "clean" in result.stdout.lower()
