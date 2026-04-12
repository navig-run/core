"""Tests for Template Manager"""

import json
import shutil
import tempfile
from pathlib import Path

import pytest

from navig.template_manager import Template, TemplateManager, TemplateSchema

pytestmark = pytest.mark.integration

# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def temp_template_dir():
    """Create temporary template directory structure."""
    # Use custom temp directory to avoid Windows permission issues
    temp_base = Path(tempfile.gettempdir()) / "navig_test_templates"
    temp_base.mkdir(exist_ok=True)

    template_dir = temp_base / f"test_{id(object())}"
    template_dir.mkdir()

    yield template_dir

    # Cleanup
    try:
        shutil.rmtree(template_dir)
    except (OSError, PermissionError):
        pass  # Cleanup failures are acceptable in tests


@pytest.fixture
def sample_template_metadata():
    """Sample valid template metadata."""
    return {
        "name": "test-template",
        "version": "1.0.0",
        "description": "Test template for unit tests",
        "author": "test-user",
        "enabled": False,
        "dependencies": [],
        "paths": {"app_root": "/var/www/test"},
        "services": {"web": "nginx"},
        "commands": [
            {
                "name": "test-command",
                "description": "Test command",
                "command": "echo test",
            }
        ],
    }


@pytest.fixture
def create_template_files(temp_template_dir, sample_template_metadata):
    """Create template files in temp directory."""

    def _create(name, metadata=None):
        template_path = temp_template_dir / name
        template_path.mkdir()

        if metadata is None:
            metadata = sample_template_metadata.copy()
            metadata["name"] = name

        template_json = template_path / "template.json"
        template_json.write_text(json.dumps(metadata, indent=2))

        return template_path

    return _create


# ============================================================================
# TEMPLATE SCHEMA TESTS
# ============================================================================


def test_template_schema_valid():
    """Test TemplateSchema validates correct metadata."""
    metadata = {
        "name": "test",
        "version": "1.0.0",
        "description": "Test",
        "author": "test",
    }
    is_valid, error = TemplateSchema.validate(metadata)
    assert is_valid
    assert error is None


def test_template_schema_missing_required():
    """Test TemplateSchema rejects missing required fields."""
    metadata = {
        "name": "test",
        "version": "1.0.0",
        # Missing description and author
    }
    is_valid, error = TemplateSchema.validate(metadata)
    assert not is_valid
    assert "Missing required field" in error


def test_template_schema_all_fields():
    """Test TemplateSchema with all optional fields."""
    metadata = {
        "name": "test",
        "version": "1.0.0",
        "description": "Test",
        "author": "test",
        "enabled": True,
        "dependencies": ["other-template"],
        "paths": {"root": "/path"},
        "services": {"web": "nginx"},
        "commands": [{"name": "cmd", "description": "desc", "command": "echo"}],
        "env_vars": {"VAR": "value"},
        "hooks": {"onEnable": "enable.sh", "onDisable": "disable.sh"},
        "api": {"endpoint": "http://localhost"},
    }
    is_valid, error = TemplateSchema.validate(metadata)
    assert is_valid
    assert error is None


# ============================================================================
# TEMPLATE CLASS TESTS
# ============================================================================


def test_template_initialization(create_template_files):
    """Test Template class initialization."""
    template_path = create_template_files("test-template")
    template = Template(template_path)

    assert template.name == "test-template"
    assert template.metadata["name"] == "test-template"
    assert template.metadata["version"] == "1.0.0"


def test_template_enable_disable(create_template_files):
    """Test enabling and disabling template."""
    template_path = create_template_files("test-template")
    template = Template(template_path)

    # Initially disabled
    assert not template.is_enabled()

    # Enable
    template.enable()
    assert template.is_enabled()

    # Disable
    template.disable()
    assert not template.is_enabled()


def test_template_get_paths(create_template_files, sample_template_metadata):
    """Test getting template paths."""
    template_path = create_template_files("test-template", sample_template_metadata)
    template = Template(template_path)

    paths = template.get_paths()
    assert paths == {"app_root": "/var/www/test"}


def test_template_get_services(create_template_files, sample_template_metadata):
    """Test getting template services."""
    template_path = create_template_files("test-template", sample_template_metadata)
    template = Template(template_path)

    services = template.get_services()
    assert services == {"web": "nginx"}


def test_template_get_commands(create_template_files, sample_template_metadata):
    """Test getting template commands."""
    template_path = create_template_files("test-template", sample_template_metadata)
    template = Template(template_path)

    commands = template.get_commands()
    assert len(commands) == 1
    assert commands[0]["name"] == "test-command"


def test_template_check_dependencies(create_template_files):
    """Test dependency checking."""
    metadata = {
        "name": "dependent-template",
        "version": "1.0.0",
        "description": "Depends on other template",
        "author": "test",
        "dependencies": ["other-template"],
    }
    template_path = create_template_files("dependent-template", metadata)
    template = Template(template_path)

    # With missing dependency
    is_valid, missing = template.check_dependencies([])
    assert not is_valid
    assert "other-template" in missing

    # With dependency present
    is_valid, missing = template.check_dependencies(["other-template"])
    assert is_valid
    assert len(missing) == 0


# ============================================================================
# TEMPLATE MANAGER TESTS
# ============================================================================


def test_template_manager_discover(create_template_files, temp_template_dir):
    """Test template discovery."""
    create_template_files("template1")
    create_template_files("template2")
    create_template_files("template3")

    manager = TemplateManager(temp_template_dir)
    manager.discover_templates()

    assert len(manager.templates) == 3
    assert "template1" in manager.templates
    assert "template2" in manager.templates
    assert "template3" in manager.templates


def test_template_manager_enable_template(create_template_files, temp_template_dir):
    """Test enabling template."""
    create_template_files("test-template")

    manager = TemplateManager(temp_template_dir)
    manager.discover_templates()

    manager.enable_template("test-template")

    template = manager.get_template("test-template")
    assert template.is_enabled()


def test_template_manager_disable_template(create_template_files, temp_template_dir):
    """Test disabling template."""
    metadata = {
        "name": "test-template",
        "version": "1.0.0",
        "description": "Test",
        "author": "test",
        "enabled": True,  # Start enabled
    }
    create_template_files("test-template", metadata)

    manager = TemplateManager(temp_template_dir)
    manager.discover_templates()

    manager.disable_template("test-template")

    template = manager.get_template("test-template")
    assert not template.is_enabled()


def test_template_manager_toggle_template(create_template_files, temp_template_dir):
    """Test toggling template state."""
    create_template_files("test-template")

    manager = TemplateManager(temp_template_dir)
    manager.discover_templates()

    template = manager.get_template("test-template")
    initial_state = template.is_enabled()

    manager.toggle_template("test-template")

    assert template.is_enabled() == (not initial_state)


def test_template_manager_dependency_validation(create_template_files, temp_template_dir):
    """Test dependency validation during enable."""
    # Create template with dependency
    metadata = {
        "name": "dependent",
        "version": "1.0.0",
        "description": "Depends on base",
        "author": "test",
        "dependencies": ["base"],
    }
    create_template_files("dependent", metadata)

    manager = TemplateManager(temp_template_dir)
    manager.discover_templates()

    # Try to enable without dependency - should return False
    result = manager.enable_template("dependent")
    assert result is False


def test_template_manager_dependency_satisfied(create_template_files, temp_template_dir):
    """Test enabling template with satisfied dependencies."""
    # Create base template
    base_metadata = {
        "name": "base",
        "version": "1.0.0",
        "description": "Base template",
        "author": "test",
        "enabled": True,
    }
    create_template_files("base", base_metadata)

    # Create dependent template
    dependent_metadata = {
        "name": "dependent",
        "version": "1.0.0",
        "description": "Depends on base",
        "author": "test",
        "dependencies": ["base"],
    }
    create_template_files("dependent", dependent_metadata)

    manager = TemplateManager(temp_template_dir)
    manager.discover_templates()

    # Should succeed because base is enabled
    manager.enable_template("dependent")

    template = manager.get_template("dependent")
    assert template.is_enabled()


def test_template_manager_list_templates(create_template_files, temp_template_dir):
    """Test listing all templates."""
    create_template_files("template1")
    create_template_files("template2")

    manager = TemplateManager(temp_template_dir)
    manager.discover_templates()

    templates = manager.list_templates()

    assert len(templates) == 2
    assert all(isinstance(template, Template) for template in templates)


def test_template_manager_validate_all(create_template_files, temp_template_dir):
    """Test validating all templates."""
    create_template_files("valid1")
    create_template_files("valid2")

    manager = TemplateManager(temp_template_dir)
    manager.discover_templates()

    results = manager.validate_all_templates()

    assert len(results) == 2
    assert all(results.values())


def test_template_manager_apply_template_config(create_template_files, temp_template_dir):
    """Test applying template configuration to server config."""
    metadata = {
        "name": "test-template",
        "version": "1.0.0",
        "description": "Test",
        "author": "test",
        "enabled": True,
        "paths": {"app_root": "/var/www/test", "logs": "/var/log/test"},
        "services": {"web": "nginx", "app": "test-app"},
        "env_vars": {"APP_ENV": "production"},
    }
    create_template_files("test-template", metadata)

    manager = TemplateManager(temp_template_dir)
    manager.discover_templates()

    server_config = {
        "name": "test-server",
        "host": "example.com",
        "paths": {"web_root": "/var/www/html"},
    }

    # apply_template_config applies ALL enabled templates automatically
    updated_config = manager.apply_template_config(server_config)

    # Check paths merged
    assert updated_config["paths"]["app_root"] == "/var/www/test"
    assert updated_config["paths"]["logs"] == "/var/log/test"
    assert updated_config["paths"]["web_root"] == "/var/www/html"  # Original preserved

    # Check services added
    assert updated_config["services"]["web"] == "nginx"
    assert updated_config["services"]["app"] == "test-app"

    # Check env vars added
    assert updated_config["env_vars"]["APP_ENV"] == "production"


def test_template_manager_invalid_template_json(temp_template_dir):
    """Test handling of invalid template.json."""
    template_path = temp_template_dir / "invalid-template"
    template_path.mkdir()

    # Create invalid JSON
    template_json = template_path / "template.json"
    template_json.write_text("{invalid json")

    manager = TemplateManager(temp_template_dir)
    manager.discover_templates()

    # Should skip invalid template
    assert "invalid-template" not in manager.templates


def test_template_manager_missing_template_json(temp_template_dir):
    """Test handling of directory without template.json."""
    template_path = temp_template_dir / "no-json"
    template_path.mkdir()

    manager = TemplateManager(temp_template_dir)
    manager.discover_templates()

    # Should skip directory without template.json
    assert "no-json" not in manager.templates


# ============================================================================
# PRE-BUILT TEMPLATE TESTS (Integration Tests)
# ============================================================================


def test_hestiacp_template_valid():
    """Test HestiaCP template has valid configuration."""
    template_path = Path("templates/hestiacp")

    if not template_path.exists():
        pytest.skip("HestiaCP template not found")

    template = Template(template_path)

    assert template.metadata["name"] == "hestiacp"
    assert template.metadata["version"] == "1.0.0"
    assert "hestia_root" in template.get_paths()
    assert "control_panel" in template.get_services()
    assert len(template.get_commands()) > 0


def test_n8n_template_valid():
    """Test n8n template has valid configuration."""
    template_path = Path("templates/n8n")

    if not template_path.exists():
        pytest.skip("n8n template not found")

    template = Template(template_path)

    assert template.metadata["name"] == "n8n"
    assert template.metadata["version"] == "1.0.0"
    assert "n8n_home" in template.get_paths()
    assert "automation" in template.get_services()
    assert "N8N_PORT" in template.get_env_vars()


def test_gitea_template_valid():
    """Test Gitea template has valid configuration."""
    template_path = Path("templates/gitea")

    if not template_path.exists():
        pytest.skip("Gitea template not found")

    template = Template(template_path)

    assert template.metadata["name"] == "gitea"
    assert template.metadata["version"] == "1.0.0"
    assert "gitea_root" in template.get_paths()
    assert "git_service" in template.get_services()
    # Check database configuration (it's under 'database' key, not 'database_support')
    assert template.metadata.get("database") is not None
    assert "supported" in template.metadata["database"]
