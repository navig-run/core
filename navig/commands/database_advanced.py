"""Advanced Database Operation Commands - SECURE VERSION"""

import json
import os
import re
import subprocess
from typing import Any

import typer
from rich.table import Table

from navig import console_helper as ch
from navig.commands._db_utils import create_mysql_config_file, get_db_host_port
from navig.core.proc_text import decode_console_result


def _validate_sql_identifier(identifier: str, identifier_type: str = "identifier") -> bool:
    """
    Validate SQL identifier (database, table, column name).

    Only allows: alphanumeric, underscores. No spaces, special chars, SQL keywords.

    Args:
        identifier: The identifier to validate
        identifier_type: Type for error messages (e.g., "table", "database")

    Returns:
        True if valid

    Raises:
        ValueError: If identifier is invalid or contains SQL injection attempts
    """
    if not identifier:
        raise ValueError(f"{identifier_type} name cannot be empty")

    # Only allow alphanumeric and underscores
    if not re.match(r"^[a-zA-Z0-9_]+$", identifier):
        raise ValueError(
            f"Invalid {identifier_type} name: '{identifier}'. "
            f"Only alphanumeric characters and underscores are allowed."
        )

    # Block exact reserved SQL keywords (case-insensitive)
    reserved_keywords = {
        "SELECT",
        "INSERT",
        "UPDATE",
        "DELETE",
        "DROP",
        "CREATE",
        "ALTER",
        "TRUNCATE",
        "EXEC",
        "EXECUTE",
        "UNION",
        "WHERE",
        "INFORMATION_SCHEMA",
    }

    upper_identifier = identifier.upper()
    if upper_identifier in reserved_keywords:
        raise ValueError(
            f"Invalid {identifier_type} name: '{identifier}'. "
            "Identifier cannot be a reserved SQL keyword."
        )

    # Additional length check
    if len(identifier) > 64:  # MySQL max identifier length
        raise ValueError(f"{identifier_type} name too long (max 64 characters)")

    return True


def _escape_sql_identifier(identifier: str) -> str:
    """
    Escape SQL identifier using backticks (MySQL standard).

    Even though we validate identifiers, we still escape them as defense-in-depth.
    Backticks prevent SQL injection even if validation is bypassed.

    Args:
        identifier: SQL identifier to escape

    Returns:
        Escaped identifier with backticks
    """
    # Remove any existing backticks first to prevent double-escaping
    clean_identifier = identifier.replace("`", "")
    return f"`{clean_identifier}`"



def optimize_table_cmd(table: str, options: dict[str, Any]):
    """Optimize database table.

    SECURITY:
    - Validates table name to prevent SQL injection
    - Escapes table name with backticks as defense-in-depth
    - Uses secure config file for credentials

    Args:
        table: Table name to optimize
        options: Command options (app, dry_run, json)
    """
    from navig.config import get_config_manager
    from navig.tunnel import TunnelManager

    config_manager = get_config_manager()
    tunnel_manager = TunnelManager(config_manager)

    from navig.cli.recovery import require_active_server  # noqa: PLC0415
    server_name = require_active_server(options, config_manager)

    # SECURITY: Validate table name
    try:
        _validate_sql_identifier(table, "table")
    except ValueError as e:
        ch.error(str(e))
        return False

    # Dry run mode
    if options.get("dry_run"):
        safe_table = _escape_sql_identifier(table)
        msg = f"Would optimize table: {safe_table}"
        if options.get("json"):
            ch.raw_print(json.dumps({"success": True, "dry_run": True, "table": table}))
        else:
            ch.info(f"[DRY RUN] {msg}")
        return True

    server_config = config_manager.load_server_config(server_name)
    db = server_config["database"]

    tunnel_info = None
    if not db.get("direct_host"):
        tunnel_info = tunnel_manager.get_tunnel_status(server_name)
        if not tunnel_info:
            tunnel_info = tunnel_manager.start_tunnel(server_name)

    db_host, db_port = get_db_host_port(db, tunnel_info)

    # SECURITY: Escape table name with backticks
    safe_table = _escape_sql_identifier(table)
    query = f"OPTIMIZE TABLE {safe_table};"

    config_file = None
    try:
        config_file = create_mysql_config_file(db["user"], db["password"])

        mysql_cmd = [
            "mysql",
            f"--defaults-file={config_file}",
            "-h",
            db_host,
            "-P",
            str(db_port),
            db["name"],
            "-e",
            query,
        ]

        result = decode_console_result(subprocess.run(mysql_cmd, capture_output=True))

        if result.returncode == 0:
            if options.get("json"):
                ch.raw_print(json.dumps({"success": True, "table": table, "output": result.stdout}))
            else:
                ch.success(f"✓ Optimized table: {table}")
                ch.raw_print(result.stdout)
            return True
        else:
            if options.get("json"):
                ch.raw_print(json.dumps({"success": False, "error": result.stderr}))
            else:
                ch.error(f"Optimize failed: {result.stderr}")
            return False

    except FileNotFoundError:
        ch.error("mysql client not found.")
        return False
    finally:
        if config_file and os.path.exists(config_file):
            try:
                os.unlink(config_file)
            except OSError:
                pass  # Cleanup - file deletion may fail


def repair_table_cmd(table: str, options: dict[str, Any]):
    """Repair database table.

    SECURITY:
    - Validates table name to prevent SQL injection
    - Escapes table name with backticks as defense-in-depth
    - Uses secure config file for credentials

    Args:
        table: Table name to repair
        options: Command options (app, dry_run, json)
    """
    from navig.config import get_config_manager
    from navig.tunnel import TunnelManager

    config_manager = get_config_manager()
    tunnel_manager = TunnelManager(config_manager)

    from navig.cli.recovery import require_active_server  # noqa: PLC0415
    server_name = require_active_server(options, config_manager)

    # SECURITY: Validate table name
    try:
        _validate_sql_identifier(table, "table")
    except ValueError as e:
        ch.error(str(e))
        return False

    # Dry run mode
    if options.get("dry_run"):
        safe_table = _escape_sql_identifier(table)
        msg = f"Would repair table: {safe_table}"
        if options.get("json"):
            ch.raw_print(json.dumps({"success": True, "dry_run": True, "table": table}))
        else:
            ch.info(f"[DRY RUN] {msg}")
        return True

    server_config = config_manager.load_server_config(server_name)
    db = server_config["database"]

    tunnel_info = None
    if not db.get("direct_host"):
        tunnel_info = tunnel_manager.get_tunnel_status(server_name)
        if not tunnel_info:
            tunnel_info = tunnel_manager.start_tunnel(server_name)

    db_host, db_port = get_db_host_port(db, tunnel_info)

    # SECURITY: Escape table name with backticks
    safe_table = _escape_sql_identifier(table)
    query = f"REPAIR TABLE {safe_table};"

    config_file = None
    try:
        config_file = create_mysql_config_file(db["user"], db["password"])

        mysql_cmd = [
            "mysql",
            f"--defaults-file={config_file}",
            "-h",
            db_host,
            "-P",
            str(db_port),
            db["name"],
            "-e",
            query,
        ]

        result = decode_console_result(subprocess.run(mysql_cmd, capture_output=True))

        if result.returncode == 0:
            if options.get("json"):
                ch.raw_print(json.dumps({"success": True, "table": table, "output": result.stdout}))
            else:
                ch.success(f"✓ Repaired table: {table}")
                ch.raw_print(result.stdout)
            return True
        else:
            if options.get("json"):
                ch.raw_print(json.dumps({"success": False, "error": result.stderr}))
            else:
                ch.error(f"Repair failed: {result.stderr}")
            return False

    except FileNotFoundError:
        ch.error("mysql client not found.")
        return False
    finally:
        if config_file and os.path.exists(config_file):
            try:
                os.unlink(config_file)
            except OSError:
                pass  # Cleanup - file deletion may fail


def list_users_cmd(options: dict[str, Any]):
    """List database users.

    SECURITY: No SQL injection risk - query has no user input.
    Uses secure config file for credentials.

    Args:
        options: Command options (app, json)
    """
    from navig.config import get_config_manager
    from navig.tunnel import TunnelManager

    config_manager = get_config_manager()
    tunnel_manager = TunnelManager(config_manager)

    from navig.cli.recovery import require_active_server  # noqa: PLC0415
    server_name = require_active_server(options, config_manager)

    server_config = config_manager.load_server_config(server_name)
    db = server_config["database"]

    tunnel_info = None
    if not db.get("direct_host"):
        tunnel_info = tunnel_manager.get_tunnel_status(server_name)
        if not tunnel_info:
            tunnel_info = tunnel_manager.start_tunnel(server_name)

    db_host, db_port = get_db_host_port(db, tunnel_info)

    query = "SELECT User, Host FROM mysql.user ORDER BY User, Host;"

    config_file = None
    try:
        config_file = create_mysql_config_file(db["user"], db["password"])

        mysql_cmd = [
            "mysql",
            f"--defaults-file={config_file}",
            "-h",
            db_host,
            "-P",
            str(db_port),
            "-e",
            query,
        ]

        result = decode_console_result(subprocess.run(mysql_cmd, capture_output=True))

        if result.returncode != 0:
            # `list_users_cmd` returns nothing, so its callers cannot inspect a result —
            # returning here reported success for a query that never ran. (Its siblings
            # optimize/repair DO return a bool, and their wrappers check it; a function
            # that returns nothing has to raise.) A genuinely empty listing is a
            # `ch.warning` + exit 0 below, which is a different thing entirely.
            ch.error(f"Query failed: {result.stderr}")
            raise typer.Exit(1)

        # Parse output
        lines = result.stdout.strip().split("\n")
        if len(lines) < 2:
            ch.warning("No users found.")
            return

        # Skip header
        data_lines = lines[1:]
        users = []

        for line in data_lines:
            parts = line.split("\t")
            if len(parts) >= 2:
                users.append({"user": parts[0], "host": parts[1]})

        # Output
        if options.get("json"):
            ch.raw_print(json.dumps({"users": users, "count": len(users)}))
        else:
            table = Table(title=f"Database Users on {server_name}")
            table.add_column("User", style="cyan")
            table.add_column("Host", style="yellow")

            for user_info in users:
                table.add_row(user_info["user"], user_info["host"])

            ch.console.print(table)
            ch.dim(f"\nTotal: {len(users)} users")

    except FileNotFoundError:
        ch.error("mysql client not found.")
    finally:
        if config_file and os.path.exists(config_file):
            try:
                os.unlink(config_file)
            except OSError:
                pass  # Cleanup - file deletion may fail

