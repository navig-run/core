"""
Database Commands - SSH-based database management for Docker and native databases.

Supports:
- MySQL/MariaDB (native and Docker)
- PostgreSQL (native and Docker)

Commands execute directly on the remote server via SSH, with special handling
for databases running inside Docker containers.
"""

import json
import shlex
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from navig import console_helper as ch
from navig.registry.meta import command_meta

# ============================================================================
# DATABASE TYPE DETECTION
# ============================================================================


def _detect_db_type(discovery, container: str | None = None) -> str | None:
    """
    Detect database type (mysql, mariadb, postgresql).

    Args:
        discovery: ServerDiscovery instance
        container: Optional Docker container name

    Returns:
        Database type string or None if not detected
    """
    if container:
        # Check inside Docker container
        c_safe = _escape_for_shell(container)
        cmd = f"docker exec {c_safe} sh -c 'which mysql || which mariadb || which psql' 2>/dev/null"
    else:
        cmd = "which mysql || which mariadb || which psql 2>/dev/null"

    success, stdout, _ = discovery._execute_ssh(cmd)

    if success and stdout:
        if "psql" in stdout:
            return "postgresql"
        elif "mariadb" in stdout or "mysql" in stdout:
            # Check if it's MariaDB specifically
            if container:
                c_safe = _escape_for_shell(container)
                version_cmd = f"docker exec {c_safe} mysql --version 2>/dev/null"
            else:
                version_cmd = "mysql --version 2>/dev/null"
            _, version_out, _ = discovery._execute_ssh(version_cmd)
            if version_out and "mariadb" in version_out.lower():
                return "mariadb"
            return "mysql"
    return None


def _list_docker_db_containers(discovery) -> list[dict[str, str]]:
    """
    List Docker containers running database services.

    Returns:
        List of dicts with container info: {name, image, status, db_type}
    """
    # Get running containers with database images
    cmd = "docker ps --format '{{.Names}}|{{.Image}}|{{.Status}}' 2>/dev/null"
    success, stdout, _ = discovery._execute_ssh(cmd)

    if not success or not stdout:
        return []

    containers = []
    db_images = ["mysql", "mariadb", "postgres", "postgresql", "mongo", "redis"]

    for line in stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|")
        if len(parts) >= 3:
            name, image, status = parts[0], parts[1], parts[2]
            # Check if image is a database
            image_lower = image.lower()
            db_type = None
            for db in db_images:
                if db in image_lower:
                    db_type = db
                    if db == "postgres":
                        db_type = "postgresql"
                    break

            if db_type:
                containers.append(
                    {"name": name, "image": image, "status": status, "db_type": db_type}
                )

    return containers


# ============================================================================
# CREDENTIAL RESOLUTION
# ============================================================================


def _get_db_credentials_from_config(
    config_manager,
    host_name: str,
    user: str | None = None,
    password: str | None = None,
    db_type: str | None = None,
) -> tuple[str, str | None, str | None]:
    """
    Get database credentials from app or host configuration.

    Priority:
    1. Command-line arguments (user, password) - highest priority
    2. Active app's database configuration
    3. Host's database root credentials
    4. Default to 'root' user with no password

    Args:
        config_manager: ConfigManager instance
        host_name: Host name
        user: User from command-line (overrides config)
        password: Password from command-line (overrides config)
        db_type: Database type hint

    Returns:
        Tuple of (user, password, db_type)
    """
    resolved_user = user
    resolved_password = password
    resolved_db_type = db_type

    # Try to get credentials from active app config
    try:
        active_app = config_manager.get_active_app()
        if active_app:
            app_config = config_manager.load_app_config(host_name, active_app)
            db_config = app_config.get("database", {})

            # Use app credentials if not provided via command-line
            if not resolved_user or resolved_user == "root":
                resolved_user = db_config.get("user") or resolved_user
            if not resolved_password:
                resolved_password = db_config.get("password")
            if not resolved_db_type:
                resolved_db_type = db_config.get("type")
    except (FileNotFoundError, ValueError):
        pass  # No active app or app not found

    # Try to get root credentials from host config
    if not resolved_password:
        try:
            host_config = config_manager.load_host_config(host_name)
            host_db_config = host_config.get("database", {})

            # Use host root credentials if user is 'root' and no password provided
            if (not resolved_user or resolved_user == "root") and host_db_config.get(
                "root_user"
            ):
                resolved_user = host_db_config.get("root_user")
                resolved_password = host_db_config.get("root_password")

            if not resolved_db_type:
                resolved_db_type = host_db_config.get("type")
        except FileNotFoundError:
            pass  # Host not found

    # Default to 'root' if no user specified
    if not resolved_user:
        resolved_user = "root"

    return resolved_user, resolved_password, resolved_db_type


# ============================================================================
# CORE DATABASE OPERATIONS
# ============================================================================


def _escape_for_shell(value: str) -> str:
    """
    Escape a value for safe shell execution.

    Uses shlex.quote() to properly escape special characters like:
    $ ! ( ) [ ] { } & | ; < > ' " \\ * ? ~ # @ %

    Args:
        value: String value to escape

    Returns:
        Shell-safe escaped string
    """
    return shlex.quote(value)


def _build_db_command(
    db_type: str,
    query: str,
    user: str = "root",
    password: str | None = None,
    database: str | None = None,
    container: str | None = None,
) -> str:
    """
    Build database command string for execution.

    Uses proper shell escaping for passwords with special characters.

    Args:
        db_type: mysql, mariadb, or postgresql
        query: SQL query to execute
        user: Database user
        password: Database password
        database: Database name (optional)
        container: Docker container name (optional)
    """
    # Escape user and password for shell safety
    escaped_user = _escape_for_shell(user)
    escaped_password = _escape_for_shell(password) if password else None
    escaped_query = _escape_for_shell(query)

    if db_type in ("mysql", "mariadb"):
        # Build MySQL/MariaDB command
        # Use mariadb command for MariaDB to avoid deprecation warnings
        cmd_name = "mariadb" if db_type == "mariadb" else "mysql"

        # Use -p with password attached (no space) for MySQL compatibility
        if password:
            # Format: mysql/mariadb -u'user' -p'password' [database] -e 'query'
            db_cmd = f"{cmd_name} -u{escaped_user} -p{escaped_password}"
        else:
            db_cmd = f"{cmd_name} -u{escaped_user}"

        if database:
            db_cmd += f" {_escape_for_shell(database)}"

        db_cmd += f" -e {escaped_query}"
    else:
        # PostgreSQL
        # PostgreSQL uses PGPASSWORD env var for password
        cmd = f"psql -U {escaped_user}"
        if database:
            cmd += f" -d {_escape_for_shell(database)}"
        cmd += f" -c {escaped_query}"

        if password:
            # Set PGPASSWORD env var before command
            db_cmd = f"PGPASSWORD={escaped_password} {cmd}"
        else:
            db_cmd = cmd

    if container:
        # Wrap in docker exec - need to escape the entire command for sh -c
        # The inner command is already escaped, so we just wrap it
        db_cmd = f"docker exec {_escape_for_shell(container)} sh -c {_escape_for_shell(db_cmd)}"

    return db_cmd


def _execute_db_query(
    discovery,
    query: str,
    db_type: str,
    user: str = "root",
    password: str | None = None,
    database: str | None = None,
    container: str | None = None,
) -> tuple[bool, str, str]:
    """Execute a database query via SSH."""
    cmd = _build_db_command(db_type, query, user, password, database, container)
    return discovery._execute_ssh(cmd)


def _resolve_host_discovery(
    options: dict[str, Any],
) -> tuple[str, Any, Any] | None:
    """
    Shared bootstrap for DB commands: resolve the active host, build the SSH
    config, and return ``(host_name, config_manager, discovery)``.

    Returns *None* (after emitting an error) when the host cannot be found.
    """
    from navig.config import get_config_manager
    from navig.discovery import ServerDiscovery
    from navig.cli.recovery import require_active_host

    config_manager = get_config_manager()
    host_name = require_active_host(options, config_manager)

    host_config = config_manager.load_host_config(host_name)
    if not host_config:
        ch.error(f"Host not found: {host_name}")
        return None

    debug_logger = options.get("debug_logger")
    ssh_config = {
        "host": host_config.get("host", host_config.get("hostname")),
        "user": host_config.get("user", "root"),
        "port": host_config.get("port", 22),
        "ssh_key": host_config.get("ssh_key"),
        "ssh_password": host_config.get("ssh_password"),
    }
    discovery = ServerDiscovery(ssh_config, debug_logger=debug_logger)
    return host_name, config_manager, discovery


def _emit_db_json(payload: dict[str, Any]) -> None:
    """Emit a DB command JSON envelope to stdout (indent=2, sorted keys)."""
    ch.raw_print(json.dumps(payload, indent=2, sort_keys=True))


# ============================================================================
# PUBLIC COMMAND FUNCTIONS
# ============================================================================


def db_containers_cmd(options: dict[str, Any]):
    """
    List Docker containers running database services.

    Usage: navig db containers
    """
    result = _resolve_host_discovery(options)
    if result is None:
        return
    host_name, _config_manager, discovery = result

    spinner_ctx = (
        ch.create_spinner("Scanning for database containers...")
        if not options.get("json")
        else nullcontext()
    )
    with spinner_ctx:
        containers = _list_docker_db_containers(discovery)

    if not containers:
        ch.warning("No database containers found.")
        ch.info("Tip: Make sure Docker is running and you have database containers.")
        return

    # Display results
    if options.get("json"):
        ch.raw_print(json.dumps({"containers": containers, "count": len(containers)}))
    else:
        table = ch.create_table(
            title=f"Database Containers on {host_name}",
            columns=[
                {"name": "Container", "style": "cyan"},
                {"name": "Image", "style": "green"},
                {"name": "Type", "style": "yellow"},
                {"name": "Status", "style": "white"},
            ],
        )

        for c in containers:
            table.add_row(c["name"], c["image"], c["db_type"], c["status"])

        ch.print_table(table)
        ch.dim(f"\nFound {len(containers)} database container(s)")


@command_meta(
    summary="Execute SQL query on remote database",
    status="stable",
    since="2.4.18",
    tags=["database", "sql", "remote"],
    examples=["navig db query \"SELECT 1\"", "navig db query \"SELECT * FROM users\" -d mydb"],
)
def db_query_cmd(
    query: str,
    container: str | None,
    user: str,
    password: str | None,
    database: str | None,
    db_type: str | None,
    options: dict[str, Any],
):
    """
    Execute SQL query on remote database (Docker or native).

    Usage: navig db query "SELECT 1" --container mysql_db --user root
    """
    result = _resolve_host_discovery(options)
    if result is None:
        return
    host_name, config_manager, discovery = result

    # Resolve credentials from app/host config
    # Only use CLI-provided credentials if they differ from defaults
    cli_user_provided = user != "root"  # Check if user explicitly provided
    user, password, db_type_from_config = _get_db_credentials_from_config(
        config_manager,
        host_name,
        user if cli_user_provided else None,  # Only pass if explicitly provided
        password,
        db_type,
    )

    # Use db_type from config if available and not specified
    if not db_type:
        db_type = db_type_from_config

    # Auto-detect database type if still not specified
    if not db_type:
        spinner_ctx = (
            ch.create_spinner("Detecting database type...")
            if not options.get("json")
            else nullcontext()
        )
        with spinner_ctx:
            db_type = _detect_db_type(discovery, container)

        if not db_type:
            ch.error("Could not detect database type.")
            ch.info("Use --type to specify: mysql, mariadb, or postgresql")
            return
        # Keep JSON/plain/quiet output strictly machine-readable.
        if (
            not options.get("json")
            and not options.get("plain")
            and not options.get("quiet")
        ):
            ch.dim(f"Detected: {db_type}")

    # Dry run
    if options.get("dry_run"):
        ch.info(f"[DRY RUN] Would execute on {db_type}: {query}")
        return

    # Execute query
    spinner_ctx = (
        ch.create_spinner("Executing query...")
        if not options.get("json")
        else nullcontext()
    )
    with spinner_ctx:
        success, stdout, stderr = _execute_db_query(
            discovery, query, db_type, user, password, database, container
        )

    # Filter out MySQL deprecation warning from stderr
    if stderr:
        stderr_lines = stderr.strip().split("\n")
        filtered_stderr = "\n".join(
            line
            for line in stderr_lines
            if "Deprecated program name" not in line
            and "use '/usr/bin/mariadb' instead" not in line
        ).strip()
    else:
        filtered_stderr = ""

    if options.get("json"):
        _emit_db_json(
            {
                "schema_version": "1.0.0",
                "command": "db.query",
                "success": bool(success),
                "host": host_name,
                "db_type": db_type,
                "database": database,
                "container": container,
                "query": query,
                "stdout": stdout,
                "stderr": filtered_stderr,
            }
        )
        return

    if success:
        if stdout:
            if options.get("plain"):
                ch.format_db_output_plain(stdout)
            else:
                # Detect query type for smart formatting
                query_upper = query.strip().upper()
                if query_upper.startswith("DESCRIBE") or query_upper.startswith(
                    "DESC "
                ):
                    query_type = "describe"
                elif query_upper.startswith("SHOW"):
                    query_type = "show"
                else:
                    query_type = "select"
                ch.format_db_output(stdout, query_type)
        else:
            ch.success("Query executed successfully (no output)")
    else:
        ch.error("Query failed")
        if filtered_stderr:
            ch.error(filtered_stderr)

            # Detect authentication errors and provide helpful guidance
            if (
                "Access denied" in filtered_stderr
                or "authentication failed" in filtered_stderr.lower()
            ):
                ch.console.print()
                ch.warning("Database Authentication Failed")
                ch.console.print()
                ch.info("Credentials attempted:")
                ch.dim(f"  User: {user}")
                ch.dim(f"  Password: {'(yes)' if password else '(no)'}")
                ch.dim(f"  Database: {database or '(default)'}")
                ch.console.print()
                ch.info("Solutions:")
                ch.dim("  1. Specify credentials explicitly:")
                ch.dim('     navig db query "..." -u YOUR_USER -p YOUR_PASSWORD')
                ch.console.print()
                ch.dim("  2. Configure in app database settings:")
                ch.dim("     navig app edit")
                ch.dim("     # Add database.user and database.password")
                ch.console.print()
                ch.dim("  3. Configure host root credentials:")
                ch.dim("     navig host edit")
                ch.dim("     # Add database.root_user and database.root_password")
                ch.console.print()
                ch.dim("  4. Use app-specific database user (recommended):")
                ch.dim(
                    "     Instead of root, use a dedicated user with limited permissions"
                )


@command_meta(
    summary="List available databases on remote host",
    status="stable",
    since="2.4.18",
    tags=["database", "inventory"],
    examples=["navig db list", "navig db list --json"],
)
def db_list_cmd(
    container: str | None,
    user: str,
    password: str | None,
    db_type: str | None,
    options: dict[str, Any],
):
    """
    List all databases.

    Credentials are resolved in priority order:
    1. Command-line arguments (--user, --password)
    2. Active app's database configuration
    3. Host's database root credentials
    4. Default to 'root' user with no password

    Usage: navig db-databases --container mysql_db
    """
    result = _resolve_host_discovery(options)
    if result is None:
        return
    host_name, config_manager, discovery = result

    # Resolve database credentials from config if not provided
    resolved_user, resolved_password, resolved_db_type = (
        _get_db_credentials_from_config(
            config_manager, host_name, user, password, db_type
        )
    )

    # Show credential source info if verbose
    if options.get("verbose"):
        if resolved_password and not password:
            ch.dim("Using database credentials from configuration")

    # Auto-detect database type
    if not resolved_db_type:
        spinner_ctx = (
            ch.create_spinner("Detecting database type...")
            if not options.get("json")
            else nullcontext()
        )
        with spinner_ctx:
            resolved_db_type = _detect_db_type(discovery, container)

        if not resolved_db_type:
            ch.error("Could not detect database type.")
            return

    # Build query based on database type
    if resolved_db_type in ("mysql", "mariadb"):
        query = "SHOW DATABASES"
    else:
        query = "SELECT datname FROM pg_database WHERE datistemplate = false"

    spinner_ctx = (
        ch.create_spinner("Fetching databases...")
        if not options.get("json")
        else nullcontext()
    )
    with spinner_ctx:
        success, stdout, stderr = _execute_db_query(
            discovery,
            query,
            resolved_db_type,
            resolved_user,
            resolved_password,
            None,
            container,
        )

    if not success:
        ch.error("Failed to list databases")
        if stderr:
            ch.error(stderr)
        return

    # Parse output
    lines = stdout.strip().split("\n")
    databases = [
        line.strip()
        for line in lines
        if line.strip()
        and not line.startswith("+")
        and "Database" not in line
        and "datname" not in line
    ]

    # Clean up database names (remove MySQL table formatting)
    clean_databases = []
    for db in databases:
        db_name = db.replace("|", "").strip()
        if db_name:
            clean_databases.append(db_name)

    if options.get("json"):
        _emit_db_json(
            {
                "schema_version": "1.0.0",
                "command": "db.list",
                "success": True,
                "host": host_name,
                "db_type": resolved_db_type,
                "container": container,
                "databases": clean_databases,
                "count": len(clean_databases),
            }
        )
    elif options.get("plain"):
        # Plain text output - one database per line
        for db_name in clean_databases:
            ch.raw_print(db_name)
    else:
        table = ch.create_table(
            title=f"Databases ({resolved_db_type})",
            columns=[{"name": "Database Name", "style": "cyan"}],
        )
        for db_name in clean_databases:
            table.add_row(db_name)

        ch.print_table(table)
        ch.dim(f"\nTotal: {len(clean_databases)} database(s)")


def db_tables_cmd(
    database: str,
    container: str | None,
    user: str,
    password: str | None,
    db_type: str | None,
    options: dict[str, Any],
):
    """
    List tables in a database.

    Credentials are resolved in priority order:
    1. Command-line arguments (--user, --password)
    2. Active app's database configuration
    3. Host's database root credentials
    4. Default to 'root' user with no password

    Usage: navig db-show-tables mydb --container mysql_db
    """
    result = _resolve_host_discovery(options)
    if result is None:
        return
    host_name, config_manager, discovery = result

    # Resolve database credentials from config if not provided
    resolved_user, resolved_password, resolved_db_type = (
        _get_db_credentials_from_config(
            config_manager, host_name, user, password, db_type
        )
    )

    if not resolved_db_type:
        spinner_ctx = (
            ch.create_spinner("Detecting database type...")
            if not options.get("json")
            else nullcontext()
        )
        with spinner_ctx:
            resolved_db_type = _detect_db_type(discovery, container)
        if not resolved_db_type:
            ch.error("Could not detect database type.")
            return

    # Build query
    if resolved_db_type in ("mysql", "mariadb"):
        query = "SHOW TABLES"
    else:
        query = "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"

    spinner_ctx = (
        ch.create_spinner(f"Fetching tables from {database}...")
        if not options.get("json")
        else nullcontext()
    )
    with spinner_ctx:
        success, stdout, stderr = _execute_db_query(
            discovery,
            query,
            resolved_db_type,
            resolved_user,
            resolved_password,
            database,
            container,
        )

    if not success:
        ch.error("Failed to list tables")
        if stderr:
            ch.error(stderr)
        return

    # Parse output
    lines = stdout.strip().split("\n")
    tables = [
        line.strip().replace("|", "").strip()
        for line in lines
        if line.strip()
        and not line.startswith("+")
        and "Tables_in" not in line
        and "tablename" not in line
    ]
    tables = [t for t in tables if t]

    if options.get("json"):
        _emit_db_json(
            {
                "schema_version": "1.0.0",
                "command": "db.tables",
                "success": True,
                "host": host_name,
                "db_type": resolved_db_type,
                "container": container,
                "database": database,
                "tables": tables,
                "count": len(tables),
            }
        )
    elif options.get("plain"):
        # Plain text output - one table per line
        for t in tables:
            ch.raw_print(t)
    else:
        table = ch.create_table(
            title=f"Tables in {database}",
            columns=[{"name": "Table Name", "style": "cyan"}],
        )
        for t in tables:
            table.add_row(t)

        ch.print_table(table)
        ch.dim(f"\nTotal: {len(tables)} table(s)")


def db_dump_cmd(
    database: str,
    output: Path | None,
    container: str | None,
    user: str,
    password: str | None,
    db_type: str | None,
    options: dict[str, Any],
):
    """
    Dump/backup a database.

    Credentials are resolved in priority order:
    1. Command-line arguments (--user, --password)
    2. Active app's database configuration
    3. Host's database root credentials
    4. Default to 'root' user with no password

    Usage: navig db-dump mydb --container mysql_db --output backup.sql
    """
    from datetime import datetime

    from navig.config import get_config_manager
    from navig.discovery import ServerDiscovery

    config_manager = get_config_manager()
    host_name = options.get("host") or config_manager.get_active_host()

    if not host_name:
        ch.error("No active host.", "Use 'navig host use <name>' to set one.")
        return

    host_config = config_manager.load_host_config(host_name)
    if not host_config:
        ch.error(f"Host not found: {host_name}")
        return

    # Resolve database credentials from config if not provided
    resolved_user, resolved_password, resolved_db_type = (
        _get_db_credentials_from_config(
            config_manager, host_name, user, password, db_type
        )
    )

    debug_logger = options.get("debug_logger")

    ssh_config = {
        "host": host_config.get("host", host_config.get("hostname")),
        "user": host_config.get("user", "root"),
        "port": host_config.get("port", 22),
        "ssh_key": host_config.get("ssh_key"),
        "ssh_password": host_config.get("ssh_password"),
    }
    discovery = ServerDiscovery(ssh_config, debug_logger=debug_logger)

    if not resolved_db_type:
        with ch.create_spinner("Detecting database type..."):
            resolved_db_type = _detect_db_type(discovery, container)
        if not resolved_db_type:
            ch.error("Could not detect database type.")
            return

    # Build dump command with proper shell escaping
    escaped_user = _escape_for_shell(resolved_user)
    escaped_password = (
        _escape_for_shell(resolved_password) if resolved_password else None
    )
    escaped_database = _escape_for_shell(database)

    if resolved_db_type in ("mysql", "mariadb"):
        if resolved_password:
            dump_cmd = (
                f"mysqldump -u{escaped_user} -p{escaped_password} {escaped_database}"
            )
        else:
            dump_cmd = f"mysqldump -u{escaped_user} {escaped_database}"
    else:
        dump_cmd = f"pg_dump -U {escaped_user} {escaped_database}"
        if resolved_password:
            dump_cmd = f"PGPASSWORD={escaped_password} {dump_cmd}"

    if container:
        dump_cmd = f"docker exec {_escape_for_shell(container)} sh -c {_escape_for_shell(dump_cmd)}"

    # Determine output path
    if not output:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output = config_manager.backups_dir / f"{database}_{timestamp}.sql"

    if options.get("dry_run"):
        ch.info(f"[DRY RUN] Would dump {database} to {output}")
        return

    ch.info(f"Dumping database: {database}")

    # Execute dump and save locally
    with ch.create_spinner("Creating backup..."):
        success, stdout, stderr = discovery._execute_ssh(dump_cmd)

    if success and stdout:
        # Save to local file
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(stdout)
        size_kb = output.stat().st_size / 1024
        ch.success(f"✓ Backup saved: {output}")
        ch.dim(f"  Size: {size_kb:.1f} KB")
    else:
        ch.error("Backup failed")
        if stderr:
            ch.error(stderr)


def db_shell_cmd(
    container: str | None,
    user: str,
    password: str | None,
    database: str | None,
    db_type: str | None,
    options: dict[str, Any],
):
    """
    Open interactive database shell.

    Credentials are resolved in priority order:
    1. Command-line arguments (--user, --password)
    2. Active app's database configuration
    3. Host's database root credentials
    4. Default to 'root' user with no password

    Usage: navig db-shell --container mysql_db
    """
    import subprocess

    from navig.config import get_config_manager

    config_manager = get_config_manager()
    host_name = options.get("host") or config_manager.get_active_host()

    if not host_name:
        ch.error("No active host.", "Use 'navig host use <name>' to set one.")
        return

    host_config = config_manager.load_host_config(host_name)
    if not host_config:
        ch.error(f"Host not found: {host_name}")
        return

    # Resolve database credentials from config if not provided
    resolved_user, resolved_password, resolved_db_type = (
        _get_db_credentials_from_config(
            config_manager, host_name, user, password, db_type
        )
    )

    # Default to mysql if still not detected
    if not resolved_db_type:
        resolved_db_type = "mysql"

    ssh_host = host_config.get("host", host_config.get("hostname"))
    ssh_user = host_config.get("user", "root")
    ssh_port = host_config.get("port", 22)
    ssh_key = host_config.get("ssh_key")

    # Build the database client command with proper shell escaping
    escaped_user = _escape_for_shell(resolved_user)
    escaped_password = (
        _escape_for_shell(resolved_password) if resolved_password else None
    )
    escaped_database = _escape_for_shell(database) if database else None

    if resolved_db_type in ("mysql", "mariadb"):
        if resolved_password:
            db_cmd = f"mysql -u{escaped_user} -p{escaped_password}"
        else:
            db_cmd = f"mysql -u{escaped_user}"
        if database:
            db_cmd += f" {escaped_database}"
    else:
        db_cmd = f"psql -U {escaped_user}"
        if database:
            db_cmd += f" -d {escaped_database}"
        if resolved_password:
            db_cmd = f"PGPASSWORD={escaped_password} {db_cmd}"

    if container:
        db_cmd = f"docker exec -it {_escape_for_shell(container)} sh -c {_escape_for_shell(db_cmd)}"

    # Build SSH command for interactive session — resolve full path for 32-bit Python on Windows
    import pathlib as _pl
    import shutil as _shutil

    def _find_ssh_db():
        b = _shutil.which("ssh") or _shutil.which("ssh.exe")
        if b:
            return b
        _sr = __import__("os").environ.get("SystemRoot", "C:/Windows")
        for _c in [
            _pl.Path(_sr) / "SysNative" / "OpenSSH" / "ssh.exe",
            _pl.Path(_sr) / "System32" / "OpenSSH" / "ssh.exe",
        ]:
            if _c.exists():
                return str(_c)
        raise FileNotFoundError("ssh.exe not found")

    ssh_cmd = [_find_ssh_db(), "-t", "-p", str(ssh_port)]
    if ssh_key:
        ssh_cmd.extend(["-i", str(Path(ssh_key).expanduser())])
    ssh_cmd.append(f"{ssh_user}@{ssh_host}")
    ssh_cmd.append(db_cmd)

    ch.info(f"Connecting to {resolved_db_type} on {host_name}...")
    if container:
        ch.dim(f"Container: {container}")

    # Run interactively
    subprocess.run(ssh_cmd)


from pathlib import Path
from typing import Any

import typer

from navig.cli import show_subcommand_help
from navig.deprecation import deprecation_warning

db_app = typer.Typer(
    help="Database operations (query, backup, restore, list, shell)",
    invoke_without_command=True,
    no_args_is_help=False,
)


@db_app.callback()
def db_callback(ctx: typer.Context):
    """Database management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        import os as _os  # noqa: PLC0415

        if _os.environ.get("NAVIG_LAUNCHER", "fuzzy") == "legacy":
            show_subcommand_help("db", ctx)
            raise typer.Exit()
        from navig.cli.launcher import smart_launch  # noqa: PLC0415

        smart_launch("db", db_app)


@db_app.command("show")
def db_show(
    ctx: typer.Context,
    database: str | None = typer.Argument(None, help="Database name"),
    container: str | None = typer.Option(
        None, "--container", "-c", help="Docker container name"
    ),
    user: str = typer.Option("root", "--user", "-u", help="Database user"),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Database password"
    ),
    db_type: str | None = typer.Option(
        None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"
    ),
    tables: bool = typer.Option(False, "--tables", help="Show tables in database"),
    containers: bool = typer.Option(
        False, "--containers", help="Show database containers"
    ),
    users: bool = typer.Option(False, "--users", help="Show database users"),
    plain: bool = typer.Option(False, "--plain", help="Plain output for scripting"),
):
    """Show database information (canonical command)."""
    ctx.obj["plain"] = plain
    if containers:
        from navig.commands.db import db_containers_cmd

        db_containers_cmd(ctx.obj)
    elif users:
        from navig.commands.database_advanced import list_users_cmd

        list_users_cmd(ctx.obj)
    elif tables and database or database:
        from navig.commands.db import db_tables_cmd

        db_tables_cmd(database, container, user, password, db_type, ctx.obj)
    else:
        from navig.commands.db import db_list_cmd

        db_list_cmd(container, user, password, db_type, ctx.obj)


@db_app.command("run")
def db_run(
    ctx: typer.Context,
    query: str | None = typer.Argument(None, help="SQL query to execute"),
    container: str | None = typer.Option(
        None, "--container", "-c", help="Docker container name"
    ),
    user: str = typer.Option("root", "--user", "-u", help="Database user"),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Database password"
    ),
    database: str | None = typer.Option(None, "--database", "-d", help="Database name"),
    db_type: str | None = typer.Option(
        None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"
    ),
    file: Path | None = typer.Option(None, "--file", "-f", help="SQL file to execute"),
    shell: bool = typer.Option(False, "--shell", "-s", help="Open interactive shell"),
):
    """Run SQL query/file or open shell (canonical command)."""
    if shell:
        from navig.commands.db import db_shell_cmd

        db_shell_cmd(container, user, password, database, db_type, ctx.obj)
    elif file:
        from navig.commands.database import execute_sql_file

        execute_sql_file(file, ctx.obj)
    elif query:
        from navig.commands.db import db_query_cmd

        db_query_cmd(query, container, user, password, database, db_type, ctx.obj)
    else:
        # Default to shell if no query provided
        from navig.commands.db import db_shell_cmd

        db_shell_cmd(container, user, password, database, db_type, ctx.obj)


def _is_base64_encoded(s: str) -> bool:
    """Check if string looks like base64 (for auto-detection)."""
    import base64
    import re

    # Base64 pattern: only A-Za-z0-9+/= and length multiple of 4
    if not re.match(r"^[A-Za-z0-9+/]+=*$", s):
        return False
    if len(s) % 4 != 0:
        return False
    # Must be reasonably long (short strings could be false positives)
    if len(s) < 20:
        return False
    # Try to decode - valid base64 should decode cleanly
    try:
        decoded = base64.b64decode(s).decode("utf-8")
        # Check if decoded looks like SQL (common keywords)
        sql_keywords = [
            "SELECT",
            "INSERT",
            "UPDATE",
            "DELETE",
            "CREATE",
            "DROP",
            "ALTER",
            "SHOW",
            "DESCRIBE",
        ]
        return any(kw in decoded.upper() for kw in sql_keywords)
    except Exception:
        return False


@db_app.command("query")
@command_meta(
    summary="Run an SQL query through NAVIG DB surface",
    status="stable",
    since="2.4.18",
    tags=["database", "sql"],
    examples=["navig db query \"SELECT 1\"", "navig db query \"SHOW DATABASES\" --json"],
)
def db_query_new(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="SQL query to execute (auto-detects base64)"),
    container: str | None = typer.Option(
        None, "--container", "-c", help="Docker container name"
    ),
    user: str = typer.Option("root", "--user", "-u", help="Database user"),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Database password"
    ),
    database: str | None = typer.Option(None, "--database", "-d", help="Database name"),
    db_type: str | None = typer.Option(
        None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"
    ),
    plain: bool = typer.Option(
        False,
        "--plain",
        "--raw",
        help="Output plain text (no formatting) for scripting",
    ),
    json: bool = typer.Option(False, "--json", help="Output JSON"),
    b64: bool = typer.Option(
        False, "--b64", "-b", help="Force base64 decode (usually auto-detected)"
    ),
):
    """Execute SQL query on remote database.

    Base64 encoding is AUTO-DETECTED. Just pass the query:
        navig db query "SELECT * FROM users" -d mydb
        navig db query "U0VMRUNUICogRlJPTSB1c2Vycw==" -d mydb  # Auto-detected as base64

    Use --b64 to force base64 decoding if auto-detection fails.
    """
    import base64

    from navig.commands.db import db_query_cmd

    # Auto-detect base64 or use explicit flag
    if b64 or _is_base64_encoded(query):
        try:
            decoded = base64.b64decode(query).decode("utf-8").strip()
            if not b64:
                ch.info(
                    f"Auto-detected base64 query ({len(query)} chars → {len(decoded)} chars)"
                )
            query = decoded
        except Exception as e:
            if b64:
                ch.error(f"Failed to decode base64 query: {e}")
                raise typer.Exit(1) from None
            # If auto-detect failed, just use original query
            pass

    ctx.obj["plain"] = plain
    if json:
        ctx.obj["json"] = True
    db_query_cmd(query, container, user, password, database, db_type, ctx.obj)


@db_app.command("file")
def db_file_new(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="SQL file to execute"),
):
    """Execute SQL file through tunnel."""
    from navig.commands.database import execute_sql_file

    execute_sql_file(file, ctx.obj)


@db_app.command("list")
@command_meta(
    summary="List remote databases",
    status="stable",
    since="2.4.18",
    tags=["database", "inventory"],
    examples=["navig db list", "navig db list --plain"],
)
def db_list_new(
    ctx: typer.Context,
    container: str | None = typer.Option(
        None, "--container", "-c", help="Docker container name"
    ),
    user: str = typer.Option("root", "--user", "-u", help="Database user"),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Database password"
    ),
    db_type: str | None = typer.Option(
        None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"
    ),
    plain: bool = typer.Option(
        False, "--plain", help="Output plain text (one database per line) for scripting"
    ),
    json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """List all databases on remote server."""
    from navig.commands.db import db_list_cmd

    ctx.obj["plain"] = plain
    if json:
        ctx.obj["json"] = True
    db_list_cmd(container, user, password, db_type, ctx.obj)


@db_app.command("tables")
def db_tables_new(
    ctx: typer.Context,
    database: str = typer.Argument(..., help="Database name"),
    container: str | None = typer.Option(
        None, "--container", "-c", help="Docker container name"
    ),
    user: str = typer.Option("root", "--user", "-u", help="Database user"),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Database password"
    ),
    db_type: str | None = typer.Option(
        None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"
    ),
    plain: bool = typer.Option(
        False, "--plain", help="Output plain text (one table per line) for scripting"
    ),
):
    """List tables in a database."""
    from navig.commands.db import db_tables_cmd

    ctx.obj["plain"] = plain
    db_tables_cmd(database, container, user, password, db_type, ctx.obj)


@db_app.command("dump")
def db_dump_new(
    ctx: typer.Context,
    database: str = typer.Argument(..., help="Database name to dump"),
    output: Path | None = typer.Option(None, "--output", "-o", help="Output file path"),
    container: str | None = typer.Option(
        None, "--container", "-c", help="Docker container name"
    ),
    user: str = typer.Option("root", "--user", "-u", help="Database user"),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Database password"
    ),
    db_type: str | None = typer.Option(
        None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"
    ),
):
    """Dump/backup a database from remote server."""
    from navig.commands.db import db_dump_cmd

    db_dump_cmd(database, output, container, user, password, db_type, ctx.obj)


@db_app.command("restore")
def db_restore_new(
    ctx: typer.Context,
    file: Path = typer.Argument(..., help="Backup file to restore from"),
):
    """Restore database from backup file."""
    from navig.commands.database import restore_database

    restore_database(file, ctx.obj)


@db_app.command("shell", hidden=True)
def db_shell_new(
    ctx: typer.Context,
    container: str | None = typer.Option(
        None, "--container", "-c", help="Docker container name"
    ),
    user: str = typer.Option("root", "--user", "-u", help="Database user"),
    password: str | None = typer.Option(
        None, "--password", "-p", help="Database password"
    ),
    database: str | None = typer.Option(None, "--database", "-d", help="Database name"),
    db_type: str | None = typer.Option(
        None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"
    ),
):
    """[DEPRECATED: Use 'navig db run --shell'] Open interactive database shell."""
    deprecation_warning("navig db shell", "navig db run --shell")
    from navig.commands.db import db_shell_cmd

    db_shell_cmd(container, user, password, database, db_type, ctx.obj)


@db_app.command("containers", hidden=True)
def db_containers_new(ctx: typer.Context):
    """[DEPRECATED: Use 'navig db show --containers'] List database containers."""
    deprecation_warning("navig db containers", "navig db show --containers")
    from navig.commands.db import db_containers_cmd

    db_containers_cmd(ctx.obj)


@db_app.command("users", hidden=True)
def db_users_new(ctx: typer.Context):
    """[DEPRECATED: Use 'navig db show --users'] List database users."""
    deprecation_warning("navig db users", "navig db show --users")
    from navig.commands.database_advanced import list_users_cmd

    list_users_cmd(ctx.obj)


@db_app.command("optimize")
def db_optimize_new(
    ctx: typer.Context,
    table: str = typer.Argument(..., help="Table name to optimize"),
):
    """Optimize database table."""
    from navig.commands.database_advanced import optimize_table_cmd

    optimize_table_cmd(table, ctx.obj)


@db_app.command("repair")
def db_repair_new(
    ctx: typer.Context,
    table: str = typer.Argument(..., help="Table name to repair"),
):
    """Repair database table."""
    from navig.commands.database_advanced import repair_table_cmd

    repair_table_cmd(table, ctx.obj)
