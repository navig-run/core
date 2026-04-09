"""
Tests for Proactive Assistant System

Tests all four modules:
1. Auto-Detection & Analysis
2. Proactive Information Display
3. Intelligent Error Resolution
4. AI Copilot Integration
"""

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from navig.platform.paths import config_dir as get_navig_directory
from navig.proactive_assistant import ensure_navig_directory

from navig.modules.error_resolution import Solution
from navig.proactive_assistant import ProactiveAssistant


class TestAssistantUtils:
    """Test assistant utility functions."""

    def test_get_navig_directory(self):
        """Test getting NAVIG directory path."""
        navig_dir = get_navig_directory()
        assert navig_dir is not None
        assert isinstance(navig_dir, Path)

    def test_ensure_navig_directory(self):
        """Test directory creation."""
        navig_dir = ensure_navig_directory()
        assert navig_dir.exists()
        assert (navig_dir / "ai_context").exists()
        assert (navig_dir / "baselines").exists()

    def test_json_files_initialized(self):
        """Test that JSON files are initialized."""
        navig_dir = ensure_navig_directory()
        ai_context_dir = navig_dir / "ai_context"

        required_files = [
            "command_history.json",
            "error_log.json",
            "error_patterns.json",
            "solutions.json",
            "performance_baselines.json",
            "workflow_patterns.json",
            "detected_issues.json",
            "config_rules.json",
        ]

        for filename in required_files:
            file_path = ai_context_dir / filename
            assert file_path.exists(), f"{filename} should exist"


class TestAutoDetection:
    """Test Module 1: Auto-Detection & Analysis."""

    @pytest.fixture
    def assistant(self):
        """Create mock assistant."""
        config = Mock()
        config.global_config = {"proactive_assistant": {"enabled": True}}
        assistant = ProactiveAssistant(config)
        return assistant

    def test_log_command_execution(self, assistant):
        """Test command execution logging."""
        auto_detect = assistant.auto_detection

        auto_detect.log_command_execution(
            command="navig sql 'SELECT 1'",
            exit_code=0,
            stderr="",
            stdout="1",
            duration=0.5,
            context={"server": "test-server"},
        )

        # Check history file
        history_file = assistant.ai_context_dir / "command_history.json"
        assert history_file.exists()

        with open(history_file, "r") as f:
            history = json.load(f)

        assert len(history) > 0
        assert history[-1]["command"] == "navig sql 'SELECT 1'"
        assert history[-1]["exit_code"] == 0

    def test_categorize_error(self, assistant):
        """Test error categorization."""
        auto_detect = assistant.auto_detection

        assert auto_detect._categorize_error("Permission denied") == "permission"
        assert auto_detect._categorize_error("Connection refused") == "network"
        assert auto_detect._categorize_error("Disk full") == "resource_exhaustion"
        assert auto_detect._categorize_error("No such file") == "dependency_missing"
        assert auto_detect._categorize_error("Syntax error") == "syntax"

    def test_collect_performance_metrics(self, assistant):
        """Test performance metrics collection."""
        auto_detect = assistant.auto_detection

        # Mock remote operations
        remote_ops = Mock()
        remote_ops.execute_command = Mock(
            side_effect=[
                Mock(returncode=0, stdout="25.5"),  # CPU
                Mock(returncode=0, stdout="60.2"),  # Memory
                Mock(returncode=0, stdout="75"),  # Disk
            ]
        )

        # Mock server config
        server_config = {"name": "test", "host": "localhost", "user": "test"}

        metrics = auto_detect.collect_performance_metrics(remote_ops, server_config)

        assert "cpu_percent" in metrics
        assert "memory_percent" in metrics
        assert "disk_percent" in metrics
        assert metrics["status"] in ["normal", "warning", "critical"]


class TestProactiveDisplay:
    """Test Module 2: Proactive Information Display."""

    @pytest.fixture
    def assistant(self):
        """Create mock assistant."""
        config = Mock()
        config.global_config = {"proactive_assistant": {"enabled": True}}
        assistant = ProactiveAssistant(config)
        return assistant

    def test_is_destructive_operation(self, assistant):
        """Test destructive operation detection."""
        display = assistant.proactive_display

        assert display._is_destructive_operation("delete", {"recursive": True})
        assert display._is_destructive_operation("sql", {"query": "DROP TABLE users"})
        assert not display._is_destructive_operation("list", {})

    def test_check_pre_execution_warnings(self, assistant):
        """Test pre-execution warnings."""
        display = assistant.proactive_display

        should_proceed, warnings = display.check_pre_execution_warnings(
            command="delete",
            args={"remote": "/var/www/html", "recursive": True},
            context={"yes": False},
        )

        assert len(warnings) > 0
        assert not should_proceed  # Should require confirmation


class TestErrorResolution:
    """Test Module 3: Intelligent Error Resolution."""

    @pytest.fixture
    def assistant(self):
        """Create mock assistant."""
        config = Mock()
        config.global_config = {"proactive_assistant": {"enabled": True}}
        assistant = ProactiveAssistant(config)
        return assistant

    def test_solution_creation(self):
        """Test Solution object creation."""
        solution = Solution(
            description="Fix permissions",
            command="chmod 755 /var/www",
            success_rate=0.85,
            risk_level="medium",
        )

        assert solution.description == "Fix permissions"
        assert solution.success_rate == 0.85

        # Test serialization
        sol_dict = solution.to_dict()
        assert sol_dict["command"] == "chmod 755 /var/www"

        # Test deserialization
        solution2 = Solution.from_dict(sol_dict)
        assert solution2.description == solution.description

    def test_analyze_error(self, assistant):
        """Test error analysis."""
        error_res = assistant.error_resolution

        solutions = error_res.analyze_error(
            command="navig upload file.txt",
            exit_code=1,
            error_message="Permission denied",
            context={"server": "test"},
        )

        # Should return list of solutions (may be empty if no matches)
        assert isinstance(solutions, list)

    def test_record_solution_feedback(self, assistant):
        """Test solution feedback recording."""
        error_res = assistant.error_resolution

        error_res.record_solution_feedback(
            error_pattern="Permission denied",
            solution_command="chmod 755 /path",
            success=True,
            category="permission",
        )

        # Check solutions file
        solutions_file = assistant.ai_context_dir / "solutions.json"
        assert solutions_file.exists()


class TestContextGenerator:
    """Test Module 4: AI Copilot Integration."""

    @pytest.fixture
    def assistant(self):
        """Create mock assistant."""
        config = Mock()
        config.global_config = {"proactive_assistant": {"enabled": True}}
        config.get_active_server = Mock(
            return_value={"name": "test-server", "host": "10.0.0.10", "user": "admin"}
        )
        assistant = ProactiveAssistant(config)
        return assistant

    def test_generate_context_summary(self, assistant):
        """Test context summary generation."""
        ctx_gen = assistant.context_generator

        config = Mock()
        config.get_active_server = Mock(return_value="test-server")
        config.load_server_config = Mock(
            return_value={"name": "test-server", "host": "10.0.0.10", "user": "admin"}
        )

        context = ctx_gen.generate_context_summary(config, remote_ops=None)

        assert "generated_at" in context
        assert "server" in context
        assert "recent_operations" in context
        assert "context_summary" in context

    def test_context_summary_format(self, assistant):
        """Test that context summary is properly formatted."""
        ctx_gen = assistant.context_generator

        config = Mock()
        config.get_active_server = Mock(return_value="test-server")
        config.load_server_config = Mock(
            return_value={"name": "test-server", "host": "10.0.0.10", "user": "admin"}
        )

        context = ctx_gen.generate_context_summary(config)

        # Should be JSON serializable
        json_str = json.dumps(context)
        assert json_str is not None
