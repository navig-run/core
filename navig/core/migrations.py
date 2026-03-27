"""
Configuration Migration System

Handles automated migration of configuration files between versions.
Ensures seamless upgrades by transforming old config structures
into the current schema.
"""

from dataclasses import dataclass
from typing import Any, Callable, Dict, List

from packaging import version as pkg_version
from rich.console import Console

# Use stderr for migration logs to avoid polluting stdout (JSON output)
stderr_console = Console(stderr=True)

# Current configuration version
CURRENT_VERSION = "1.0"


@dataclass
class Migration:
    """Definition of a configuration migration."""

    from_version: str
    to_version: str
    description: str
    apply: Callable[[Dict[str, Any]], Dict[str, Any]]


class MigrationManager:
    """Manages and applies configuration migrations."""

    def __init__(self):
        self.migrations: List[Migration] = []
        self._register_core_migrations()

    def _register_core_migrations(self):
        """Register built-in migrations."""
        self.register(
            Migration(
                from_version="0.9",
                to_version="1.0",
                description="Migrate legacy AI fields",
                apply=self._migrate_0_9_to_1_0,
            )
        )

    def register(self, migration: Migration):
        """Register a new migration."""
        self.migrations.append(migration)

    def get_pending_migrations(self, current_version: str) -> List[Migration]:
        """
        Get list of migrations that need to be applied.
        Sorts by version.
        """
        if not current_version:
            current_version = "0.0"

        try:
            curr = pkg_version.parse(current_version)
        except Exception:
            # If version is invalid, assume oldest
            curr = pkg_version.parse("0.0")

        pending = []
        for m in self.migrations:
            try:
                mig_ver = pkg_version.parse(m.from_version)
                if mig_ver >= curr:
                    pending.append(m)
            except Exception:
                continue

        # Sort by version
        return sorted(pending, key=lambda x: pkg_version.parse(x.from_version))

    def apply_migrations(self, config: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
        """
        Apply all pending migrations to the configuration.

        Returns:
            Tuple of (migrated_config, was_modified)
        """
        # Get version from config, default to 0.0 if missing
        config_ver = config.get("version", "0.0")

        # If config ver is same as current system version, no need to migrate
        if config_ver == CURRENT_VERSION:
            return config, False

        pending = self.get_pending_migrations(config_ver)

        if not pending:
            # Just update the version tag if no migrations needed
            if config_ver != CURRENT_VERSION:
                config["version"] = CURRENT_VERSION
                return config, True
            return config, False

        modified = False
        migrated_config = config.copy()

        # Log to stderr to allow clean stdout for JSON output
        stderr_console.print(
            f"[blue]ℹ[/blue] Applying {len(pending)} configuration migrations..."
        )

        for migration in pending:
            try:
                stderr_console.print(
                    f"[dim]  - [{migration.from_version} -> {migration.to_version}] {migration.description}[/dim]"
                )
                migrated_config = migration.apply(migrated_config)
                migrated_config["version"] = migration.to_version
                modified = True
            except Exception as e:
                stderr_console.print(f"[red]✗[/red] Migration failed: {e}")
                # Stop processing on failure to avoid corruption
                break

        # Ensure final version is set
        migrated_config["version"] = CURRENT_VERSION

        return migrated_config, modified

    # --- Migration Logic implementations ---

    def _migrate_0_9_to_1_0(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """
        Migrate from v0.9 (Legacy) to v1.0.

        Changes:
        - Moves 'ai_model_preference' -> 'ai.model_preference'
        """
        # Ensure 'ai' dict exists
        if "ai" not in config:
            config["ai"] = {}

        # Move legacy field if it exists
        if "ai_model_preference" in config:
            # Only migrate if target doesn't already have it
            if "model_preference" not in config["ai"]:
                config["ai"]["model_preference"] = config.pop("ai_model_preference")
            else:
                # If both exist, just remove legacy
                config.pop("ai_model_preference")

        return config


# Global instance
migration_manager = MigrationManager()


def migrate_config(config: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
    """Helper to apply migrations using default manager."""
    return migration_manager.apply_migrations(config)
