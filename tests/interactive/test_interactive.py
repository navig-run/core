"""
Tests for Interactive Menu System

The Schema tests all operations. Every path. Every edge case.
"""

from unittest.mock import Mock, patch

import pytest

# Import interactive module components
from navig.commands.interactive import (
    CommandHistory,
    MenuState,
    clear_screen,
    show_status,
)

pytestmark = pytest.mark.integration


class TestCommandHistory:
    """Test command history tracking."""

    def test_add_command(self):
        """Test adding commands to history."""
        history = CommandHistory(max_size=5)

        history.add("navig host list", "List hosts", True)
        history.add("navig sql SELECT 1", "Execute SQL", True)

        assert len(history.commands) == 2
        assert history.commands[0]["command"] == "navig sql SELECT 1"  # Most recent first
        assert history.commands[1]["command"] == "navig host list"

    def test_max_size_limit(self):
        """Test history size limit."""
        history = CommandHistory(max_size=3)

        for i in range(5):
            history.add(f"command_{i}", f"Description {i}", True)

        assert len(history.commands) == 3
        assert history.commands[0]["command"] == "command_4"  # Most recent
        assert history.commands[2]["command"] == "command_2"  # Oldest kept

    def test_get_recent(self):
        """Test getting recent commands."""
        history = CommandHistory(max_size=10)

        for i in range(10):
            history.add(f"command_{i}", f"Description {i}", True)

        recent = history.get_recent(3)
        assert len(recent) == 3
        assert recent[0]["command"] == "command_9"
        assert recent[2]["command"] == "command_7"

    def test_clear_history(self):
        """Test clearing history."""
        history = CommandHistory()
        history.add("test", "Test command", True)

        assert len(history.commands) == 1

        history.clear()
        assert len(history.commands) == 0

    def test_success_tracking(self):
        """Test tracking command success/failure."""
        history = CommandHistory()

        history.add("success_cmd", "Success", True)
        history.add("fail_cmd", "Failure", False)

        assert history.commands[0]["success"] is False
        assert history.commands[1]["success"] is True


class TestMenuState:
    """Test menu state management."""

    @patch("navig.commands.interactive.ConfigManager")
    def test_initialization(self, mock_config_manager):
        """Test MenuState initialization."""
        mock_config = Mock()
        mock_config.get_active_host.return_value = "test-host"
        mock_config.get_active_app.return_value = "test-app"

        state = MenuState(mock_config)

        assert state.config_manager == mock_config
        assert state.active_host == "test-host"
        assert state.active_app == "test-app"
        assert len(state.menu_stack) == 0
        assert isinstance(state.history, CommandHistory)

    @patch("navig.commands.interactive.ConfigManager")
    def test_push_pop_menu(self, mock_config_manager):
        """Test menu stack navigation."""
        mock_config = Mock()
        mock_config.get_active_host.return_value = None
        mock_config.get_active_app.return_value = None

        state = MenuState(mock_config)

        state.push_menu("main")
        assert state.current_menu() == "main"

        state.push_menu("submenu")
        assert state.current_menu() == "submenu"

        popped = state.pop_menu()
        assert popped == "submenu"
        assert state.current_menu() == "main"

        state.pop_menu()
        assert state.current_menu() is None

    @patch("navig.commands.interactive.ConfigManager")
    def test_refresh_context(self, mock_config_manager):
        """Test refreshing active host/app from config."""
        mock_config = Mock()
        mock_config.get_active_host.return_value = "host1"
        mock_config.get_active_app.return_value = "app1"

        state = MenuState(mock_config)

        assert state.active_host == "host1"
        assert state.active_app == "app1"

        # Simulate config change
        mock_config.get_active_host.return_value = "host2"
        mock_config.get_active_app.return_value = "app2"

        state.refresh_context()

        assert state.active_host == "host2"
        assert state.active_app == "app2"


class TestInteractiveComponents:
    """Test interactive UI components."""

    @patch("navig.commands.interactive.console")
    def test_show_status_info(self, mock_console):
        """Test status display with info level."""
        show_status("Test message", "info")

        # Verify console.print was called with correct formatting
        mock_console.print.assert_called_once()
        call_args = mock_console.print.call_args[0][0]
        assert "Test message" in call_args
        assert "[*]" in call_args  # Info icon

    @patch("navig.commands.interactive.console")
    def test_show_status_error(self, mock_console):
        """Test status display with error level."""
        show_status("Error occurred", "error")

        mock_console.print.assert_called_once()
        call_args = mock_console.print.call_args[0][0]
        assert "Error occurred" in call_args
        assert "[x]" in call_args  # Error icon

    @patch("navig.commands.interactive.console")
    def test_show_status_success(self, mock_console):
        """Test status display with success level."""
        show_status("Operation successful", "success")

        mock_console.print.assert_called_once()
        call_args = mock_console.print.call_args[0][0]
        assert "Operation successful" in call_args
        assert "[+]" in call_args  # Success icon

    @patch("navig.commands.interactive.subprocess.run")
    def test_clear_screen_windows(self, mock_run):
        """Test clear screen on Windows."""
        with patch("navig.commands.interactive.os.name", "nt"):
            clear_screen()
            mock_run.assert_called_once_with(["cmd", "/c", "cls"], check=False)

    @patch("navig.commands.interactive.subprocess.run")
    def test_clear_screen_unix(self, mock_run):
        """Test clear screen on Unix/Linux."""
        with patch("navig.commands.interactive.os.name", "posix"):
            clear_screen()
            mock_run.assert_called_once_with(["clear"], check=False)

