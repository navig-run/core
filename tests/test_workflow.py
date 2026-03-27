"""Tests for Workflow System"""

import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from navig.commands.workflow import Workflow, WorkflowManager, WorkflowStep

# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def temp_config_dir():
    """Create temporary config directory for testing."""
    temp_dir = tempfile.mkdtemp()
    workflows_dir = Path(temp_dir) / "workflows"
    workflows_dir.mkdir()
    yield Path(temp_dir)
    shutil.rmtree(temp_dir)


@pytest.fixture
def mock_config_manager(temp_config_dir):
    """Create mock config manager with temp directories."""
    manager = MagicMock()
    manager.global_config_dir = temp_config_dir
    manager.app_config_dir = None
    return manager


@pytest.fixture
def sample_workflow_yaml():
    """Sample workflow YAML content."""
    return {
        "name": "Test Workflow",
        "description": "A test workflow for unit tests",
        "version": "1.0",
        "author": "Test Author",
        "variables": {"host": "production", "app": "myapp"},
        "steps": [
            {
                "name": "Step 1",
                "command": "host use ${host}",
                "description": "Set active host",
            },
            {
                "name": "Step 2",
                "command": "run 'echo ${app}'",
                "continue_on_error": True,
            },
            {"name": "Step 3", "command": "health", "prompt": "Continue?"},
        ],
    }


@pytest.fixture
def workflow_file(temp_config_dir, sample_workflow_yaml):
    """Create a sample workflow file."""
    workflows_dir = temp_config_dir / "workflows"
    file_path = workflows_dir / "test-workflow.yaml"
    with open(file_path, "w") as f:
        yaml.dump(sample_workflow_yaml, f)
    return file_path


# ============================================================================
# DATA CLASS TESTS
# ============================================================================


class TestWorkflowStep:
    """Tests for WorkflowStep dataclass."""

    def test_basic_step(self):
        """Test basic step creation."""
        step = WorkflowStep(name="Test", command="host list")
        assert step.name == "Test"
        assert step.command == "host list"
        assert step.description == ""
        assert step.prompt == ""
        assert step.continue_on_error is False
        assert step.skip_on_error is False

    def test_step_with_options(self):
        """Test step with all options."""
        step = WorkflowStep(
            name="Test",
            command="run 'command'",
            description="A description",
            prompt="Proceed?",
            continue_on_error=True,
            skip_on_error=True,
        )
        assert step.description == "A description"
        assert step.prompt == "Proceed?"
        assert step.continue_on_error is True
        assert step.skip_on_error is True

    def test_step_to_dict(self):
        """Test step serialization."""
        step = WorkflowStep(
            name="Test", command="host list", description="Desc", continue_on_error=True
        )
        data = step.to_dict()
        assert data["name"] == "Test"
        assert data["command"] == "host list"
        assert data["description"] == "Desc"
        assert data["continue_on_error"] is True
        assert "skip_on_error" not in data  # False values not included
        assert "prompt" not in data  # Empty values not included


class TestWorkflow:
    """Tests for Workflow dataclass."""

    def test_basic_workflow(self):
        """Test basic workflow creation."""
        wf = Workflow(name="Test Workflow")
        assert wf.name == "Test Workflow"
        assert wf.description == ""
        assert wf.variables == {}
        assert wf.steps == []
        assert wf.version == "1.0"

    def test_workflow_with_steps(self):
        """Test workflow with steps."""
        steps = [
            WorkflowStep(name="Step 1", command="cmd1"),
            WorkflowStep(name="Step 2", command="cmd2"),
        ]
        wf = Workflow(
            name="Test", description="A test", variables={"host": "prod"}, steps=steps
        )
        assert len(wf.steps) == 2
        assert wf.variables["host"] == "prod"

    def test_workflow_to_dict(self):
        """Test workflow serialization."""
        wf = Workflow(
            name="Test",
            description="Desc",
            version="2.0",
            author="Author",
            variables={"var1": "val1"},
            steps=[WorkflowStep(name="S1", command="c1")],
        )
        data = wf.to_dict()
        assert data["name"] == "Test"
        assert data["description"] == "Desc"
        assert data["version"] == "2.0"
        assert data["author"] == "Author"
        assert data["variables"]["var1"] == "val1"
        assert len(data["steps"]) == 1


# ============================================================================
# WORKFLOW MANAGER TESTS
# ============================================================================


class TestWorkflowManagerDiscovery:
    """Tests for workflow discovery."""

    def test_discover_workflows(self, mock_config_manager, workflow_file):
        """Test workflow discovery finds files."""
        manager = WorkflowManager(mock_config_manager)
        workflows = manager.discover_workflows()
        assert "test-workflow" in workflows
        assert workflows["test-workflow"] == workflow_file

    def test_discover_empty_directory(self, mock_config_manager):
        """Test discovery with no workflows."""
        manager = WorkflowManager(mock_config_manager)
        workflows = manager.discover_workflows()
        # May have builtin workflows
        assert isinstance(workflows, dict)

    def test_get_workflow_source_global(self, mock_config_manager, workflow_file):
        """Test source identification for global workflows."""
        manager = WorkflowManager(mock_config_manager)
        source = manager.get_workflow_source(workflow_file)
        assert source == "global"


class TestWorkflowManagerLoading:
    """Tests for workflow loading and parsing."""

    def test_load_workflow(self, mock_config_manager, workflow_file):
        """Test loading a workflow file."""
        manager = WorkflowManager(mock_config_manager)
        manager.discover_workflows()
        workflow = manager.load_workflow("test-workflow")

        assert workflow is not None
        assert workflow.name == "Test Workflow"
        assert workflow.description == "A test workflow for unit tests"
        assert len(workflow.steps) == 3
        assert workflow.variables["host"] == "production"

    def test_load_nonexistent_workflow(self, mock_config_manager):
        """Test loading non-existent workflow returns None."""
        manager = WorkflowManager(mock_config_manager)
        manager.discover_workflows()
        workflow = manager.load_workflow("nonexistent")
        assert workflow is None

    def test_load_invalid_yaml(self, mock_config_manager, temp_config_dir):
        """Test loading invalid YAML file."""
        workflows_dir = temp_config_dir / "workflows"
        bad_file = workflows_dir / "bad.yaml"
        bad_file.write_text("invalid: yaml: content: [")

        manager = WorkflowManager(mock_config_manager)
        manager.discover_workflows()
        workflow = manager.load_workflow("bad")
        assert workflow is None


class TestWorkflowValidation:
    """Tests for workflow validation."""

    def test_validate_valid_workflow(self, mock_config_manager, workflow_file):
        """Test validation of valid workflow."""
        manager = WorkflowManager(mock_config_manager)
        manager.discover_workflows()
        workflow = manager.load_workflow("test-workflow")
        errors = manager.validate_workflow(workflow)
        assert len(errors) == 0

    def test_validate_empty_workflow(self):
        """Test validation catches empty workflow."""
        manager = WorkflowManager()
        workflow = Workflow(name="", steps=[])
        errors = manager.validate_workflow(workflow)
        assert "Workflow name is required" in errors
        assert "Workflow must have at least one step" in errors

    def test_validate_missing_command(self):
        """Test validation catches step without command."""
        manager = WorkflowManager()
        workflow = Workflow(
            name="Test", steps=[WorkflowStep(name="Empty Step", command="")]
        )
        errors = manager.validate_workflow(workflow)
        assert any("has no command" in e for e in errors)

    def test_validate_undefined_variable(self):
        """Test validation catches undefined variables."""
        manager = WorkflowManager()
        workflow = Workflow(
            name="Test",
            variables={"defined": "value"},
            steps=[WorkflowStep(name="Step", command="run ${undefined}")],
        )
        errors = manager.validate_workflow(workflow)
        assert any("undefined variable" in e for e in errors)


class TestVariableSubstitution:
    """Tests for variable substitution."""

    def test_substitute_simple(self):
        """Test simple variable substitution."""
        manager = WorkflowManager()
        text = "host use ${host}"
        result = manager.substitute_variables(text, {"host": "production"})
        assert result == "host use production"

    def test_substitute_multiple(self):
        """Test multiple variable substitution."""
        manager = WorkflowManager()
        text = "deploy ${app} to ${host}"
        result = manager.substitute_variables(text, {"app": "myapp", "host": "prod"})
        assert result == "deploy myapp to prod"

    def test_substitute_with_override(self):
        """Test variable override."""
        manager = WorkflowManager()
        text = "host use ${host}"
        result = manager.substitute_variables(
            text, {"host": "default"}, extra_vars={"host": "override"}
        )
        assert result == "host use override"

    def test_substitute_undefined_left_as_is(self):
        """Test undefined variables are left unchanged."""
        manager = WorkflowManager()
        text = "run ${undefined}"
        result = manager.substitute_variables(text, {})
        assert result == "run ${undefined}"


class TestWorkflowCreation:
    """Tests for workflow creation and deletion."""

    def test_create_workflow(self, mock_config_manager, temp_config_dir):
        """Test creating new workflow."""
        manager = WorkflowManager(mock_config_manager)
        path = manager.create_workflow("new-workflow", global_scope=True)

        assert path is not None
        assert path.exists()
        assert path.name == "new-workflow.yaml"

        # Verify content
        with open(path) as f:
            data = yaml.safe_load(f)
        assert data["name"] == "New Workflow"
        assert "steps" in data

    def test_create_duplicate_workflow(self, mock_config_manager, workflow_file):
        """Test creating duplicate workflow fails."""
        manager = WorkflowManager(mock_config_manager)
        path = manager.create_workflow("test-workflow", global_scope=True)
        assert path is None

    def test_delete_workflow(self, mock_config_manager, workflow_file):
        """Test deleting workflow."""
        manager = WorkflowManager(mock_config_manager)
        manager.discover_workflows()

        assert workflow_file.exists()
        result = manager.delete_workflow("test-workflow")
        assert result is True
        assert not workflow_file.exists()

    def test_delete_nonexistent_workflow(self, mock_config_manager):
        """Test deleting non-existent workflow fails."""
        manager = WorkflowManager(mock_config_manager)
        manager.discover_workflows()
        result = manager.delete_workflow("nonexistent")
        assert result is False


# ============================================================================
# CLI INTEGRATION TESTS
# ============================================================================


class TestWorkflowCLI:
    """Tests for workflow CLI commands."""

    def test_workflow_list_import(self):
        """Test workflow list function can be imported."""
        from navig.commands.workflow import list_workflows

        assert callable(list_workflows)

    def test_workflow_show_import(self):
        """Test workflow show function can be imported."""
        from navig.commands.workflow import show_workflow

        assert callable(show_workflow)

    def test_workflow_run_import(self):
        """Test workflow run function can be imported."""
        from navig.commands.workflow import run_workflow

        assert callable(run_workflow)

    def test_workflow_validate_import(self):
        """Test workflow validate function can be imported."""
        from navig.commands.workflow import validate_workflow

        assert callable(validate_workflow)

    def test_workflow_create_import(self):
        """Test workflow create function can be imported."""
        from navig.commands.workflow import create_workflow

        assert callable(create_workflow)

    def test_workflow_delete_import(self):
        """Test workflow delete function can be imported."""
        from navig.commands.workflow import delete_workflow

        assert callable(delete_workflow)


# ============================================================================
# BUILTIN WORKFLOW TESTS
# ============================================================================


class TestBuiltinWorkflows:
    """Tests for built-in workflow files."""

    def test_builtin_workflows_exist(self):
        """Test that built-in workflows are discoverable."""
        manager = WorkflowManager()
        workflows = manager.discover_workflows()

        # Check for expected builtins
        expected = [
            "safe-deployment",
            "db-snapshot",
            "emergency-debug",
            "server-health",
        ]
        for name in expected:
            assert name in workflows, f"Built-in workflow '{name}' not found"

    def test_builtin_workflows_valid(self):
        """Test that all built-in workflows are valid."""
        manager = WorkflowManager()
        manager.discover_workflows()

        for name in [
            "safe-deployment",
            "db-snapshot",
            "emergency-debug",
            "server-health",
        ]:
            workflow = manager.load_workflow(name)
            assert workflow is not None, f"Failed to load '{name}'"

            errors = manager.validate_workflow(workflow)
            assert (
                len(errors) == 0
            ), f"Workflow '{name}' has validation errors: {errors}"

    def test_builtin_safe_deployment(self):
        """Test safe-deployment workflow structure."""
        manager = WorkflowManager()
        manager.discover_workflows()
        workflow = manager.load_workflow("safe-deployment")

        assert workflow is not None
        assert "host" in workflow.variables
        assert len(workflow.steps) >= 5

    def test_builtin_db_snapshot(self):
        """Test db-snapshot workflow structure."""
        manager = WorkflowManager()
        manager.discover_workflows()
        workflow = manager.load_workflow("db-snapshot")

        assert workflow is not None
        assert "db_name" in workflow.variables
        assert any("dump" in step.command.lower() for step in workflow.steps)

    def test_builtin_emergency_debug(self):
        """Test emergency-debug workflow structure."""
        manager = WorkflowManager()
        manager.discover_workflows()
        workflow = manager.load_workflow("emergency-debug")

        assert workflow is not None
        assert "service" in workflow.variables
        # Most steps should continue on error
        continue_steps = sum(1 for s in workflow.steps if s.continue_on_error)
        assert continue_steps >= 5
