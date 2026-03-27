"""
Template Manager for NAVIG

Modular plugin system for server-specific configurations.
Hot-swappable. Versioned. Traceable.
"""

import json
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from navig import console_helper as ch


class TemplateSchema:
    """Schema for template metadata validation (supports both JSON and YAML)."""

    REQUIRED_FIELDS = ["name", "version", "description", "author"]
    OPTIONAL_FIELDS = [
        "dependencies",
        "enabled",
        "paths",
        "services",
        "commands",
        "env_vars",
        "hooks",
    ]

    @staticmethod
    def validate(metadata: dict[str, Any]) -> tuple[bool, str | None]:
        """Validate template metadata against schema."""
        # Check required fields
        for field in TemplateSchema.REQUIRED_FIELDS:
            if field not in metadata:
                return False, f"Missing required field: {field}"

        # Validate version format
        version = metadata.get("version", "")
        if not version or not isinstance(version, str):
            return False, "Invalid version format"

        # Validate dependencies
        if "dependencies" in metadata:
            if not isinstance(metadata["dependencies"], list):
                return False, "Dependencies must be a list"

        # Validate enabled flag
        if "enabled" in metadata:
            if not isinstance(metadata["enabled"], bool):
                return False, "Enabled field must be boolean"

        return True, None


class Template:
    """Represents a single template with metadata and configuration."""

    def __init__(self, template_dir: Path):
        self.template_dir = template_dir
        self.name = template_dir.name
        self.metadata_file, self.metadata_format = self._find_metadata_file()
        self.metadata = self._load_metadata()
        self.hooks: dict[str, Callable] = {}
        self._loaded = False

    def _find_metadata_file(self) -> tuple[Path, str]:
        """
        Find template metadata file, preferring YAML over JSON.

        Returns:
            Tuple of (file_path, format) where format is 'yaml' or 'json'
        """
        yaml_file = self.template_dir / "template.yaml"
        json_file = self.template_dir / "template.json"

        # Prefer YAML format
        if yaml_file.exists():
            return yaml_file, "yaml"
        elif json_file.exists():
            return json_file, "json"
        else:
            raise FileNotFoundError(
                f"Template metadata not found in {self.template_dir}. "
                f"Expected template.yaml or template.json"
            )

    def _load_metadata(self) -> dict[str, Any]:
        """Load template metadata from YAML or JSON file."""
        with open(self.metadata_file, encoding="utf-8") as f:
            if self.metadata_format == "yaml":
                metadata = yaml.safe_load(f)
            else:
                metadata = json.load(f)

        # Validate schema
        valid, error = TemplateSchema.validate(metadata)
        if not valid:
            raise ValueError(f"Invalid template metadata for '{self.name}': {error}")

        return metadata

    def save_metadata(self):
        """Save template metadata to file (preserving original format)."""
        with open(self.metadata_file, "w", encoding="utf-8") as f:
            if self.metadata_format == "yaml":
                yaml.dump(self.metadata, f, default_flow_style=False, sort_keys=False)
            else:
                json.dump(self.metadata, f, indent=2)

    def is_enabled(self) -> bool:
        """Check if template is enabled."""
        return self.metadata.get("enabled", False)

    def enable(self):
        """Enable the template."""
        if not self.is_enabled():
            self.metadata["enabled"] = True
            self.metadata["last_enabled"] = datetime.now().isoformat()
            self.save_metadata()
            self._call_hook("onEnable")

    def disable(self):
        """Disable the template."""
        if self.is_enabled():
            self.metadata["enabled"] = False
            self.metadata["last_disabled"] = datetime.now().isoformat()
            self.save_metadata()
            self._call_hook("onDisable")

    def load(self):
        """Load the template into memory."""
        if self._loaded:
            return

        self._call_hook("onLoad")
        self._loaded = True

    def unload(self):
        """Unload the template from memory."""
        if not self._loaded:
            return

        self._call_hook("onUnload")
        self._loaded = False

    def register_hook(self, hook_name: str, callback: Callable):
        """Register a lifecycle hook."""
        self.hooks[hook_name] = callback

    def _call_hook(self, hook_name: str):
        """Call a lifecycle hook if registered."""
        if hook_name in self.hooks:
            try:
                self.hooks[hook_name](self)
            except Exception as e:
                ch.error(f"Hook '{hook_name}' failed for template '{self.name}': {e}")

    def get_paths(self) -> dict[str, str]:
        """Get server paths defined by this template."""
        return self.metadata.get("paths", {})

    def get_services(self) -> dict[str, str]:
        """Get services defined by this template."""
        return self.metadata.get("services", {})

    def get_commands(self) -> list[dict[str, str]]:
        """Get common commands defined by this template."""
        return self.metadata.get("commands", [])

    def get_env_vars(self) -> dict[str, str]:
        """Get environment variables defined by this template."""
        return self.metadata.get("env_vars", {})

    def check_dependencies(
        self, available_templates: list[str]
    ) -> tuple[bool, list[str]]:
        """Check if all dependencies are met."""
        dependencies = self.metadata.get("dependencies", [])
        missing = [dep for dep in dependencies if dep not in available_templates]
        return len(missing) == 0, missing


class TemplateManager:
    """
    Manages template lifecycle, discovery, and state.

    Directory Structure:
        store/templates/
        ├── hestiacp/
        │   ├── template.yaml  (preferred) or template.json
        │   └── README.md
        ├── n8n/
        │   ├── template.yaml
        │   └── README.md
        └── gitea/
            ├── template.yaml
            └── README.md
    """

    def __init__(self, templates_dir: Path | None = None):
        # Resolve built-in templates from the content store
        if templates_dir is None:
            try:
                from navig.platform.paths import builtin_store_dir

                templates_dir = builtin_store_dir() / "templates"
            except Exception:
                # Fallback: resolve relative to this file (navig/ → repo root → store/)
                templates_dir = (
                    Path(__file__).resolve().parent.parent / "store" / "templates"
                )

        self.templates_dir = templates_dir
        self.templates: dict[str, Template] = {}
        self._ensure_directory()

    def _ensure_directory(self):
        """Create templates directory if it doesn't exist."""
        self.templates_dir.mkdir(parents=True, exist_ok=True)

    def discover_templates(self) -> list[str]:
        """Scan templates directory and discover all available templates (YAML or JSON)."""
        discovered = []

        for template_dir in self.templates_dir.iterdir():
            if not template_dir.is_dir():
                continue

            # Check for template metadata file (YAML preferred, JSON fallback)
            yaml_file = template_dir / "template.yaml"
            json_file = template_dir / "template.json"

            if not yaml_file.exists() and not json_file.exists():
                ch.warning(
                    f"Skipping '{template_dir.name}': no template.yaml or template.json found"
                )
                continue

            try:
                template = Template(template_dir)
                self.templates[template.name] = template
                discovered.append(template.name)
            except Exception as e:
                ch.error(f"Failed to load template '{template_dir.name}': {e}")

        return discovered

    def get_template(self, name: str) -> Template | None:
        """Get template by name."""
        return self.templates.get(name)

    def list_templates(self, enabled_only: bool = False) -> list[Template]:
        """List all templates, optionally filtering by enabled status."""
        templates = list(self.templates.values())
        if enabled_only:
            templates = [a for a in templates if a.is_enabled()]
        return sorted(templates, key=lambda a: a.metadata["name"])

    def enable_template(self, name: str) -> bool:
        """Enable an template with dependency checking."""
        template = self.get_template(name)
        if not template:
            ch.error(f"Template '{name}' not found")
            return False

        if template.is_enabled():
            ch.warning(f"Template '{name}' is already enabled")
            return True

        # Check dependencies
        available_templates = [a.name for a in self.list_templates(enabled_only=True)]
        deps_met, missing_deps = template.check_dependencies(available_templates)

        if not deps_met:
            ch.error(
                f"Cannot enable '{name}': missing dependencies: {', '.join(missing_deps)}"
            )
            return False

        # Enable template
        try:
            template.enable()
            template.load()
            ch.success(f"Template '{name}' enabled")
            return True
        except Exception as e:
            ch.error(f"Failed to enable template '{name}': {e}")
            return False

    def disable_template(self, name: str) -> bool:
        """Disable an template with dependent checking."""
        template = self.get_template(name)
        if not template:
            ch.error(f"Template '{name}' not found")
            return False

        if not template.is_enabled():
            ch.warning(f"Template '{name}' is already disabled")
            return True

        # Check if other templates depend on this one
        dependents = []
        for other_template in self.list_templates(enabled_only=True):
            if other_template.name == name:
                continue
            deps = other_template.metadata.get("dependencies", [])
            if name in deps:
                dependents.append(other_template.name)

        if dependents:
            ch.warning(
                f"Warning: The following templates depend on '{name}': {', '.join(dependents)}"
            )
            if not ch.confirm_action(f"Disable '{name}' anyway?"):
                return False

        # Disable template
        try:
            template.unload()
            template.disable()
            ch.success(f"Template '{name}' disabled")
            return True
        except Exception as e:
            ch.error(f"Failed to disable template '{name}': {e}")
            return False

    def toggle_template(self, name: str) -> bool:
        """Toggle template enabled/disabled state."""
        template = self.get_template(name)
        if not template:
            ch.error(f"Template '{name}' not found")
            return False

        if template.is_enabled():
            return self.disable_template(name)
        else:
            return self.enable_template(name)

    def load_enabled_templates(self):
        """Load all enabled templates into memory (lazy loading)."""
        for template in self.list_templates(enabled_only=True):
            try:
                template.load()
            except Exception as e:
                ch.error(f"Failed to load template '{template.name}': {e}")

    def apply_template_config(self, server_config: dict[str, Any]) -> dict[str, Any]:
        """Apply enabled template configurations to server config."""
        for template in self.list_templates(enabled_only=True):
            # Merge paths
            template_paths = template.get_paths()
            if template_paths:
                if "paths" not in server_config:
                    server_config["paths"] = {}
                server_config["paths"].update(template_paths)

            # Merge services
            template_services = template.get_services()
            if template_services:
                if "services" not in server_config:
                    server_config["services"] = {}
                server_config["services"].update(template_services)

            # Add env vars
            template_env = template.get_env_vars()
            if template_env:
                if "env_vars" not in server_config:
                    server_config["env_vars"] = {}
                server_config["env_vars"].update(template_env)

        return server_config

    def get_template_commands(self) -> list[dict[str, str]]:
        """Get all common commands from enabled templates."""
        commands = []
        for template in self.list_templates(enabled_only=True):
            commands.extend(template.get_commands())
        return commands

    def validate_all_templates(self) -> dict[str, bool]:
        """Validate all template configurations."""
        results = {}
        for name, template in self.templates.items():
            try:
                valid, error = TemplateSchema.validate(template.metadata)
                results[name] = valid
                if not valid:
                    ch.error(f"Template '{name}' validation failed: {error}")
            except Exception as e:
                results[name] = False
                ch.error(f"Template '{name}' validation error: {e}")
        return results
