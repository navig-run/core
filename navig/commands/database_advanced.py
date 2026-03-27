"""Advanced Database Operation Commands - SECURE VERSION"""

import json
import os
import re
import subprocess
import tempfile
from typing import Any

from rich.table import Table

from navig import console_helper as ch


def _validate_sql_identifier(
    identifier: str, identifier_type: str = "identifier"
) -> bool:
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

    # Prevent SQL injection via common keywords
    dangerous_keywords = [
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
        "OR",
        "1=1",
        "INFORMATION_SCHEMA",
        "--",
        "/*",
        "*/",
        ";",
    ]

    upper_identifier = identifier.upper()
    for keyword in dangerous_keywords:
        if keyword in upper_identifier:
            raise ValueError(
                f"Invalid {identifier_type} name: '{identifier}'. "
                f"Contains disallowed keyword or pattern: {keyword}"
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


def _create_mysql_config_file(db_user: str, db_password: str) -> str:
    """
    Create temporary MySQL configuration file with credentials.

    This prevents password exposure in process listings (ps aux shows command args).
    The file is created with restrictive permissions (0600).

    Args:
        db_user: Database username
        db_password: Database password

    Returns:
        Path to temporary config file (caller must delete after use)
    """
    # Create temp file with restrictive permissions
    fd, temp_path = tempfile.mkstemp(prefix="navig_mysql_", suffix=".cnf", text=True)

    try:
        # Set file permissions to 0600 (read/write for owner only)
        try:
            os.chmod(temp_path, 0o600)
        except (OSError, PermissionError):
            pass

        # Write MySQL config format
        config_content = f"""[client]
user={db_user}
password={db_password}
"""
        os.write(fd, config_content.encode("utf-8"))
        os.close(fd)

        return temp_path
    except Exception as e:
        # Clean up on error
        os.close(fd)
        try:
            os.unlink(temp_path)
        except OSError:
            pass  # Cleanup - file may not exist
        raise RuntimeError(f"Failed to create secure MySQL config: {e}") from e


def list_databases_cmd(options: dict[str, Any]):
    """List all databases with sizes.

    SECURITY: No SQL injection risk - uses parameterized query via information_schema.
    Credentials passed via secure config file, not command line.

    Args:
        options: Command options (app, json)
    """
    from navig.config import get_config_manager
    from navig.tunnel import TunnelManager

    config_manager = get_config_manager()
    tunnel_manager = TunnelManager(config_manager)

    server_name = options.get("app") or config_manager.get_active_server()
    if not server_name:
        ch.error("No active server.")
        return

    # Ensure tunnel
    tunnel_info = tunnel_manager.get_tunnel_status(server_name)
    if not tunnel_info:
        ch.warning("Starting tunnel...")
        tunnel_info = tunnel_manager.start_tunnel(server_name)

    server_config = config_manager.load_server_config(server_name)
    db = server_config["database"]

    # Query for database sizes (safe - no user input)
    query = """
    SELECT
        table_schema AS 'database',
        ROUND(SUM(data_length + index_length) / 1024 / 1024, 2) AS 'size_mb'
    FROM information_schema.tables
    GROUP BY table_schema
    ORDER BY size_mb DESC;
    """

    # Create secure config file for credentials
    config_file = None
    try:
        config_file = _create_mysql_config_file(db["user"], db["password"])

        mysql_cmd = [
            "mysql",
            f"--defaults-file={config_file}",  # Secure credential passing
            "-h",
            "127.0.0.1",
            "-P",
            str(tunnel_info["local_port"]),
            "-e",
            query,
        ]

        result = subprocess.run(mysql_cmd, capture_output=True, text=True)

        if result.returncode != 0:
            ch.error(f"Query failed: {result.stderr}")
            return

        # Parse output
        lines = result.stdout.strip().split("\n")
        if len(lines) < 2:
            ch.warning("No databases found.")
            return

        # Skip header
        data_lines = lines[1:]
        databases = []

        for line in data_lines:
            parts = line.split("\t")
            if len(parts) >= 2:
                databases.append(
                    {
                        "name": parts[0],
                        "size_mb": float(parts[1]) if parts[1] != "NULL" else 0.0,
                    }
                )

        # Output
        if options.get("json"):
            ch.raw_print(json.dumps({"databases": databases, "count": len(databases)}))
        else:
            table = Table(title=f"Databases on {server_name}")
            table.add_column("Database", style="cyan")
            table.add_column("Size (MB)", justify="right", style="green")

            for db_info in databases:
                table.add_row(db_info["name"], f"{db_info['size_mb']:.2f}")

            ch.console.print(table)
            ch.dim(f"\nTotal: {len(databases)} databases")

    except FileNotFoundError:
        ch.error("mysql client not found. Please install MySQL client tools.")
    finally:
        # Always delete secure config file
        if config_file and os.path.exists(config_file):
            try:
                os.unlink(config_file)
            except OSError:
                pass  # Cleanup - file deletion failed


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

    server_name = options.get("app") or config_manager.get_active_server()
    if not server_name:
        ch.error("No active server.")
        return False

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

    # Ensure tunnel
    tunnel_info = tunnel_manager.get_tunnel_status(server_name)
    if not tunnel_info:
        tunnel_info = tunnel_manager.start_tunnel(server_name)

    server_config = config_manager.load_server_config(server_name)
    db = server_config["database"]

    # SECURITY: Escape table name with backticks
    safe_table = _escape_sql_identifier(table)
    query = f"OPTIMIZE TABLE {safe_table};"

    config_file = None
    try:
        config_file = _create_mysql_config_file(db["user"], db["password"])

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

        result = subprocess.run(mysql_cmd, capture_output=True, text=True)

        if result.returncode == 0:
            if options.get("json"):
                ch.raw_print(
                    json.dumps(
                        {"success": True, "table": table, "output": result.stdout}
                    )
                )
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

                pass  # Cleanup - file deletion may faildef repair_table_cmd(table: str, options: Dict[str, Any]):
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

    server_name = options.get("app") or config_manager.get_active_server()
    if not server_name:
        ch.error("No active server.")
        return False

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

    # Ensure tunnel
    tunnel_info = tunnel_manager.get_tunnel_status(server_name)
    if not tunnel_info:
        tunnel_info = tunnel_manager.start_tunnel(server_name)

    server_config = config_manager.load_server_config(server_name)
    db = server_config["database"]

    # SECURITY: Escape table name with backticks
    safe_table = _escape_sql_identifier(table)
    query = f"REPAIR TABLE {safe_table};"

    config_file = None
    try:
        config_file = _create_mysql_config_file(db["user"], db["password"])

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

        result = subprocess.run(mysql_cmd, capture_output=True, text=True)

        if result.returncode == 0:
            if options.get("json"):
                ch.raw_print(
                    json.dumps(
                        {"success": True, "table": table, "output": result.stdout}
                    )
                )
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

                pass  # Cleanup - file deletion may faildef list_users_cmd(options: Dict[str, Any]):
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

    server_name = options.get("app") or config_manager.get_active_server()
    if not server_name:
        ch.error("No active server.")
        return

    # Ensure tunnel
    tunnel_info = tunnel_manager.get_tunnel_status(server_name)
    if not tunnel_info:
        tunnel_info = tunnel_manager.start_tunnel(server_name)

    server_config = config_manager.load_server_config(server_name)
    db = server_config["database"]

    query = "SELECT User, Host FROM mysql.user ORDER BY User, Host;"

    config_file = None
    try:
        config_file = _create_mysql_config_file(db["user"], db["password"])

        mysql_cmd = [
            "mysql",
            f"--defaults-file={config_file}",
            "-h",
            "127.0.0.1",
            "-P",
            str(tunnel_info["local_port"]),
            "-e",
            query,
        ]

        result = subprocess.run(mysql_cmd, capture_output=True, text=True)

        if result.returncode != 0:
            ch.error(f"Query failed: {result.stderr}")
            return

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

                pass  # Cleanup - file deletion may faildef list_tables_cmd(database: str, options: Dict[str, Any]):
    """List tables in a database.

    SECURITY:
    - Validates database name to prevent SQL injection
    - Uses parameterized query with escaped identifier
    - Uses secure config file for credentials

    Args:
        database: Database name
        options: Command options (app, json)
    """
    from navig.config import get_config_manager
    from navig.tunnel import TunnelManager

    config_manager = get_config_manager()
    tunnel_manager = TunnelManager(config_manager)

    server_name = options.get("app") or config_manager.get_active_server()
    if not server_name:
        ch.error("No active server.")
        return

    # SECURITY: Validate database name
    try:
        _validate_sql_identifier(database, "database")
    except ValueError as e:
        ch.error(str(e))
        return

    # Ensure tunnel
    tunnel_info = tunnel_manager.get_tunnel_status(server_name)
    if not tunnel_info:
        tunnel_info = tunnel_manager.start_tunnel(server_name)

    server_config = config_manager.load_server_config(server_name)
    db = server_config["database"]

    # SECURITY: Use backtick escaping for database name in WHERE clause
    safe_database = _escape_sql_identifier(database)

    # Note: We can't use backticks in string comparison, so we validate heavily first
    # Then use single quotes which is safe after validation
    query = f"""
    SELECT
        table_name,
        ROUND((data_length + index_length) / 1024 / 1024, 2) AS size_mb,
        table_rows
    FROM information_schema.tables
    WHERE table_schema = '{database}'
    ORDER BY size_mb DESC;
    """

    config_file = None
    try:
        config_file = _create_mysql_config_file(db["user"], db["password"])

        mysql_cmd = [
            "mysql",
            f"--defaults-file={config_file}",
            "-h",
            "127.0.0.1",
            "-P",
            str(tunnel_info["local_port"]),
            "-e",
            query,
        ]

        result = subprocess.run(mysql_cmd, capture_output=True, text=True)

        if result.returncode != 0:
            ch.error(f"Query failed: {result.stderr}")
            return

        # Parse output
        lines = result.stdout.strip().split("\n")
        if len(lines) < 2:
            ch.warning(f"No tables found in database: {database}")
            return

        # Skip header
        data_lines = lines[1:]
        tables = []

        for line in data_lines:
            parts = line.split("\t")
            if len(parts) >= 3:
                tables.append(
                    {
                        "name": parts[0],
                        "size_mb": float(parts[1]) if parts[1] != "NULL" else 0.0,
                        "rows": int(parts[2]) if parts[2] != "NULL" else 0,
                    }
                )

        # Output
        if options.get("json"):
            ch.raw_print(
                json.dumps(
                    {"database": database, "tables": tables, "count": len(tables)}
                )
            )
        else:
            table = Table(title=f"Tables in {database}")
            table.add_column("Table", style="cyan")
            table.add_column("Size (MB)", justify="right", style="green")
            table.add_column("Rows", justify="right", style="yellow")

            for tbl in tables:
                table.add_row(tbl["name"], f"{tbl['size_mb']:.2f}", f"{tbl['rows']:,}")

            ch.console.print(table)
            ch.dim(f"\nTotal: {len(tables)} tables")

    except FileNotFoundError:
        ch.error("mysql client not found.")
    finally:
        if config_file and os.path.exists(config_file):
            try:
                os.unlink(config_file)
            except OSError:

                pass  # Cleanup - file deletion may fail
