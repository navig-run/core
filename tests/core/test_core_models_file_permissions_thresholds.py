"""Tests for core/models.py, core/file_permissions.py, core/thresholds.py — batch 55."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# core/models — Pydantic models
# ---------------------------------------------------------------------------


def test_command_parameter_defaults():
    from navig.core.models import CommandParameter

    p = CommandParameter(type="string", description="A param")
    assert p.type == "string"
    assert p.required is False
    assert p.default is None
    assert p.options is None


def test_command_parameter_with_options():
    from navig.core.models import CommandParameter

    p = CommandParameter(type="enum", description="Pick", options=["a", "b"])
    assert p.options == ["a", "b"]


def test_navig_command_defaults():
    from navig.core.models import NavigCommand

    cmd = NavigCommand(name="test", syntax="navig test", description="A test cmd")
    assert cmd.risk == "safe"
    assert cmd.confirmation_required is False
    assert cmd.parameters is None


def test_navig_command_custom_risk():
    from navig.core.models import NavigCommand

    cmd = NavigCommand(name="drop", syntax="navig drop", description="Drop db", risk="destructive")
    assert cmd.risk == "destructive"


def test_skill_manifest_defaults():
    from navig.core.models import SkillManifest

    sm = SkillManifest(name="my-skill", description="Test skill", version="1.0.0")
    assert sm.category == "uncategorized"
    assert sm.user_invocable is True
    assert sm.requires == []
    assert sm.tags == []


def test_skill_manifest_alias_population():
    from navig.core.models import SkillManifest

    sm = SkillManifest(
        **{
            "name": "my-skill",
            "description": "Test",
            "version": "1.0.0",
            "risk-level": "moderate",
            "user-invocable": False,
            "navig-commands": [],
        }
    )
    assert sm.risk_level == "moderate"
    assert sm.user_invocable is False


def test_skill_example_fields():
    from navig.core.models import SkillExample

    ex = SkillExample(user="deploy app", thought="I'll deploy", command="navig deploy")
    assert ex.user == "deploy app"
    assert ex.command == "navig deploy"


def test_pack_step_defaults():
    from navig.core.models import PackStep

    step = PackStep(command="ls -la")
    assert step.name == "unnamed-step"
    assert step.continue_on_error is False


def test_navig_pack_minimum():
    from navig.core.models import NavigPack

    pack = NavigPack(name="my-pack", description="A pack")
    assert pack.version == "1.0.0"
    assert pack.type == "runbook"
    assert pack.steps == []


# ---------------------------------------------------------------------------
# core/file_permissions — set_owner_only_file_permissions
# ---------------------------------------------------------------------------


def test_set_permissions_posix_chmod(tmp_path):
    """On POSIX, chmod 0o600 is called if os.name != 'nt'."""
    test_file = tmp_path / "secret.txt"
    test_file.write_text("secret")

    with patch("os.name", "posix"):
        import navig.core.file_permissions as fp
        with patch.object(fp.os, "chmod") as mock_chmod:
            fp.set_owner_only_file_permissions(test_file)
        mock_chmod.assert_called_once_with(str(test_file), 0o600)


def test_set_permissions_posix_oserror_silenced(tmp_path):
    """OSError during chmod is silenced."""
    test_file = tmp_path / "secret.txt"
    test_file.write_text("secret")

    with patch("os.name", "posix"):
        import navig.core.file_permissions as fp
        with patch.object(fp.os, "chmod", side_effect=PermissionError("denied")):
            fp.set_owner_only_file_permissions(test_file)  # must not raise


def test_set_permissions_windows_calls_icacls(tmp_path):
    """On Windows path, icacls subprocess calls are made."""
    test_file = tmp_path / "secret.txt"
    test_file.write_text("secret")

    with patch("os.name", "nt"):
        import navig.core.file_permissions as fp
        with (
            patch.object(fp.os, "name", "nt"),
            patch("subprocess.run") as mock_run,
            patch("getpass.getuser", return_value="testuser"),
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            fp.set_owner_only_file_permissions(test_file)
        assert mock_run.call_count >= 1


def test_set_permissions_accepts_string_path(tmp_path):
    """Accepts a string path as well as Path object."""
    test_file = tmp_path / "secret.txt"
    test_file.write_text("secret")

    with patch("os.name", "posix"):
        import navig.core.file_permissions as fp
        with patch.object(fp.os, "chmod") as mock_chmod:
            fp.set_owner_only_file_permissions(str(test_file))
        mock_chmod.assert_called_once()


# ---------------------------------------------------------------------------
# core/thresholds
# ---------------------------------------------------------------------------


def test_resolve_returns_threshold_for_known_metric():
    from navig.core.thresholds import resolve

    t = resolve("cpu_usage")
    assert t.warn_pct == 75.0
    assert t.crit_pct == 90.0


def test_resolve_disk_usage():
    from navig.core.thresholds import resolve

    t = resolve("disk_usage")
    assert t.warn_pct == 85.0
    assert t.crit_pct == 95.0


def test_resolve_unknown_returns_defaults():
    from navig.core.thresholds import resolve, DEFAULTS

    t = resolve("totally_unknown_metric_xyz")
    assert t == DEFAULTS


def test_defaults_values():
    from navig.core.thresholds import DEFAULTS

    assert DEFAULTS.warn_pct == 80.0
    assert DEFAULTS.crit_pct == 95.0


def test_threshold_is_frozen():
    from navig.core.thresholds import Threshold

    t = Threshold(warn_pct=50.0, crit_pct=75.0)
    with pytest.raises((AttributeError, TypeError)):
        t.warn_pct = 60.0  # type: ignore[misc]


def test_registry_contains_expected_keys():
    from navig.core.thresholds import REGISTRY

    expected = {"cpu_usage", "memory_usage", "disk_usage", "error_rate"}
    assert expected.issubset(set(REGISTRY.keys()))


def test_warn_lt_crit_for_all_registered():
    from navig.core.thresholds import REGISTRY

    for name, t in REGISTRY.items():
        assert t.warn_pct < t.crit_pct, f"{name}: warn_pct must be < crit_pct"
