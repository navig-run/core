"""
Tests for Interactive Menu Bug Fixes

This test file specifically tests the bug fixes for parameter passing
in the interactive menu system.
"""

from unittest.mock import Mock, patch

import pytest

from navig.commands.interactive import (
    MenuState,
    execute_app_edit,
    execute_host_clone,
    execute_host_edit,
    execute_host_info,
    execute_host_inspect,
    execute_host_test,
)
from navig.config import ConfigManager


@pytest.fixture
def mock_state():
    """Create a mock MenuState for testing."""
    mock_config = Mock(spec=ConfigManager)
    mock_config.list_hosts.return_value = ["host1", "host2", "pigkiss"]
    mock_config.list_apps.return_value = ["app1", "app2"]
    mock_config.get_active_host.return_value = "host1"
    mock_config.get_active_app.return_value = "app1"

    state = MenuState(mock_config)
    return state


class TestHostEditFix:
    """Test that host edit passes correct parameter."""

    @patch("navig.commands.interactive.prompt_selection")
    @patch("navig.commands.interactive.host.edit_host")
    def test_edit_host_passes_host_name_parameter(
        self, mock_edit, mock_prompt, mock_state
    ):
        """Test that execute_host_edit passes 'host_name' not 'name'."""
        mock_prompt.return_value = "pigkiss"

        execute_host_edit(mock_state)

        # Verify edit_host was called with correct parameter key
        mock_edit.assert_called_once()
        call_args = mock_edit.call_args[0][0]
        assert "host_name" in call_args
        assert call_args["host_name"] == "pigkiss"
        assert "name" not in call_args  # Should NOT have 'name' key


class TestHostCloneFix:
    """Test that host clone passes correct parameters."""

    @patch("navig.commands.interactive.Prompt.ask")
    @patch("navig.commands.interactive.prompt_selection")
    @patch("navig.commands.interactive.host.clone_host")
    def test_clone_host_passes_correct_parameters(
        self, mock_clone, mock_prompt, mock_ask, mock_state
    ):
        """Test that execute_host_clone passes 'source_name' and 'new_name'."""
        mock_prompt.return_value = "host1"
        mock_ask.return_value = "host1-clone"

        execute_host_clone(mock_state)

        # Verify clone_host was called with correct parameter keys
        mock_clone.assert_called_once()
        call_args = mock_clone.call_args[0][0]
        assert "source_name" in call_args
        assert "new_name" in call_args
        assert call_args["source_name"] == "host1"
        assert call_args["new_name"] == "host1-clone"
        assert "source" not in call_args  # Should NOT have 'source' key
        assert "new" not in call_args  # Should NOT have 'new' key


class TestHostTestFix:
    """Test that host test passes correct parameter."""

    @patch("navig.commands.interactive.console.status")
    @patch("navig.commands.interactive.prompt_selection")
    @patch("navig.commands.interactive.host.test_host")
    def test_test_host_passes_host_name_parameter(
        self, mock_test, mock_prompt, mock_status, mock_state
    ):
        """Test that execute_host_test passes 'host_name' not 'name'."""
        mock_prompt.return_value = "pigkiss"
        mock_status.return_value.__enter__ = Mock()
        mock_status.return_value.__exit__ = Mock()

        execute_host_test(mock_state)

        # Verify test_host was called with correct parameter key
        mock_test.assert_called_once()
        call_args = mock_test.call_args[0][0]
        assert "host_name" in call_args
        assert call_args["host_name"] == "pigkiss"
        assert "name" not in call_args  # Should NOT have 'name' key


class TestHostInspectFix:
    """Test that host inspect sets active host correctly."""

    @patch("navig.commands.interactive.console.status")
    @patch("navig.commands.interactive.prompt_selection")
    @patch("navig.commands.interactive.host.inspect_host")
    def test_inspect_host_sets_active_host(
        self, mock_inspect, mock_prompt, mock_status, mock_state
    ):
        """Test that execute_host_inspect sets active host before calling inspect_host."""
        mock_prompt.return_value = "pigkiss"
        mock_status.return_value.__enter__ = Mock()
        mock_status.return_value.__exit__ = Mock()

        original_active = mock_state.active_host

        execute_host_inspect(mock_state)

        # Verify set_active_host was called with selected host
        mock_state.config_manager.set_active_host.assert_called()

        # Verify inspect_host was called with silent=True (uses active host)
        mock_inspect.assert_called_once()
        call_args = mock_inspect.call_args[0][0]
        assert call_args == {"silent": True}  # Should pass silent=True


class TestHostInfoFix:
    """Test that host info passes correct parameter."""

    @patch("navig.commands.interactive.prompt_selection")
    @patch("navig.commands.interactive.host.info_host")
    def test_info_host_passes_host_name_parameter(
        self, mock_info, mock_prompt, mock_state
    ):
        """Test that execute_host_info passes 'host_name' not 'name'."""
        mock_prompt.return_value = "pigkiss"

        execute_host_info(mock_state)

        # Verify info_host was called with correct parameter key
        mock_info.assert_called_once()
        call_args = mock_info.call_args[0][0]
        assert "host_name" in call_args
        assert call_args["host_name"] == "pigkiss"
        assert "name" not in call_args  # Should NOT have 'name' key


class TestAppEditFix:
    """Test that app edit passes correct parameters."""

    @patch("navig.commands.interactive.prompt_selection")
    @patch("navig.commands.interactive.app.edit_app")
    def test_edit_app_passes_correct_parameters(
        self, mock_edit, mock_prompt, mock_state
    ):
        """Test that execute_app_edit passes 'app_name' and 'host'."""
        mock_prompt.return_value = "host1/app1"

        execute_app_edit(mock_state)

        # Verify edit_app was called with correct parameter keys
        mock_edit.assert_called_once()
        call_args = mock_edit.call_args[0][0]
        assert "app_name" in call_args
        assert "host" in call_args
        assert call_args["app_name"] == "app1"
        assert call_args["host"] == "host1"
        assert "name" not in call_args  # Should NOT have 'name' key
