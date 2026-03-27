"""
Migration utilities for converting legacy NAVIG configurations to new format.

This module handles:
- Format detection (old vs new)
- Configuration conversion (old → new)
- Webserver type extraction from services.web field
- Backup creation before migration
"""

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


class ConfigMigrationError(Exception):
    """Raised when configuration migration fails."""

    pass


def detect_format(config_path: Path) -> str:
    """
    Detect if config is old or new format.

    Args:
        config_path: Path to configuration file

    Returns:
        'old' or 'new'

    Raises:
        FileNotFoundError: If config file doesn't exist
        yaml.YAMLError: If config file is invalid YAML
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_path, encoding="utf-8") as f:
        config = yaml.safe_load(f)

    if not config:
        raise ConfigMigrationError(f"Empty configuration file: {config_path}")

    # New format has 'apps' field at root
    if "apps" in config:
        return "new"

    # Old format has 'host' at root and no 'apps' field
    if "host" in config and "apps" not in config:
        return "old"

    raise ConfigMigrationError(
        f"Unable to detect format for {config_path}. "
        f"Config must have either 'apps' (new format) or 'host' (old format)."
    )


def extract_webserver_type(config: dict[str, Any]) -> str:
    """
    Extract webserver type from old format config.

    Looks for webserver type in:
    1. services.web field (nginx, apache2)
    2. webserver.type field (if already present)

    Args:
        config: Old format configuration dictionary

    Returns:
        Webserver type ('nginx' or 'apache2')

    Raises:
        ConfigMigrationError: If webserver type cannot be determined
    """
    # Check if webserver.type already exists (shouldn't in old format, but check anyway)
    if "webserver" in config and "type" in config.get("webserver", {}):
        return config["webserver"]["type"]

    # Extract from services.web field
    if "services" in config and "web" in config.get("services", {}):
        web_service = config["services"]["web"]

        # Normalize to lowercase for comparison
        web_service_lower = web_service.lower()

        if "nginx" in web_service_lower:
            return "nginx"
        elif "apache" in web_service_lower:
            return "apache2"

    # Unable to determine webserver type
    raise ConfigMigrationError(
        f"Unable to determine webserver type from configuration. "
        f"Expected 'services.web' field with value 'nginx' or 'apache2', but found: "
        f"{config.get('services', {}).get('web', 'MISSING')}"
    )


def migrate_config(old_path: Path, new_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Convert old format configuration to new format.

    Conversion process:
    1. Extract host-level fields (name, host, port, user, ssh_key, ssh_password)
    2. Extract webserver type from services.web field
    3. Move remaining fields into apps.<name> where <name> = filename
    4. Add metadata with migration timestamp
    5. Set default_app to the app name

    Args:
        old_path: Path to old format configuration file
        new_path: Path where new format will be saved

    Returns:
        Tuple of (old_config, new_config) dictionaries

    Raises:
        ConfigMigrationError: If migration fails
        FileNotFoundError: If old config doesn't exist
    """
    if not old_path.exists():
        raise FileNotFoundError(f"Old configuration file not found: {old_path}")

    # Load old configuration
    with open(old_path, encoding="utf-8") as f:
        old_config = yaml.safe_load(f)

    if not old_config:
        raise ConfigMigrationError(f"Empty configuration file: {old_path}")

    # Verify it's old format
    if detect_format(old_path) != "old":
        raise ConfigMigrationError(f"Configuration is not in old format: {old_path}")

    # Extract app name from filename (without .yaml extension)
    app_name = old_path.stem

    # Extract webserver type
    try:
        webserver_type = extract_webserver_type(old_config)
    except ConfigMigrationError as e:
        raise ConfigMigrationError(
            f"Failed to migrate {old_path}: {str(e)}\n"
            f"Please manually add 'services.web: nginx' or 'services.web: apache2' to the config."
        ) from e

    # Build new configuration
    new_config = {
        # Host-level fields
        "name": old_config.get("name", app_name),
        "host": old_config["host"],
        "port": old_config.get("port", 22),
        "user": old_config["user"],
    }

    # Add SSH authentication
    if "ssh_key" in old_config:
        new_config["ssh_key"] = old_config["ssh_key"]
    if "ssh_password" in old_config:
        new_config["ssh_password"] = old_config["ssh_password"]

    # Set default app
    new_config["default_app"] = app_name

    # Add migration metadata
    new_config["metadata"] = {
        "description": "Migrated from legacy format",
        "migrated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
    }

    # Build app configuration
    app_config = {}

    # Copy all fields except host-level ones
    host_level_fields = {"name", "host", "port", "user", "ssh_key", "ssh_password"}
    for key, value in old_config.items():
        if key not in host_level_fields:
            app_config[key] = value

    # Add webserver configuration with extracted type
    if "webserver" not in app_config:
        app_config["webserver"] = {}

    app_config["webserver"]["type"] = webserver_type

    # Add app to new config
    new_config["apps"] = {app_name: app_config}

    return old_config, new_config


def backup_config(config_path: Path) -> Path:
    """
    Create backup of configuration file.

    Backup filename format: <original_name>.backup.<timestamp>.yaml

    Args:
        config_path: Path to configuration file to backup

    Returns:
        Path to backup file

    Raises:
        FileNotFoundError: If config file doesn't exist
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = config_path.parent / f"{config_path.stem}.backup.{timestamp}.yaml"

    shutil.copy2(config_path, backup_path)

    return backup_path


def save_config(config: dict[str, Any], path: Path) -> None:
    """
    Save configuration to YAML file.

    Args:
        config: Configuration dictionary
        path: Path where to save configuration
    """
    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False, allow_unicode=True)


def migrate_all_configs(
    old_dir: Path, new_dir: Path, dry_run: bool = False, backup: bool = True
) -> dict[str, Any]:
    """
    Migrate all configurations from old directory to new directory.

    Args:
        old_dir: Directory containing old format configs (~/.navig/apps/)
        new_dir: Directory where new format configs will be saved (~/.navig/hosts/)
        dry_run: If True, only show what would be migrated without making changes
        backup: If True, create backups before migration

    Returns:
        Dictionary with migration results:
        {
            'migrated': [list of migrated files],
            'skipped': [list of skipped files],
            'failed': [list of failed files with errors],
            'backups': [list of backup files created]
        }
    """
    results = {"migrated": [], "skipped": [], "failed": [], "backups": []}

    if not old_dir.exists():
        return results

    # Find all YAML files in old directory
    yaml_files = list(old_dir.glob("*.yaml")) + list(old_dir.glob("*.yml"))

    for old_path in yaml_files:
        try:
            # Skip backup files
            if ".backup." in old_path.name:
                results["skipped"].append({"file": str(old_path), "reason": "Backup file"})
                continue

            # Detect format
            format_type = detect_format(old_path)

            if format_type == "new":
                results["skipped"].append(
                    {"file": str(old_path), "reason": "Already in new format"}
                )
                continue

            # Determine new path
            new_path = new_dir / old_path.name

            if dry_run:
                # Just validate migration without saving
                old_config, new_config = migrate_config(old_path, new_path)
                results["migrated"].append(
                    {
                        "old_file": str(old_path),
                        "new_file": str(new_path),
                        "dry_run": True,
                    }
                )
            else:
                # Create backup if requested
                if backup:
                    backup_path = backup_config(old_path)
                    results["backups"].append(str(backup_path))

                # Perform migration
                old_config, new_config = migrate_config(old_path, new_path)

                # Save new configuration
                save_config(new_config, new_path)

                results["migrated"].append(
                    {
                        "old_file": str(old_path),
                        "new_file": str(new_path),
                        "dry_run": False,
                    }
                )

        except Exception as e:
            results["failed"].append({"file": str(old_path), "error": str(e)})

    return results
