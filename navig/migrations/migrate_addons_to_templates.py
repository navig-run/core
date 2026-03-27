"""
Migrate Addons to Templates

This migration script converts the legacy "addons" system to the new "templates"
architecture:

1. Repository: addons/<name>/addon.json → templates/<name>/template.yaml
2. User overrides: ~/.navig/apps/<server>/addons/<name>.json →
                   ~/.navig/apps/<server>/templates/<name>.yaml

The migration is idempotent and safe to run multiple times.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

from navig import console_helper as ch
from navig.config import get_config_manager


class AddonToTemplateMigration:
    """Handles migration from addons to templates architecture."""

    def __init__(self, dry_run: bool = False, force: bool = False):
        """
        Initialize migration.

        Args:
            dry_run: If True, show what would be done without making changes
            force: If True, overwrite existing YAML files
        """
        self.dry_run = dry_run
        self.force = force
        self.config_manager = get_config_manager()

        # Paths
        self.repo_root = Path.cwd()
        self.addons_dir = self.repo_root / "addons"
        self.templates_dir = self.repo_root / "store" / "templates"

        # Migration results
        self.migrated_repo: List[str] = []
        self.migrated_user: List[str] = []
        self.skipped: List[Tuple[str, str]] = []  # (path, reason)
        self.errors: List[Tuple[str, str]] = []  # (path, error message)

    def run(self) -> bool:
        """
        Run the full migration.

        Returns:
            True if migration completed successfully, False if there were errors
        """
        ch.header("Addons to Templates Migration")

        if self.dry_run:
            ch.warning("DRY RUN - No changes will be made")
            ch.newline()

        # Phase 1: Migrate repository addons
        ch.step("Phase 1: Migrating repository addons...")
        self._migrate_repo_addons()

        # Phase 2: Migrate user addon overrides
        ch.step("Phase 2: Migrating user addon overrides...")
        self._migrate_user_addons()

        # Print summary
        self._print_summary()

        return len(self.errors) == 0

    def _migrate_repo_addons(self):
        """Migrate addons/<name>/addon.json to templates/<name>/template.yaml."""
        if not self.addons_dir.exists():
            ch.dim("  No addons/ directory found - skipping repository migration")
            return

        # Ensure templates directory exists
        if not self.dry_run:
            self.templates_dir.mkdir(parents=True, exist_ok=True)

        for addon_dir in self.addons_dir.iterdir():
            if not addon_dir.is_dir():
                continue

            addon_name = addon_dir.name
            json_file = addon_dir / "addon.json"
            yaml_file = addon_dir / "addon.yaml"

            # Check for source file (JSON or YAML)
            source_file = None
            if json_file.exists():
                source_file = json_file
            elif yaml_file.exists():
                source_file = yaml_file
            else:
                self.skipped.append(
                    (str(addon_dir), "No addon.json or addon.yaml found")
                )
                continue

            # Target location
            target_dir = self.templates_dir / addon_name
            target_file = target_dir / "template.yaml"

            # Check if target already exists
            if target_file.exists() and not self.force:
                self.skipped.append(
                    (str(target_file), "Already exists (use --force to overwrite)")
                )
                continue

            # Load source
            try:
                content = self._load_file(source_file)
            except Exception as e:
                self.errors.append((str(source_file), str(e)))
                continue

            # Write target
            if not self.dry_run:
                try:
                    target_dir.mkdir(parents=True, exist_ok=True)
                    self._save_yaml(target_file, content)

                    # Copy README if exists
                    readme = addon_dir / "README.md"
                    if readme.exists():
                        shutil.copy2(readme, target_dir / "README.md")

                    ch.success(
                        f"  {addon_name}: addon.{'json' if source_file.suffix == '.json' else 'yaml'} -> template.yaml"
                    )
                except Exception as e:
                    self.errors.append((str(target_file), str(e)))
                    continue
            else:
                ch.dim(f"  Would migrate: {source_file} → {target_file}")

            self.migrated_repo.append(addon_name)

    def _migrate_user_addons(self):
        """Migrate ~/.navig/apps/<server>/addons/*.json to templates/*.yaml."""
        apps_dir = self.config_manager.apps_dir

        if not apps_dir.exists():
            ch.dim("  No apps directory found - skipping user migration")
            return

        # Find all server directories
        for server_dir in apps_dir.iterdir():
            if not server_dir.is_dir():
                continue

            server_name = server_dir.name
            addons_dir = server_dir / "addons"
            templates_dir = server_dir / "templates"

            if not addons_dir.exists():
                continue

            ch.dim(f"  Processing server: {server_name}")

            # Migrate each addon file
            for addon_file in addons_dir.glob("*.json"):
                addon_name = addon_file.stem
                target_file = templates_dir / f"{addon_name}.yaml"

                # Check if target exists
                if target_file.exists() and not self.force:
                    self.skipped.append((str(target_file), "Already exists"))
                    continue

                # Load and convert
                try:
                    content = self._load_file(addon_file)
                except Exception as e:
                    self.errors.append((str(addon_file), str(e)))
                    continue

                # Write target
                if not self.dry_run:
                    try:
                        templates_dir.mkdir(parents=True, exist_ok=True)
                        self._save_yaml(target_file, content)
                        ch.success(f"    {addon_name}.json -> {addon_name}.yaml")
                    except Exception as e:
                        self.errors.append((str(target_file), str(e)))
                        continue
                else:
                    ch.dim(f"    Would migrate: {addon_file} → {target_file}")

                self.migrated_user.append(f"{server_name}/{addon_name}")

            # Also migrate YAML files in addons/ to templates/
            for addon_file in addons_dir.glob("*.yaml"):
                addon_name = addon_file.stem
                target_file = templates_dir / f"{addon_name}.yaml"

                if target_file.exists() and not self.force:
                    self.skipped.append((str(target_file), "Already exists"))
                    continue

                if not self.dry_run:
                    try:
                        templates_dir.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(addon_file, target_file)
                        ch.success(
                            f"    addons/{addon_name}.yaml -> templates/{addon_name}.yaml"
                        )
                    except Exception as e:
                        self.errors.append((str(target_file), str(e)))
                        continue
                else:
                    ch.dim(f"    Would copy: {addon_file} → {target_file}")

                self.migrated_user.append(f"{server_name}/{addon_name}")

    def _load_file(self, path: Path) -> Dict[str, Any]:
        """Load JSON or YAML file."""
        with open(path, "r", encoding="utf-8") as f:
            if path.suffix == ".json":
                return json.load(f)
            else:
                return yaml.safe_load(f) or {}

    def _save_yaml(self, path: Path, content: Dict[str, Any]):
        """Save content as YAML with header comment."""
        with open(path, "w", encoding="utf-8") as f:
            # Add migration comment
            f.write(f"# Migrated from addons system on {datetime.now().isoformat()}\n")
            f.write("# See templates/README.md for documentation\n\n")
            yaml.dump(
                content,
                f,
                default_flow_style=False,
                sort_keys=False,
                allow_unicode=True,
            )

    def _print_summary(self):
        """Print migration summary."""
        ch.newline()
        ch.header("Migration Summary")

        # Repository migrations
        if self.migrated_repo:
            ch.success(f"Repository templates migrated: {len(self.migrated_repo)}")
            for name in self.migrated_repo:
                ch.dim(f"  - {name}")

        # User migrations
        if self.migrated_user:
            ch.success(f"User overrides migrated: {len(self.migrated_user)}")
            for name in self.migrated_user:
                ch.dim(f"  - {name}")

        # Skipped
        if self.skipped:
            ch.warning(f"Skipped: {len(self.skipped)}")
            for path, reason in self.skipped:
                ch.dim(f"  - {path}: {reason}")

        # Errors
        if self.errors:
            ch.error(f"Errors: {len(self.errors)}")
            for path, error in self.errors:
                ch.dim(f"  - {path}: {error}")

        # Total
        total = len(self.migrated_repo) + len(self.migrated_user)
        if total > 0 and not self.errors:
            ch.newline()
            if self.dry_run:
                ch.info(
                    f"Would migrate {total} item(s). Run without --dry-run to apply changes."
                )
            else:
                ch.success(f"Migration complete! {total} item(s) migrated.")
        elif total == 0:
            ch.info("Nothing to migrate - system is already using templates format.")


def migrate_addons_to_templates(dry_run: bool = False, force: bool = False) -> bool:
    """
    Run the addons-to-templates migration.

    Args:
        dry_run: If True, show what would be done without making changes
        force: If True, overwrite existing YAML files

    Returns:
        True if migration completed successfully
    """
    migration = AddonToTemplateMigration(dry_run=dry_run, force=force)
    return migration.run()


# CLI integration
def migrate_addons_to_templates_cmd(options: Dict[str, Any]):
    """CLI command handler for addons-to-templates migration."""
    dry_run = options.get("dry_run", False)
    force = options.get("force", False)

    success = migrate_addons_to_templates(dry_run=dry_run, force=force)

    if not success:
        raise SystemExit(1)
