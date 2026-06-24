"""Tests for navig.installer.planner — plan()."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from navig.installer.contracts import InstallerContext
from navig.installer.planner import plan
from navig.installer.profiles import VALID_PROFILES


class TestPlan:
    def test_unknown_profile_raises(self):
        ctx = InstallerContext(profile="no-such-profile")
        with pytest.raises(ValueError, match="Unknown installer profile"):
            plan(ctx)

    def test_error_mentions_valid_profiles(self):
        ctx = InstallerContext(profile="alien")
        with pytest.raises(ValueError) as exc_info:
            plan(ctx)
        msg = str(exc_info.value)
        for name in VALID_PROFILES:
            assert name in msg

    def test_unknown_module_emits_placeholder(self):
        """If a module doesn't exist, plan() adds a placeholder action, not raises."""
        ctx = InstallerContext(profile="node")
        # Patch _load_module to always raise ModuleNotFoundError
        with patch("navig.installer.planner._load_module", side_effect=ModuleNotFoundError("missing")):
            actions = plan(ctx)
        assert len(actions) > 0
        # All actions should be placeholders
        assert all(a.data.get("placeholder") for a in actions)

    def test_placeholder_action_has_correct_id_format(self):
        ctx = InstallerContext(profile="node")
        with patch("navig.installer.planner._load_module", side_effect=ModuleNotFoundError("x")):
            actions = plan(ctx)
        for a in actions:
            assert a.id.endswith(".placeholder")

    def test_returns_list(self):
        ctx = InstallerContext(profile="node")
        with patch("navig.installer.planner._load_module", side_effect=ModuleNotFoundError("x")):
            result = plan(ctx)
        assert isinstance(result, list)

    def test_successful_module_actions_merged(self):
        fake_action = MagicMock()
        fake_mod = MagicMock()
        fake_mod.plan.return_value = [fake_action, fake_action]

        ctx = InstallerContext(profile="node")
        with patch("navig.installer.planner._load_module", return_value=fake_mod):
            actions = plan(ctx)

        # node profile has 3 modules → 3×2 = 6 actions
        assert len(actions) == 6
