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
    prompt_selection,
    show_status,
)


class TestCommandHistory:
    """Test command history tracking."""

    def test_add_command(self):
        """Test adding commands to history."""
        history = CommandHistory(max_size=5)

        history.add("navig host list", "List hosts", True)
        history.add("navig sql SELECT 1", "Execute SQL", True)

        assert len(history.commands) == 2
        assert (
            history.commands[0]["command"] == "navig sql SELECT 1"
        )  # Most recent first
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


class TestPromptSelection:
    """Test user input prompting."""

    @patch("navig.commands.interactive.QUESTIONARY_AVAILABLE", False)
    @patch("navig.commands.interactive.Prompt.ask")
    @patch("navig.commands.interactive.console")
    def test_fallback_selection_valid(self, mock_console, mock_prompt):
        """Test fallback number-based selection with valid input."""
        options = ["Option 1", "Option 2", "Option 3"]
        mock_prompt.return_value = "2"

        result = prompt_selection(options, "Select option")

        assert result == "Option 2"
        mock_prompt.assert_called_once()

    @patch("navig.commands.interactive.QUESTIONARY_AVAILABLE", False)
    @patch("navig.commands.interactive.Prompt.ask")
    @patch("navig.commands.interactive.console")
    def test_fallback_selection_back(self, mock_console, mock_prompt):
        """Test fallback selection returning to previous menu."""
        options = ["Option 1", "Option 2"]
        mock_prompt.return_value = "0"

        result = prompt_selection(options, "Select option", allow_back=True)

        assert result is None

    @patch("navig.commands.interactive.QUESTIONARY_AVAILABLE", False)
    @patch("navig.commands.interactive.Prompt.ask")
    @patch("navig.commands.interactive.console")
    def test_fallback_selection_invalid_then_valid(self, mock_console, mock_prompt):
        """Test fallback selection with invalid then valid input."""
        options = ["Option 1", "Option 2"]
        mock_prompt.side_effect = ["99", "1"]  # Invalid, then valid

        result = prompt_selection(options, "Select option")

        assert result == "Option 1"
        assert mock_prompt.call_count == 2


class TestLaunchMenu:
    """Test main menu launch and error handling."""

    @patch("navig.commands.interactive.Console")
    def test_launch_menu_missing_rich(self, mock_console_class):
        """Test error handling when Rich is not available."""
        # Simulate ImportError for Rich
        with patch.dict("sys.modules", {"rich.console": None}):
            from navig.commands.interactive import launch_menu

            with pytest.raises(SystemExit) as exc_info:
                # This will fail the import check inside launch_menu
                try:
                    launch_menu({})
                except ImportError:
                    # Expected when Rich is not available
                    pass

    @patch("navig.commands.interactive.console")
    @patch("navig.commands.interactive.ConfigManager")
    @patch("navig.commands.interactive.show_main_menu")
    def test_launch_menu_terminal_size_warning(
        self, mock_main_menu, mock_config, mock_console
    ):
        """Test warning when terminal size is too small."""
        mock_console.width = 40  # Too small
        mock_console.height = 15  # Too small

        # Mock Confirm.ask to return False (don't continue)
        with patch("navig.commands.interactive.Confirm.ask", return_value=False):
            from navig.commands.interactive import launch_menu

            with pytest.raises(SystemExit):
                launch_menu({})


class TestHostExecutions:
    """Test host management command executions."""

    @patch("navig.commands.interactive.host")
    @patch("navig.commands.interactive.ConfigManager")
    def test_execute_host_list(self, mock_config, mock_host_module):
        """Test execute_host_list function."""
        from navig.commands.interactive import MenuState, execute_host_list

        mock_config_instance = Mock()
        state = MenuState(mock_config_instance)

        execute_host_list(state)

        mock_host_module.list_hosts.assert_called_once_with(
            {"all": True, "format": "table"}
        )
        assert len(state.history.commands) == 1
        assert state.history.commands[0]["success"] is True

    @patch("navig.commands.interactive.host")
    @patch("navig.commands.interactive.prompt_selection")
    @patch("navig.commands.interactive.ConfigManager")
    def test_execute_host_switch(self, mock_config, mock_prompt, mock_host_module):
        """Test execute_host_switch function."""
        from navig.commands.interactive import MenuState, execute_host_switch

        mock_config_instance = Mock()
        mock_config_instance.list_hosts.return_value = ["host1", "host2"]
        state = MenuState(mock_config_instance)

        mock_prompt.return_value = "host2"

        execute_host_switch(state)

        mock_host_module.use_host.assert_called_once_with("host2", {})
        assert state.active_host == "host2"
        assert len(state.history.commands) == 1


class TestDatabaseExecutions:
    """Test database operation command executions."""

    @patch("navig.commands.interactive.database")
    @patch("navig.commands.interactive.Prompt.ask")
    @patch("navig.commands.interactive.Confirm.ask")
    @patch("navig.commands.interactive.console")
    @patch("navig.commands.interactive.ConfigManager")
    def test_execute_sql_query_safe(
        self, mock_config, mock_console, mock_confirm, mock_prompt, mock_db_module
    ):
        """Test execute_sql_query with safe query."""
        from navig.commands.interactive import MenuState, execute_sql_query

        mock_config_instance = Mock()
        state = MenuState(mock_config_instance)

        mock_prompt.return_value = "SELECT * FROM users"

        execute_sql_query(state)

        mock_db_module.execute_sql.assert_called_once()
        mock_confirm.assert_not_called()  # No confirmation for safe queries

    @patch("navig.commands.interactive.database")
    @patch("navig.commands.interactive.Prompt.ask")
    @patch("navig.commands.interactive.Confirm.ask")
    @patch("navig.commands.interactive.console")
    @patch("navig.commands.interactive.ConfigManager")
    def test_execute_sql_query_destructive_cancelled(
        self, mock_config, mock_console, mock_confirm, mock_prompt, mock_db_module
    ):
        """Test execute_sql_query with destructive query that is cancelled."""
        from navig.commands.interactive import MenuState, execute_sql_query

        mock_config_instance = Mock()
        state = MenuState(mock_config_instance)

        mock_prompt.return_value = "DROP TABLE users"
        mock_confirm.return_value = False  # User cancels

        execute_sql_query(state)

        mock_db_module.execute_sql.assert_not_called()  # Should not execute
        mock_confirm.assert_called_once()  # Confirmation was prompted

    @patch("navig.commands.interactive.database")
    @patch("navig.commands.interactive.Prompt.ask")
    @patch("navig.commands.interactive.console")
    @patch("navig.commands.interactive.ConfigManager")
    def test_execute_db_backup(
        self, mock_config, mock_console, mock_prompt, mock_db_module
    ):
        """Test execute_db_backup function."""
        from navig.commands.interactive import MenuState, execute_db_backup

        mock_config_instance = Mock()
        state = MenuState(mock_config_instance)

        mock_prompt.return_value = "/tmp/backup.sql"

        execute_db_backup(state)

        mock_db_module.backup_database.assert_called_once()
        assert len(state.history.commands) == 1
        assert state.history.commands[0]["command"] == "navig backup"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
