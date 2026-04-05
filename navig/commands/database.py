"""Database Commands - Execute SQL through encrypted tunnels"""

import json
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from navig import console_helper as ch


def _create_mysql_config_file(user: str, password: str) -> str:
    """
    Create temporary MySQL config file with credentials.
    Returns path to config file.
    SECURITY: Prevents password from appearing in process listings.
    """
    fd, config_path = tempfile.mkstemp(suffix=".cnf", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("[client]\n")
            f.write(f"user={user}\n")
            f.write(f"password={password}\n")
        # Set restrictive permissions (owner read/write only)
        try:
            os.chmod(config_path, 0o600)
        except (OSError, PermissionError):
            pass  # best-effort: skip on access/IO error
        return config_path
    except Exception:
        try:
            os.unlink(config_path)
        except OSError:
            pass  # best-effort cleanup
        raise


def execute_sql(query: str, options: dict[str, Any]):
    """Execute SQL query through tunnel."""
    from navig.config import get_config_manager
    from navig.tunnel import TunnelManager

    config_manager = get_config_manager()
    tunnel_manager = TunnelManager(config_manager)

    from navig.cli.recovery import require_active_server
    server_name = require_active_server(options, config_manager)

    dry_run = options.get("dry_run", False)
    json_enabled = options.get("json", False)

    if dry_run:
        if json_enabled:
            ch.raw_print(json.dumps({"dry_run": True, "action": "execute_sql", "query": query}))
        else:
            ch.info(f"[DRY RUN] Would execute SQL: {query}")
        return

    # Check if SQL query requires confirmation based on configured level
    query_type = ch.classify_sql(query)
    query_preview = query if len(query) < 80 else query[:80] + "..."

    if not ch.confirm_operation(
        operation_name=f"SQL: {query_preview}",
        operation_type=query_type,
        host=server_name,
        auto_confirm=options.get("yes", False),
        force_confirm=options.get("confirm", False),
    ):
        ch.warning("Cancelled.")
        return

    # Ensure tunnel is running
    tunnel_info = tunnel_manager.get_tunnel_status(server_name)
    if not tunnel_info:
        ch.warning("Starting tunnel...")
        tunnel_info = tunnel_manager.start_tunnel(server_name)

    server_config = config_manager.load_server_config(server_name)
    db = server_config["database"]

    # Create secure config file (prevents password in process listings)
    config_file = _create_mysql_config_file(db["user"], db["password"])

    try:
        # Execute via mysql client
        mysql_cmd = [
            "mysql",
            f"--defaults-file={config_file}",
            "-h",
            "127.0.0.1",
            "-P",
            str(tunnel_info["local_port"]),
            db["name"],
            "-e",
            query,
        ]

        try:
            result = subprocess.run(mysql_cmd, capture_output=True, text=True)
            if json_enabled:
                ch.raw_print(
                    json.dumps(
                        {
                            "query": query,
                            "success": result.returncode == 0,
                            "output": result.stdout if result.returncode == 0 else None,
                            "error": result.stderr if result.returncode != 0 else None,
                        }
                    )
                )
            else:
                if result.returncode == 0:
                    ch.raw_print(result.stdout)
                else:
                    ch.error(f"SQL Error: {result.stderr}")
        except FileNotFoundError:
            if json_enabled:
                ch.raw_print(json.dumps({"success": False, "error": "mysql client not found"}))
            else:
                ch.error("mysql client not found. Please install MySQL client tools.")
    finally:
        # Always cleanup temp config file
        try:
            os.unlink(config_file)
        except OSError:
            pass  # best-effort cleanup


def execute_sql_file(file: Path, options: dict[str, Any]):
    """Execute SQL file through tunnel."""
    if not file.exists():
        ch.error(f"File not found: {file}")
        return

    query = file.read_text()
    execute_sql(query, options)


def backup_database(path: Path | None, options: dict[str, Any]):
    """Backup database."""
    from navig.config import get_config_manager
    from navig.tunnel import TunnelManager

    config_manager = get_config_manager()
    tunnel_manager = TunnelManager(config_manager)

    from navig.cli.recovery import require_active_server
    server_name = require_active_server(options, config_manager)

    dry_run = options.get("dry_run", False)
    json_enabled = options.get("json", False)

    # Default path
    if path is None:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = config_manager.backups_dir / f"{server_name}_{timestamp}.sql"

    # Ensure tunnel
    tunnel_info = tunnel_manager.get_tunnel_status(server_name)
    if not tunnel_info:
        tunnel_info = tunnel_manager.start_tunnel(server_name)

    server_config = config_manager.load_server_config(server_name)
    db = server_config["database"]

    if dry_run:
        if json_enabled:
            ch.raw_print(
                json.dumps(
                    {
                        "dry_run": True,
                        "action": "backup",
                        "database": db["name"],
                        "path": str(path),
                    }
                )
            )
        else:
            ch.info(f"[DRY RUN] Would backup database: {db['name']}")
            ch.dim(f"[DRY RUN] Destination: {path}")
        return

    if not json_enabled:
        ch.info(f"Creating backup: {path}")

    # Create secure config file (prevents password in process listings)
    config_file = _create_mysql_config_file(db["user"], db["password"])

    try:
        mysqldump_cmd = [
            "mysqldump",
            f"--defaults-file={config_file}",
            "-h",
            "127.0.0.1",
            "-P",
            str(tunnel_info["local_port"]),
            db["name"],
        ]

        try:
            with open(path, "w", encoding="utf-8") as f:
                result = subprocess.run(mysqldump_cmd, stdout=f, stderr=subprocess.PIPE, text=True)

            if result.returncode == 0:
                size = path.stat().st_size
                if json_enabled:
                    ch.raw_print(
                        json.dumps(
                            {
                                "success": True,
                                "database": db["name"],
                                "path": str(path),
                                "size_bytes": size,
                            }
                        )
                    )
                else:
                    ch.success(f"✓ Backup complete: {size:,} bytes")
            else:
                if json_enabled:
                    ch.raw_print(json.dumps({"success": False, "error": result.stderr}))
                else:
                    ch.error(f"Backup failed: {result.stderr}")
        except FileNotFoundError:
            if json_enabled:
                ch.raw_print(json.dumps({"success": False, "error": "mysqldump not found"}))
            else:
                ch.error("mysqldump not found. Please install MySQL client tools.")
            ch.info("")
            ch.info("Installation instructions:")
            ch.info("  Windows: choco install mysql")
            ch.info("  macOS:   brew install mysql-client")
            ch.info("  Ubuntu:  sudo apt-get install mysql-client")
            ch.info("  CentOS:  sudo yum install mysql")
            ch.info("")
            ch.info("After installation, restart your terminal.")
    finally:
        # Always cleanup temp config file
        try:
            os.unlink(config_file)
        except OSError:
            pass  # best-effort cleanup


def restore_database(file: Path, options: dict[str, Any]):
    """Restore database from backup file with transaction support."""
    from navig.config import get_config_manager
    from navig.tunnel import TunnelManager

    if not file.exists():
        ch.error(f"File not found: {file}")
        return

    config_manager = get_config_manager()
    tunnel_manager = TunnelManager(config_manager)

    from navig.cli.recovery import require_active_server
    server_name = require_active_server(options, config_manager)

    dry_run = options.get("dry_run", False)
    json_enabled = options.get("json", False)

    # Safety check - verify backup file integrity if checksum exists
    backup_dir = file.parent
    metadata_file = backup_dir / "metadata.json"

    if dry_run:
        if json_enabled:
            ch.raw_print(
                json.dumps(
                    {
                        "dry_run": True,
                        "action": "restore",
                        "file": str(file),
                        "warning": "This will OVERWRITE the database",
                    }
                )
            )
        else:
            ch.warning(f"[DRY RUN] Would restore database from: {file}")
            ch.warning("[DRY RUN] This will REPLACE all data in the database")
        return

    if metadata_file.exists():
        try:
            with open(metadata_file) as f:
                metadata = json.load(f)
                # Find checksum for this file
                for db_info in metadata.get("databases", []):
                    if db_info.get("file") == file.name and "checksum" in db_info:
                        ch.info("Verifying backup integrity...")
                        actual_checksum = _calculate_file_checksum(file)
                        if actual_checksum != db_info["checksum"]:
                            ch.error("Backup file corrupted! Checksum mismatch.")
                            ch.error(f"Expected: {db_info['checksum'][:16]}...")
                            ch.error(f"Actual: {actual_checksum[:16]}...")
                            if not options.get("force"):
                                ch.error("Restore cancelled. Use --force to override.")
                                return
                        else:
                            ch.success("✓ Backup integrity verified")
                        break
        except (json.JSONDecodeError, OSError) as e:
            ch.warning(f"Could not verify backup integrity: {e}")

    ch.warning("⚠️  DESTRUCTIVE OPERATION")
    ch.warning("   This will REPLACE all data in the database")
    ch.warning(f"   Backup file: {file.name}")
    ch.warning(f"   Size: {file.stat().st_size / (1024 * 1024):.2f} MB")

    if not options.get("yes"):
        confirm = input("\nType 'RESTORE' to confirm: ")
        if confirm != "RESTORE":
            if json_enabled:
                ch.raw_print(json.dumps({"success": False, "cancelled": True}))
            else:
                ch.warning("Restore cancelled.")
            return

    # Ensure tunnel
    tunnel_info = tunnel_manager.get_tunnel_status(server_name)
    if not tunnel_info:
        ch.info("Starting tunnel...")
        tunnel_info = tunnel_manager.start_tunnel(server_name)

    server_config = config_manager.load_server_config(server_name)
    db = server_config["database"]

    # Create backup of current state before restore
    if not options.get("no_backup"):
        ch.info("Creating safety backup of current database...")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safety_backup = config_manager.backups_dir / f"{server_name}_pre_restore_{timestamp}.sql"
        backup_database(safety_backup, options)
        ch.success(f"✓ Safety backup created: {safety_backup.name}")

    ch.info("Restoring database...")

    # Create secure config file
    config_file = _create_mysql_config_file(db["user"], db["password"])

    try:
        # Restore with error handling
        mysql_cmd = [
            "mysql",
            f"--defaults-file={config_file}",
            "-h",
            "127.0.0.1",
            "-P",
            str(tunnel_info["local_port"]),
            db["name"],
        ]

        try:
            with open(file) as f:
                result = subprocess.run(mysql_cmd, stdin=f, capture_output=True, text=True)

            if result.returncode == 0:
                if json_enabled:
                    output = {
                        "success": True,
                        "database": db["name"],
                        "source": str(file),
                    }
                    if not options.get("no_backup"):
                        output["safety_backup"] = str(safety_backup.name)
                    ch.raw_print(json.dumps(output))
                else:
                    ch.success("✅ Database restored successfully")
                    ch.info(f"   Restored from: {file.name}")
                    if not options.get("no_backup"):
                        ch.info(f"   Safety backup: {safety_backup.name}")
            else:
                if json_enabled:
                    ch.raw_print(json.dumps({"success": False, "error": result.stderr}))
                else:
                    ch.error("❌ Restore failed")
                    ch.error(f"Error: {result.stderr}")
                    if not options.get("no_backup"):
                        ch.warning(f"You can rollback using: navig restore {safety_backup}")

        except FileNotFoundError:
            if json_enabled:
                ch.raw_print(json.dumps({"success": False, "error": "mysql client not found"}))
            else:
                ch.error("mysql client not found. Please install MySQL client tools.")
    finally:
        # Always cleanup temp config file
        try:
            os.unlink(config_file)
        except OSError:
            pass  # best-effort cleanup


def _calculate_file_checksum(file_path: Path, algorithm: str = "sha256") -> str:
    """Calculate checksum for file integrity verification."""
    import hashlib

    hash_obj = hashlib.new(algorithm)
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            hash_obj.update(chunk)
    return hash_obj.hexdigest()
