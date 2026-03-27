"""Legacy flat CLI command registrations.

Extracted from `navig.cli.__init__` to preserve deprecated command surfaces
while reducing the size of the root CLI module.
"""

from __future__ import annotations

from pathlib import Path

import typer

from navig.deprecation import deprecation_warning


def register_legacy_flat_commands(app: typer.Typer) -> None:
    """Register deprecated flat file, database, and monitoring commands."""

    @app.command("upload", hidden=True)
    def upload_file(
        ctx: typer.Context,
        local: Path = typer.Argument(..., help="Local file/directory path"),
        remote: str | None = typer.Argument(
            None,
            help="Remote path (smart detection if omitted)",
        ),
    ):
        """[DEPRECATED: Use 'navig file add'] Upload file/directory."""
        deprecation_warning("navig upload", "navig file add")
        from navig.commands.files import upload_file_cmd

        upload_file_cmd(local, remote, ctx.obj)

    @app.command("download", hidden=True)
    def download_file(
        ctx: typer.Context,
        remote: str = typer.Argument(..., help="Remote file/directory path"),
        local: Path | None = typer.Argument(
            None,
            help="Local path (smart detection if omitted)",
        ),
    ):
        """[DEPRECATED: Use 'navig file show --download'] Download file/directory."""
        deprecation_warning("navig download", "navig file show --download")
        from navig.commands.files import download_file_cmd

        download_file_cmd(remote, local, ctx.obj)

    @app.command("list", hidden=True)
    def list_remote(
        ctx: typer.Context,
        remote_path: str = typer.Argument(..., help="Remote directory path"),
    ):
        """[DEPRECATED: Use 'navig file list'] List remote directory."""
        deprecation_warning("navig list", "navig file list")
        from navig.commands.files import list_remote_directory

        list_remote_directory(remote_path, ctx.obj)

    @app.command("delete", hidden=True)
    def delete_file(
        ctx: typer.Context,
        remote: str = typer.Argument(..., help="Remote file/directory path to delete"),
        recursive: bool = typer.Option(
            False, "--recursive", "-r", help="Delete directories recursively"
        ),
        force: bool = typer.Option(
            False, "--force", "-f", help="Force deletion without confirmation"
        ),
    ):
        """[DEPRECATED: Use 'navig file remove'] Delete remote file/directory."""
        deprecation_warning("navig delete", "navig file remove")
        from navig.commands.files_advanced import delete_file_cmd

        ctx.obj["recursive"] = recursive
        ctx.obj["force"] = force
        delete_file_cmd(remote, ctx.obj)

    @app.command("mkdir", hidden=True)
    def make_directory(
        ctx: typer.Context,
        remote: str = typer.Argument(..., help="Remote directory path to create"),
        parents: bool = typer.Option(
            True, "--parents", "-p", help="Create parent directories as needed"
        ),
        mode: str = typer.Option("755", "--mode", "-m", help="Permission mode (e.g., 755)"),
    ):
        """[DEPRECATED: Use 'navig file add --dir'] Create remote directory."""
        deprecation_warning("navig mkdir", "navig file add --dir")
        from navig.commands.files_advanced import mkdir_cmd

        ctx.obj["parents"] = parents
        ctx.obj["mode"] = mode
        mkdir_cmd(remote, ctx.obj)

    @app.command("chmod", hidden=True)
    def change_permissions(
        ctx: typer.Context,
        remote: str = typer.Argument(..., help="Remote file/directory path"),
        mode: str = typer.Argument(..., help="Permission mode (e.g., 755, 644)"),
        recursive: bool = typer.Option(False, "--recursive", "-r", help="Apply recursively"),
    ):
        """[DEPRECATED: Use 'navig file edit --mode'] Change permissions."""
        deprecation_warning("navig chmod", "navig file edit --mode")
        from navig.commands.files_advanced import chmod_cmd

        ctx.obj["recursive"] = recursive
        chmod_cmd(remote, mode, ctx.obj)

    @app.command("chown", hidden=True)
    def change_owner(
        ctx: typer.Context,
        remote: str = typer.Argument(..., help="Remote file/directory path"),
        owner: str = typer.Argument(..., help="New owner (user or user:group)"),
        recursive: bool = typer.Option(False, "--recursive", "-r", help="Apply recursively"),
    ):
        """[DEPRECATED: Use 'navig file edit --owner'] Change ownership."""
        deprecation_warning("navig chown", "navig file edit --owner")
        from navig.commands.files_advanced import chown_cmd

        ctx.obj["recursive"] = recursive
        chown_cmd(remote, owner, ctx.obj)

    @app.command("cat", hidden=True)
    def cat_file(
        ctx: typer.Context,
        remote: str = typer.Argument(..., help="Remote file path to read"),
        lines: int | None = typer.Option(None, "--lines", "-n", help="Number of lines to show"),
        head: bool = typer.Option(False, "--head", help="Show first N lines (use with --lines)"),
        tail: bool = typer.Option(
            False, "--tail", "-t", help="Show last N lines (use with --lines)"
        ),
    ):
        """[DEPRECATED: Use 'navig file show'] Read remote file contents."""
        deprecation_warning("navig cat", "navig file show")
        from navig.commands.files_advanced import cat_file_cmd

        cat_file_cmd(remote, ctx.obj, lines=lines, head=head, tail=tail)

    @app.command("write-file", hidden=True)
    def write_file(
        ctx: typer.Context,
        remote: str = typer.Argument(..., help="Remote file path to write"),
        content: str | None = typer.Option(None, "--content", "-c", help="Content to write"),
        stdin: bool = typer.Option(False, "--stdin", "-s", help="Read content from stdin (pipe)"),
        from_file: Path | None = typer.Option(
            None, "--from-file", "-f", help="Read content from local file"
        ),
        append: bool = typer.Option(
            False, "--append", "-a", help="Append to file instead of overwrite"
        ),
        mode: str | None = typer.Option(
            None, "--mode", "-m", help="Set file permissions after writing"
        ),
        owner: str | None = typer.Option(
            None, "--owner", "-o", help="Set file owner after writing"
        ),
    ):
        """[DEPRECATED: Use 'navig file edit --content'] Write to remote file."""
        deprecation_warning("navig write-file", "navig file edit --content")
        from navig.commands.files_advanced import write_file_cmd

        write_file_cmd(
            remote,
            content,
            ctx.obj,
            stdin=stdin,
            local_file=from_file,
            append=append,
            mode=mode,
            owner=owner,
        )

    @app.command("ls", hidden=True)
    def ls_directory(
        ctx: typer.Context,
        remote: str = typer.Argument(..., help="Remote directory path"),
        all: bool = typer.Option(False, "--all", "-a", help="Show hidden files"),
        long: bool = typer.Option(True, "--long", "-l", help="Long format with details"),
        human: bool = typer.Option(True, "--human", "-h", help="Human-readable sizes"),
    ):
        """[DEPRECATED: Use 'navig file list'] List remote directory."""
        deprecation_warning("navig ls", "navig file list")
        from navig.commands.files_advanced import list_dir_cmd

        list_dir_cmd(remote, ctx.obj, all=all, long=long, human=human)

    @app.command("tree", hidden=True)
    def tree_directory(
        ctx: typer.Context,
        remote: str = typer.Argument(..., help="Remote directory path"),
        depth: int = typer.Option(2, "--depth", "-d", help="Maximum depth to display"),
        dirs_only: bool = typer.Option(False, "--dirs-only", "-D", help="Show only directories"),
    ):
        """[DEPRECATED: Use 'navig file list --tree'] Show directory tree."""
        deprecation_warning("navig tree", "navig file list --tree")
        from navig.commands.files_advanced import tree_cmd

        tree_cmd(remote, ctx.obj, depth=depth, dirs_only=dirs_only)

    @app.command("db-list", hidden=True)
    def list_databases(ctx: typer.Context):
        """[DEPRECATED] List all databases with sizes. Use: navig db list"""
        deprecation_warning("navig db-list", "navig db list")
        from navig.commands.database_advanced import list_databases_cmd

        list_databases_cmd(ctx.obj)

    @app.command("db-tables", hidden=True)
    def list_tables(
        ctx: typer.Context,
        database: str = typer.Argument(..., help="Database name"),
    ):
        """[DEPRECATED] List tables in a database. Use: navig db tables <database>"""
        deprecation_warning("navig db-tables", "navig db tables")
        from navig.commands.database_advanced import list_tables_cmd

        list_tables_cmd(database, ctx.obj)

    @app.command("db-optimize", hidden=True)
    def optimize_table(
        ctx: typer.Context,
        table: str = typer.Argument(..., help="Table name to optimize"),
    ):
        """[DEPRECATED] Optimize database table. Use: navig db optimize <table>"""
        deprecation_warning("navig db-optimize", "navig db optimize")
        from navig.commands.database_advanced import optimize_table_cmd

        optimize_table_cmd(table, ctx.obj)

    @app.command("db-repair", hidden=True)
    def repair_table(
        ctx: typer.Context,
        table: str = typer.Argument(..., help="Table name to repair"),
    ):
        """[DEPRECATED] Repair database table. Use: navig db repair <table>"""
        deprecation_warning("navig db-repair", "navig db repair")
        from navig.commands.database_advanced import repair_table_cmd

        repair_table_cmd(table, ctx.obj)

    @app.command("db-users", hidden=True)
    def list_db_users(ctx: typer.Context):
        """[DEPRECATED] List database users. Use: navig db users"""
        deprecation_warning("navig db-users", "navig db users")
        from navig.commands.database_advanced import list_users_cmd

        list_users_cmd(ctx.obj)

    @app.command("db-containers", hidden=True)
    def db_containers(ctx: typer.Context):
        """[DEPRECATED] List Docker containers running database services. Use: navig db containers"""
        deprecation_warning("navig db-containers", "navig db containers")
        from navig.commands.db import db_containers_cmd

        db_containers_cmd(ctx.obj)

    @app.command("db-query", hidden=True)
    def db_query(
        ctx: typer.Context,
        query: str = typer.Argument(..., help="SQL query to execute"),
        container: str | None = typer.Option(
            None, "--container", "-c", help="Docker container name"
        ),
        user: str = typer.Option("root", "--user", "-u", help="Database user"),
        password: str | None = typer.Option(None, "--password", "-p", help="Database password"),
        database: str | None = typer.Option(None, "--database", "-d", help="Database name"),
        db_type: str | None = typer.Option(
            None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"
        ),
    ):
        """[DEPRECATED] Execute SQL query on remote database. Use: navig db run <query>"""
        deprecation_warning("navig db-query", "navig db run")
        from navig.commands.db import db_query_cmd

        db_query_cmd(query, container, user, password, database, db_type, ctx.obj)

    @app.command("db-databases", hidden=True)
    def db_databases(
        ctx: typer.Context,
        container: str | None = typer.Option(
            None, "--container", "-c", help="Docker container name"
        ),
        user: str = typer.Option("root", "--user", "-u", help="Database user"),
        password: str | None = typer.Option(None, "--password", "-p", help="Database password"),
        db_type: str | None = typer.Option(
            None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"
        ),
        plain: bool = typer.Option(
            False,
            "--plain",
            help="Output plain text (one database per line) for scripting",
        ),
    ):
        """[DEPRECATED] List all databases on remote server. Use: navig db list"""
        deprecation_warning("navig db-databases", "navig db list")
        from navig.commands.db import db_list_cmd

        ctx.obj["plain"] = plain
        db_list_cmd(container, user, password, db_type, ctx.obj)

    @app.command("db-show-tables", hidden=True)
    def db_show_tables(
        ctx: typer.Context,
        database: str = typer.Argument(..., help="Database name"),
        container: str | None = typer.Option(
            None, "--container", "-c", help="Docker container name"
        ),
        user: str = typer.Option("root", "--user", "-u", help="Database user"),
        password: str | None = typer.Option(None, "--password", "-p", help="Database password"),
        db_type: str | None = typer.Option(
            None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"
        ),
        plain: bool = typer.Option(
            False,
            "--plain",
            help="Output plain text (one table per line) for scripting",
        ),
    ):
        """[DEPRECATED] List tables in a database. Use: navig db tables <database>"""
        deprecation_warning("navig db-show-tables", "navig db tables")
        from navig.commands.db import db_tables_cmd

        ctx.obj["plain"] = plain
        db_tables_cmd(database, container, user, password, db_type, ctx.obj)

    @app.command("db-dump", hidden=True)
    def db_dump(
        ctx: typer.Context,
        database: str = typer.Argument(..., help="Database name to dump"),
        output: Path | None = typer.Option(None, "--output", "-o", help="Output file path"),
        container: str | None = typer.Option(
            None, "--container", "-c", help="Docker container name"
        ),
        user: str = typer.Option("root", "--user", "-u", help="Database user"),
        password: str | None = typer.Option(None, "--password", "-p", help="Database password"),
        db_type: str | None = typer.Option(
            None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"
        ),
    ):
        """[DEPRECATED] Dump/backup a database from remote server. Use: navig db dump"""
        deprecation_warning("navig db-dump", "navig db dump")
        from navig.commands.db import db_dump_cmd

        db_dump_cmd(database, output, container, user, password, db_type, ctx.obj)

    @app.command("db-shell", hidden=True)
    def db_shell(
        ctx: typer.Context,
        container: str | None = typer.Option(
            None, "--container", "-c", help="Docker container name"
        ),
        user: str = typer.Option("root", "--user", "-u", help="Database user"),
        password: str | None = typer.Option(None, "--password", "-p", help="Database password"),
        database: str | None = typer.Option(None, "--database", "-d", help="Database name"),
        db_type: str | None = typer.Option(
            None, "--type", "-t", help="Database type: mysql, mariadb, postgresql"
        ),
    ):
        """[DEPRECATED] Open interactive database shell via SSH. Use: navig db run --shell"""
        deprecation_warning("navig db-shell", "navig db run --shell")
        from navig.commands.db import db_shell_cmd

        db_shell_cmd(container, user, password, database, db_type, ctx.obj)

    @app.command("logs", hidden=True)
    def view_logs(
        ctx: typer.Context,
        service: str = typer.Argument(..., help="Service name (nginx, php-fpm, mysql, app, etc.)"),
        tail: bool = typer.Option(False, "--tail", "-f", help="Follow logs in real-time"),
        lines: int = typer.Option(50, "--lines", "-n", help="Number of lines to display"),
    ):
        """[DEPRECATED] View logs. Use: navig log show <service>"""
        deprecation_warning("navig logs", "navig log show")
        from navig.commands.monitoring import view_service_logs

        view_service_logs(service, tail, lines, ctx.obj)

    @app.command("health", hidden=True)
    def health_check(ctx: typer.Context):
        """[DEPRECATED] Run health checks. Use: navig monitor show"""
        deprecation_warning("navig health", "navig monitor show")
        from navig.commands.monitoring import run_health_check

        run_health_check(ctx.obj)

    @app.command("restart", hidden=True)
    def restart_service(
        ctx: typer.Context,
        service: str = typer.Argument(
            ..., help="Service to restart (nginx|php-fpm|mysql|app|docker|all)"
        ),
    ):
        """[DEPRECATED] Restart service. Use: navig system run --restart <service>"""
        deprecation_warning("navig restart", "navig system run --restart")
        from navig.commands.monitoring import restart_remote_service

        restart_remote_service(service, ctx.obj)
