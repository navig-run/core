"""
Server Template Configuration Manager

Manages per-server template configurations with template-based initialization,
auto-detection, customization, and sync capabilities.
"""

import copy
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from navig import console_helper as ch
from navig.config import ConfigManager, get_config_manager
from navig.template_manager import TemplateManager


class ServerTemplateManager:
    """
    Manages template configurations for individual servers.

    Implements hybrid storage approach:
    - Server YAML tracks template state (enabled, version, detection info)
    - Separate YAML files store customizations (only when needed)
    - Templates serve as defaults until overridden

    Directory Structure:
        ~/.navig/apps/
        ├── production.yaml              # Server config with template state
        └── production/                  # Server-specific data
            └── templates/                  # Template customizations
                ├── n8n.yaml             # Only exists if customized
                ├── gitea.yaml
                └── hestiacp.yaml
    """

    def __init__(
        self,
        config_manager: ConfigManager | None = None,
        template_manager: TemplateManager | None = None,
    ):
        """
        Initialize ServerTemplateManager.

        Args:
            config_manager: ConfigManager instance (creates new if None)
            template_manager: TemplateManager instance (creates new if None)
        """
        self.config_manager = config_manager or get_config_manager()
        self.template_manager = template_manager or TemplateManager()

        # Discover available templates
        self.template_manager.discover_templates()

    def _get_server_template_dir(self, server_name: str) -> Path:
        """Get the template directory for a specific server."""
        server_dir = self.config_manager.apps_dir / server_name
        template_dir = server_dir / "templates"
        return template_dir

    def _ensure_server_template_dir(self, server_name: str):
        """Create server template directory if it doesn't exist."""
        template_dir = self._get_server_template_dir(server_name)
        template_dir.mkdir(parents=True, exist_ok=True)

    def initialize_templates_from_detection(
        self, server_name: str, detected_templates: dict[str, dict[str, Any]]
    ) -> dict[str, bool]:
        """
        Initialize template configurations from auto-detection results.

        Args:
            server_name: Name of the server
            detected_templates: Dict from ServerDiscovery.discover_templates()
                Format: {'template_name': {'detected': True, 'version': '1.0.0', ...}}

        Returns:
            Dict mapping template names to initialization success status
        """
        ch.step(f"Initializing templates for server '{server_name}'...")

        results = {}

        # Load server config
        try:
            server_config = self.config_manager.load_server_config(server_name)
        except FileNotFoundError:
            ch.error(f"Server '{server_name}' not found")
            return results

        # Initialize templates section if not exists
        if "templates" not in server_config:
            server_config["templates"] = {}

        # Process each detected template
        for template_name, detection_info in detected_templates.items():
            # Check if template template exists
            template = self.template_manager.get_template(template_name)
            if not template:
                ch.warning(f"No template found for detected template '{template_name}'")
                results[template_name] = False
                continue

            # Initialize template config in server YAML
            server_config["templates"][template_name] = {
                "enabled": True,  # Auto-enable detected templates
                "template_version": template.metadata.get("version", "1.0.0"),
                "last_synced": datetime.now().isoformat(),
                "auto_detected": True,
                "customized": False,
                "detection_info": {
                    "version": detection_info.get("version"),
                    "paths": detection_info.get("paths", {}),
                    "services": detection_info.get("services", []),
                    "ports": detection_info.get("ports", []),
                },
            }

            results[template_name] = True
            ch.success(f"Initialized template '{template_name}'")

        # Save updated server config
        self.config_manager.save_server_config(server_name, server_config)

        return results

    def initialize_template_manually(
        self, server_name: str, template_name: str, enabled: bool = False
    ) -> bool:
        """
        Manually initialize an template for a server (not auto-detected).

        Args:
            server_name: Name of the server
            template_name: Name of the template to initialize
            enabled: Whether to enable the template immediately

        Returns:
            True if successful, False otherwise
        """
        # Check if template template exists
        template = self.template_manager.get_template(template_name)
        if not template:
            ch.error(f"Template template '{template_name}' not found")
            return False

        # Load server config
        try:
            server_config = self.config_manager.load_server_config(server_name)
        except FileNotFoundError:
            ch.error(f"Server '{server_name}' not found")
            return False

        # Initialize templates section if not exists
        if "templates" not in server_config:
            server_config["templates"] = {}

        # Check if already initialized
        if template_name in server_config["templates"]:
            ch.warning(
                f"Template '{template_name}' already initialized for server '{server_name}'"
            )
            return True

        # Initialize template config
        server_config["templates"][template_name] = {
            "enabled": enabled,
            "template_version": template.metadata.get("version", "1.0.0"),
            "last_synced": datetime.now().isoformat(),
            "auto_detected": False,
            "customized": False,
        }

        # Save updated server config
        self.config_manager.save_server_config(server_name, server_config)

        ch.success(f"Initialized template '{template_name}' for server '{server_name}'")
        return True

    def get_template_config(
        self, server_name: str, template_name: str, include_template: bool = True
    ) -> dict[str, Any] | None:
        """
        Get merged template configuration for a server.

        Merge order:
        1. Start with template template
        2. Apply detection info (if auto-detected)
        3. Apply custom overrides (if customization file exists)

        Args:
            server_name: Name of the server
            template_name: Name of the template
            include_template: Whether to merge with template (default: True)

        Returns:
            Merged configuration dict or None if not initialized
        """
        # Load server config
        try:
            server_config = self.config_manager.load_server_config(server_name)
        except FileNotFoundError:
            ch.error(f"Server '{server_name}' not found")
            return None

        # Check if template is initialized for this server
        templates = server_config.get("templates", {})
        if template_name not in templates:
            return None

        template_state = templates[template_name]

        # Start with template if requested
        if include_template:
            template = self.template_manager.get_template(template_name)
            if not template:
                ch.error(f"Template template '{template_name}' not found")
                return None

            # Deep copy template metadata (paths, services, env_vars, etc.)
            merged_config = {
                "paths": copy.deepcopy(template.get_paths()),
                "services": copy.deepcopy(template.get_services()),
                "env_vars": copy.deepcopy(template.get_env_vars()),
                "commands": copy.deepcopy(template.get_commands()),
            }

            # Include API config if present
            if "api" in template.metadata:
                merged_config["api"] = copy.deepcopy(template.metadata["api"])
        else:
            merged_config = {}

        # Apply detection info overrides
        if template_state.get("auto_detected") and "detection_info" in template_state:
            detection = template_state["detection_info"]

            # Merge detected paths (override template)
            if detection.get("paths"):
                if "paths" not in merged_config:
                    merged_config["paths"] = {}
                merged_config["paths"].update(detection["paths"])

        # Apply custom overrides from file (check YAML first, then JSON for backwards compat)
        yaml_config_file = (
            self._get_server_template_dir(server_name) / f"{template_name}.yaml"
        )
        json_config_file = (
            self._get_server_template_dir(server_name) / f"{template_name}.json"
        )

        custom_config_file = None
        if yaml_config_file.exists():
            custom_config_file = yaml_config_file
        elif json_config_file.exists():
            custom_config_file = json_config_file

        if custom_config_file:
            try:
                with open(custom_config_file, encoding="utf-8") as f:
                    custom_config = yaml.safe_load(f)

                # Deep merge custom config
                merged_config = self._deep_merge(merged_config, custom_config)
            except Exception as e:
                ch.warning(f"Failed to load custom config for '{template_name}': {e}")

        return merged_config

    def set_template_custom_value(
        self, server_name: str, template_name: str, key_path: str, value: Any
    ) -> bool:
        """
        Set a custom value for an template configuration.

        Creates custom config file if it doesn't exist.
        Marks template as customized in server YAML.

        Args:
            server_name: Name of the server
            template_name: Name of the template
            key_path: Dot-separated path to config key (e.g., 'paths.web_root')
            value: Value to set

        Returns:
            True if successful, False otherwise
        """
        # Load server config
        try:
            server_config = self.config_manager.load_server_config(server_name)
        except FileNotFoundError:
            ch.error(f"Server '{server_name}' not found")
            return False

        # Check if template is initialized
        templates = server_config.get("templates", {})
        if template_name not in templates:
            ch.error(
                f"Template '{template_name}' not initialized for server '{server_name}'"
            )
            return False

        # Ensure template directory exists
        self._ensure_server_template_dir(server_name)

        # Load or create custom config (prefer YAML, migrate JSON if exists)
        yaml_config_file = (
            self._get_server_template_dir(server_name) / f"{template_name}.yaml"
        )
        json_config_file = (
            self._get_server_template_dir(server_name) / f"{template_name}.json"
        )

        custom_config = {}
        if yaml_config_file.exists():
            with open(yaml_config_file, encoding="utf-8") as f:
                custom_config = yaml.safe_load(f) or {}
        elif json_config_file.exists():
            # Migrate from JSON to YAML
            with open(json_config_file, encoding="utf-8") as f:
                import json

                custom_config = json.load(f)

        # Set value using key path
        keys = key_path.split(".")
        current = custom_config
        for key in keys[:-1]:
            if key not in current:
                current[key] = {}
            current = current[key]
        current[keys[-1]] = value

        # Save custom config as YAML
        with open(yaml_config_file, "w", encoding="utf-8") as f:
            yaml.dump(custom_config, f, default_flow_style=False, sort_keys=False)

        # Mark as customized in server YAML
        server_config["templates"][template_name]["customized"] = True
        server_config["templates"][template_name][
            "last_modified"
        ] = datetime.now().isoformat()
        self.config_manager.save_server_config(server_name, server_config)

        ch.success(f"Set {key_path} = {value} for template '{template_name}'")
        return True

    def enable_template(self, server_name: str, template_name: str) -> bool:
        """Enable an template for a specific server."""
        try:
            server_config = self.config_manager.load_server_config(server_name)
        except FileNotFoundError:
            ch.error(f"Server '{server_name}' not found")
            return False

        templates = server_config.get("templates", {})
        if template_name not in templates:
            ch.error(
                f"Template '{template_name}' not initialized. Initialize it first."
            )
            return False

        if templates[template_name].get("enabled"):
            ch.warning(f"Template '{template_name}' already enabled")
            return True

        templates[template_name]["enabled"] = True
        templates[template_name]["last_enabled"] = datetime.now().isoformat()
        self.config_manager.save_server_config(server_name, server_config)

        ch.success(f"Enabled template '{template_name}' for server '{server_name}'")
        return True

    def disable_template(self, server_name: str, template_name: str) -> bool:
        """Disable an template for a specific server."""
        try:
            server_config = self.config_manager.load_server_config(server_name)
        except FileNotFoundError:
            ch.error(f"Server '{server_name}' not found")
            return False

        templates = server_config.get("templates", {})
        if template_name not in templates:
            ch.warning(f"Template '{template_name}' not initialized for this server")
            return True

        if not templates[template_name].get("enabled"):
            ch.warning(f"Template '{template_name}' already disabled")
            return True

        templates[template_name]["enabled"] = False
        templates[template_name]["last_disabled"] = datetime.now().isoformat()
        self.config_manager.save_server_config(server_name, server_config)

        ch.success(f"Disabled template '{template_name}' for server '{server_name}'")
        return True

    def list_server_templates(
        self, server_name: str, enabled_only: bool = False
    ) -> list[dict[str, Any]]:
        """
        List all template configurations for a server.

        Args:
            server_name: Name of the server
            enabled_only: Only return enabled templates

        Returns:
            List of dicts with template info
        """
        try:
            server_config = self.config_manager.load_server_config(server_name)
        except FileNotFoundError:
            return []

        templates = server_config.get("templates", {})
        result = []

        for template_name, template_state in templates.items():
            if enabled_only and not template_state.get("enabled"):
                continue

            result.append(
                {
                    "name": template_name,
                    "enabled": template_state.get("enabled", False),
                    "template_version": template_state.get("template_version"),
                    "auto_detected": template_state.get("auto_detected", False),
                    "customized": template_state.get("customized", False),
                    "last_synced": template_state.get("last_synced"),
                }
            )

        return sorted(result, key=lambda x: x["name"])

    def sync_template_from_template(
        self, server_name: str, template_name: str, preserve_custom: bool = True
    ) -> bool:
        """
        Sync template configuration from template.

        Updates template version and last_synced timestamp.
        Optionally preserves custom overrides.

        Args:
            server_name: Name of the server
            template_name: Name of the template
            preserve_custom: Keep custom overrides (default: True)

        Returns:
            True if successful, False otherwise
        """
        # Load server config
        try:
            server_config = self.config_manager.load_server_config(server_name)
        except FileNotFoundError:
            ch.error(f"Server '{server_name}' not found")
            return False

        # Check if template is initialized
        templates = server_config.get("templates", {})
        if template_name not in templates:
            ch.error(
                f"Template '{template_name}' not initialized for server '{server_name}'"
            )
            return False

        # Get current template
        template = self.template_manager.get_template(template_name)
        if not template:
            ch.error(f"Template template '{template_name}' not found")
            return False

        # Update version and sync timestamp
        old_version = templates[template_name].get("template_version", "unknown")
        new_version = template.metadata.get("version", "1.0.0")

        templates[template_name]["template_version"] = new_version
        templates[template_name]["last_synced"] = datetime.now().isoformat()

        # Save server config
        self.config_manager.save_server_config(server_name, server_config)

        if old_version != new_version:
            ch.success(
                f"Synced template '{template_name}' from template (v{old_version} -> v{new_version})"
            )
        else:
            ch.success(
                f"Synced template '{template_name}' from template (v{new_version})"
            )

        if preserve_custom:
            ch.dim("Custom overrides preserved")

        return True

    def _deep_merge(self, base: dict, overlay: dict) -> dict:
        """
        Deep merge two dictionaries.

        Values from overlay override values in base.
        Recursively merges nested dicts.
        """
        result = copy.deepcopy(base)

        for key, value in overlay.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                result[key] = self._deep_merge(result[key], value)
            else:
                result[key] = copy.deepcopy(value)

        return result
