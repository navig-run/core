#!/usr/bin/env python3
"""
NAVIG Configuration Migration Tool

Migrates all configuration files from Documents\.navig to ~/.navig
and consolidates into a single directory structure.
"""

import argparse
import hashlib
import shutil
import sys
from datetime import datetime
from pathlib import Path


def get_file_hash(file_path):
    """Calculate MD5 hash of a file."""
    md5 = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            md5.update(chunk)
    return md5.hexdigest()


def print_header(text):
    """Print a section header."""
    print(f"\n{'=' * 80}")
    print(text)
    print("=" * 80)


def print_success(text):
    """Print success message."""
    print(f"✓ {text}")


def print_error(text):
    """Print error message."""
    print(f"✗ {text}")


def print_warning(text):
    """Print warning message."""
    print(f"⚠ {text}")


def print_info(text):
    """Print info message."""
    print(f"ℹ {text}")


def migrate_config(dry_run=False, force=False):
    """Main migration function."""

    # Define directories
    home = Path.home()
    source_dir = home / "Documents" / ".navig"
    dest_dir = home / ".navig"
    backup_dir = (
        dest_dir / "backups" / f"migration_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    )

    # Migration report
    report = {
        "files_scanned": 0,
        "files_copied": 0,
        "files_skipped": 0,
        "files_backed_up": 0,
        "conflicts": [],
        "errors": [],
    }

    print_header("NAVIG Configuration Migration Tool")

    if dry_run:
        print_warning("DRY RUN MODE - No changes will be made")

    # Step 1: Verify directories
    print_header("Step 1: Auditing Directories")

    if not source_dir.exists():
        print_error(f"Source directory not found: {source_dir}")
        return 1

    if not dest_dir.exists():
        print_warning(f"Destination directory not found: {dest_dir}")
        print_info("Creating destination directory...")
        if not dry_run:
            dest_dir.mkdir(parents=True, exist_ok=True)
        print_success(f"Created: {dest_dir}")

    # Create backup directory
    if not dry_run:
        backup_dir.mkdir(parents=True, exist_ok=True)
        print_success(f"Created backup directory: {backup_dir}")

    # Step 2: Scan source directory
    print_header("Step 2: Scanning Source Directory")

    source_hosts = source_dir / "hosts"
    source_apps = source_dir / "apps"

    print_info(f"Source: {source_dir}")

    source_host_files = []
    if source_hosts.exists():
        source_host_files = list(source_hosts.glob("*.yaml"))
        print_info(f"  hosts/: {len(source_host_files)} YAML files")
        for f in source_host_files:
            print(f"    - {f.name}")
    else:
        print_warning("  hosts/ directory not found")

    source_app_files = []
    if source_apps.exists():
        source_app_files = list(source_apps.glob("*.yaml"))
        print_info(f"  apps/: {len(source_app_files)} YAML files")
        for f in source_app_files:
            print(f"    - {f.name}")
    else:
        print_info("  apps/ directory not found (OK)")

    # Step 3: Scan destination directory
    print_header("Step 3: Scanning Destination Directory")

    dest_hosts = dest_dir / "hosts"
    dest_apps = dest_dir / "apps"

    print_info(f"Destination: {dest_dir}")

    if dest_hosts.exists():
        dest_host_files = list(dest_hosts.glob("*.yaml"))
        print_info(f"  hosts/: {len(dest_host_files)} YAML files")
        for f in dest_host_files:
            print(f"    - {f.name}")
    else:
        print_warning("  hosts/ directory not found - will create")
        if not dry_run:
            dest_hosts.mkdir(parents=True, exist_ok=True)
        dest_host_files = []

    if dest_apps.exists():
        dest_app_files = list(dest_apps.glob("*.yaml"))
        print_info(f"  apps/: {len(dest_app_files)} YAML files")
    else:
        print_info("  apps/ directory not found (OK)")
        dest_app_files = []

    # Step 4: Analyze files
    print_header("Step 4: Analyzing Files")

    host_conflicts = []
    host_unique = []

    for source_file in source_host_files:
        report["files_scanned"] += 1
        dest_file = dest_hosts / source_file.name

        if dest_file.exists():
            # Conflict - file exists in both locations
            source_hash = get_file_hash(source_file)
            dest_hash = get_file_hash(dest_file)

            if source_hash == dest_hash:
                print_info(
                    f"  [IDENTICAL] {source_file.name} - files are identical, will skip"
                )
                report["files_skipped"] += 1
            else:
                print_warning(
                    f"  [CONFLICT] {source_file.name} - different versions exist"
                )
                host_conflicts.append(
                    {
                        "name": source_file.name,
                        "source_path": source_file,
                        "dest_path": dest_file,
                        "source_size": source_file.stat().st_size,
                        "dest_size": dest_file.stat().st_size,
                        "source_modified": datetime.fromtimestamp(
                            source_file.stat().st_mtime
                        ),
                        "dest_modified": datetime.fromtimestamp(
                            dest_file.stat().st_mtime
                        ),
                    }
                )
                report["conflicts"].append(source_file.name)
        else:
            # Unique file - only exists in source
            print_success(f"  [NEW] {source_file.name} - will copy to destination")
            host_unique.append(source_file)

    # Check app files
    app_unique = []
    for source_file in source_app_files:
        report["files_scanned"] += 1
        dest_file = dest_apps / source_file.name

        if dest_file.exists():
            source_hash = get_file_hash(source_file)
            dest_hash = get_file_hash(dest_file)

            if source_hash == dest_hash:
                print_info(
                    f"  [IDENTICAL] {source_file.name} - files are identical, will skip"
                )
                report["files_skipped"] += 1
            else:
                print_warning(
                    f"  [CONFLICT] {source_file.name} - different versions exist"
                )
                report["conflicts"].append(source_file.name)
        else:
            print_success(f"  [NEW] {source_file.name} - will copy to destination")
            app_unique.append(source_file)

    # Step 5: Handle conflicts
    if host_conflicts:
        print_header("Step 5: Resolving Conflicts")

        for conflict in host_conflicts:
            print(f"\nConflict: {conflict['name']}")
            print(f"  Source:      {conflict['source_path']}")
            print(f"    Size:      {conflict['source_size']} bytes")
            print(f"    Modified:  {conflict['source_modified']}")
            print(f"  Destination: {conflict['dest_path']}")
            print(f"    Size:      {conflict['dest_size']} bytes")
            print(f"    Modified:  {conflict['dest_modified']}")

            # Use newer file by default
            use_source = conflict["source_modified"] > conflict["dest_modified"]

            if use_source:
                print_info("  → Source is newer, will use source version")
            else:
                print_info("  → Destination is newer, will keep destination version")

            if not force and not dry_run:
                choice = input(
                    "  Use [S]ource, [D]estination, or [K]eep destination? (default: {}) ".format(
                        "S" if use_source else "D"
                    )
                )
                if choice.upper() == "S":
                    use_source = True
                elif choice.upper() == "D" or choice.upper() == "K":
                    use_source = False

            if use_source:
                print_info("  → Backing up destination version...")
                if not dry_run:
                    backup_path = backup_dir / "hosts" / conflict["name"]
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(conflict["dest_path"], backup_path)
                    report["files_backed_up"] += 1

                print_info("  → Copying source version to destination...")
                if not dry_run:
                    shutil.copy2(conflict["source_path"], conflict["dest_path"])
                    report["files_copied"] += 1
                print_success("  Resolved: Using source version")
            else:
                print_success("  Resolved: Keeping destination version")
                report["files_skipped"] += 1
    else:
        print_header("Step 5: Resolving Conflicts")
        print_success("No conflicts found!")

    # Step 6: Copy unique files
    print_header("Step 6: Copying Unique Files")

    if not host_unique and not app_unique:
        print_info("No unique files to copy")
    else:
        # Copy unique host files
        for file in host_unique:
            print_info(f"Copying: {file.name} → hosts/")
            if not dry_run:
                try:
                    shutil.copy2(file, dest_hosts / file.name)
                    report["files_copied"] += 1
                    print_success("  Copied successfully")
                except Exception as e:
                    print_error(f"  Failed to copy: {e}")
                    report["errors"].append(f"Failed to copy {file.name}: {e}")

        # Copy unique app files
        for file in app_unique:
            print_info(f"Copying: {file.name} → apps/")
            if not dry_run:
                try:
                    if not dest_apps.exists():
                        dest_apps.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(file, dest_apps / file.name)
                    report["files_copied"] += 1
                    print_success("  Copied successfully")
                except Exception as e:
                    print_error(f"  Failed to copy: {e}")
                    report["errors"].append(f"Failed to copy {file.name}: {e}")

    # Step 7: Verify migration
    print_header("Step 7: Verifying Migration")

    if not dry_run:
        print_info("Checking destination directory...")

        final_host_files = list(dest_hosts.glob("*.yaml"))
        final_app_files = list(dest_apps.glob("*.yaml")) if dest_apps.exists() else []

        print_success("Destination now contains:")
        print(f"  hosts/: {len(final_host_files)} YAML files")
        for f in final_host_files:
            print(f"    - {f.name}")

        if final_app_files:
            print(f"  apps/: {len(final_app_files)} YAML files")
            for f in final_app_files:
                print(f"    - {f.name}")
    else:
        print_info("Skipping verification (dry run mode)")

    # Step 8: Clean up source directory
    print_header("Step 8: Cleaning Up Source Directory")

    if dry_run:
        print_warning(f"Would delete: {source_dir}")
    else:
        if report["errors"]:
            print_error(
                "Migration had errors - NOT deleting source directory for safety"
            )
            print_warning("Please review errors and run migration again")
        else:
            print_warning(f"About to delete: {source_dir}")

            if not force:
                confirm = input(
                    "Are you sure you want to delete the source directory? [y/N] "
                )
                if confirm.lower() != "y":
                    print_info("Skipping deletion - source directory preserved")
                    print_info("You can manually delete it later if needed")
                else:
                    print_info("Deleting source directory...")
                    shutil.rmtree(source_dir)
                    print_success("Source directory deleted")
            else:
                print_info("Deleting source directory (--force mode)...")
                shutil.rmtree(source_dir)
                print_success("Source directory deleted")

    # Step 9: Generate migration report
    print_header("Migration Report")

    print("\nSummary:")
    print(f"  Files scanned:    {report['files_scanned']}")
    print(f"  Files copied:     {report['files_copied']}")
    print(f"  Files skipped:    {report['files_skipped']}")
    print(f"  Files backed up:  {report['files_backed_up']}")
    print(f"  Conflicts found:  {len(report['conflicts'])}")
    print(f"  Errors:           {len(report['errors'])}")

    if report["conflicts"]:
        print("\nConflicts resolved:")
        for conflict in report["conflicts"]:
            print(f"  - {conflict}")

    if report["errors"]:
        print("\nErrors encountered:")
        for error in report["errors"]:
            print(f"  - {error}")

    if not dry_run and report["files_backed_up"] > 0:
        print("\nBackups saved to:")
        print(f"  {backup_dir}")

    print("\nDirectories:")
    print(
        f"  Source:      {source_dir} {'[EXISTS]' if source_dir.exists() else '[DELETED]'}"
    )
    print(f"  Destination: {dest_dir} [ACTIVE]")

    if dry_run:
        print()
        print_warning("DRY RUN COMPLETE - No changes were made")
        print_info("Run without --dry-run to perform the migration")
    else:
        print()
        print_success("Migration complete!")

        if source_dir.exists():
            print_info("\nNext steps:")
            print_info("  1. Verify all hosts appear in 'navig menu'")
            print_info("  2. Test host configurations")
            print_info("  3. Manually delete source directory if everything works")
        else:
            print_info("\nNext steps:")
            print_info("  1. Verify all hosts appear in 'navig menu'")
            print_info("  2. Test host configurations")
            print_info(f"  3. All done! Configuration consolidated to: {dest_dir}")

    print("=" * 80)

    return 0 if not report["errors"] else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Migrate NAVIG configuration from Documents\\.navig to ~/.navig"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be done without making any changes",
    )
    parser.add_argument(
        "--force", action="store_true", help="Skip confirmation prompts"
    )

    args = parser.parse_args()

    sys.exit(migrate_config(dry_run=args.dry_run, force=args.force))
