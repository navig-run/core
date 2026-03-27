"""File Operation Commands"""

import json
from pathlib import Path
from typing import Any

from navig import console_helper as ch


def upload_file_cmd(local: Path, remote: str | None, options: dict[str, Any]):
    """Upload file/directory."""
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    server_name = options.get("app") or config_manager.get_active_server()
    if not server_name:
        ch.error("No active server.")
        return

    if not local.exists():
        ch.error(f"Local file not found: {local}")
        return

    json_enabled = options.get("json", False)

    # Smart path detection
    if remote is None:
        server_config = config_manager.load_server_config(server_name)
        web_root = server_config.get("paths", {}).get("web_root", "/tmp")
        remote = f"{web_root}/{local.name}"
        if not json_enabled:
            ch.dim(f"Auto-detected remote path: {remote}")

    # Check if upload requires confirmation (uploads are state-changing)
    if not ch.confirm_operation(
        operation_name=f"Upload: {local.name} → {remote}",
        operation_type="standard",
        host=server_name,
        details=f"Size: {local.stat().st_size:,} bytes",
        auto_confirm=options.get("yes", False),
        force_confirm=options.get("confirm", False),
    ):
        ch.warning("Cancelled.")
        return

    if not json_enabled:
        ch.info(f"Uploading: {local} -> {remote}")

    server_config = config_manager.load_server_config(server_name)

    # Show progress for uploads
    if json_enabled:
        success = remote_ops.upload_file(local, remote, server_config)
    else:
        with ch.create_spinner("Transferring file..."):
            success = remote_ops.upload_file(local, remote, server_config)

    if success:
        if json_enabled:
            ch.raw_print(
                json.dumps(
                    {
                        "success": True,
                        "local": str(local),
                        "remote": remote,
                        "size_bytes": local.stat().st_size,
                    }
                )
            )
        else:
            ch.success(
                "Upload complete. No traces left."
            )  # void: except in logs. always in logs.
    else:
        if json_enabled:
            ch.raw_print(json.dumps({"success": False, "error": "Upload failed"}))
        else:
            ch.error("Upload failed.")
        ch.info("")
        ch.info("Common causes:")
        ch.info("  1. Permission denied: Check remote directory ownership")
        ch.info('     Fix: navig run "chown -R $(whoami) /remote/path"')
        ch.info("  2. Directory not found: Create it first")
        ch.info("     Fix: navig mkdir /remote/path --parents")
        ch.info("  3. Disk full: Check space with 'df -h'")
        ch.info("  4. SSH connection: Test with 'navig run \"echo test\"'")


def download_file_cmd(remote: str, local: Path | None, options: dict[str, Any]):
    """Download file/directory."""
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    server_name = options.get("app") or config_manager.get_active_server()
    if not server_name:
        ch.error("No active server.")
        return

    if local is None:
        local = Path.cwd() / Path(remote).name
        ch.dim(f"Auto-detected local path: {local}")

    ch.info(f"Downloading: {remote} -> {local}")

    server_config = config_manager.load_server_config(server_name)

    # Show progress for downloads
    with ch.create_spinner("Transferring file..."):
        success = remote_ops.download_file(remote, local, server_config)

    if success:
        ch.success(f"✓ Download complete: {local}")
    else:
        ch.error("Download failed.")
        ch.info("")
        ch.info("Common causes:")
        ch.info("  1. File not found: Check path with 'navig list /path'")
        ch.info("  2. Permission denied: Check file permissions")
        ch.info('     Fix: navig run "chmod 644 /remote/file"')
        ch.info(
            "  3. Local disk full: Check space with 'df -h' (Unix) or 'dir' (Windows)"
        )
        ch.info("  4. Network timeout: Check connection with 'navig tunnel status'")


def list_remote_directory(remote_path: str, options: dict[str, Any]):
    """List remote directory contents."""
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    server_name = options.get("app") or config_manager.get_active_server()
    if not server_name:
        ch.error("No active server.")
        return

    server_config = config_manager.load_server_config(server_name)
    result = remote_ops.execute_command(f"ls -lah {remote_path}", server_config)

    if result.returncode == 0:
        ch.raw_print(result.stdout)
    else:
        ch.error(f"Error: {result.stderr}")


from pathlib import Path
from typing import Any

import typer

from navig.cli import show_subcommand_help

file_app = typer.Typer(
    help="File operations (upload, download, list, edit, remove)",
    invoke_without_command=True,
    no_args_is_help=False,
)


@file_app.callback()
def file_callback(ctx: typer.Context):
    """File operations - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("file", ctx)
        raise typer.Exit()


@file_app.command("add")
def file_add(
    ctx: typer.Context,
    local: Path = typer.Argument(..., help="Local file/directory path"),
    remote: str | None = typer.Argument(
        None, help="Remote path (auto-detected if omitted)"
    ),
    dir: bool = typer.Option(
        False, "--dir", "-d", help="Create directory instead of upload"
    ),
    mode: str = typer.Option(
        "755", "--mode", "-m", help="Permission mode for directories"
    ),
    parents: bool = typer.Option(
        True, "--parents", "-p", help="Create parent directories"
    ),
):
    """Add file/directory to remote (upload or mkdir)."""
    if dir:
        from navig.commands.files_advanced import mkdir_cmd

        ctx.obj["parents"] = parents
        ctx.obj["mode"] = mode
        mkdir_cmd(str(local), ctx.obj)
    else:
        from navig.commands.files import upload_file_cmd

        upload_file_cmd(local, remote, ctx.obj)


@file_app.command("list")
def file_list(
    ctx: typer.Context,
    remote_path: str = typer.Argument(..., help="Remote directory path"),
    all: bool = typer.Option(False, "--all", "-a", help="Show hidden files"),
    tree: bool = typer.Option(False, "--tree", "-t", help="Show tree structure"),
    depth: int = typer.Option(2, "--depth", "-d", help="Tree depth (with --tree)"),
    tables: bool = typer.Option(
        False, "--tables", help="Show database tables (for db list)"
    ),
    containers: bool = typer.Option(False, "--containers", help="Show containers"),
    json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """List remote directory contents."""
    if json:
        ctx.obj["json"] = True
    if tree:
        from navig.commands.files_advanced import tree_cmd

        tree_cmd(remote_path, ctx.obj, depth=depth, dirs_only=False)
    else:
        from navig.commands.files_advanced import list_dir_cmd

        list_dir_cmd(remote_path, ctx.obj, all=all, long=True, human=True)


@file_app.command("show")
def file_show(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote file path"),
    download: Path | None = typer.Option(
        None, "--download", "-d", help="Download to local path"
    ),
    lines: str | None = typer.Option(
        None, "--lines", "-n", help="Number of lines or range (e.g., 50 or 100-200)"
    ),
    head: bool = typer.Option(False, "--head", help="Show first N lines"),
    tail: bool = typer.Option(False, "--tail", "-t", help="Show last N lines"),
    json: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """Show remote file contents or download."""
    if json:
        ctx.obj["json"] = True
    if download:
        from navig.commands.files import download_file_cmd

        download_file_cmd(remote, download, ctx.obj)
    else:
        from navig.commands.files_advanced import cat_file_cmd

        cat_file_cmd(remote, ctx.obj, lines=lines, head=head, tail=tail)


@file_app.command("edit")
def file_edit(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote file path"),
    content: str | None = typer.Option(
        None, "--content", "-c", help="Content to write"
    ),
    mode: str | None = typer.Option(None, "--mode", "-m", help="Set permissions"),
    owner: str | None = typer.Option(None, "--owner", "-o", help="Set ownership"),
    append: bool = typer.Option(
        False, "--append", "-a", help="Append instead of overwrite"
    ),
    stdin: bool = typer.Option(False, "--stdin", "-s", help="Read from stdin"),
    from_file: Path | None = typer.Option(
        None, "--from-file", "-f", help="Read from local file"
    ),
):
    """Edit remote file (write content, change permissions/owner)."""
    if content or stdin or from_file:
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
    elif mode:
        from navig.commands.files_advanced import chmod_cmd

        ctx.obj["recursive"] = False
        chmod_cmd(remote, mode, ctx.obj)
    elif owner:
        from navig.commands.files_advanced import chown_cmd

        ctx.obj["recursive"] = False
        chown_cmd(remote, owner, ctx.obj)
    else:
        ch.error("Specify --content, --mode, or --owner")


@file_app.command("get")
def file_get(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote file path"),
    local: Path | None = typer.Argument(None, help="Local destination path"),
):
    """Download file from remote."""
    from navig.commands.files import download_file_cmd

    download_file_cmd(remote, local, ctx.obj)


@file_app.command("remove")
def file_remove(
    ctx: typer.Context,
    remote: str = typer.Argument(..., help="Remote path to delete"),
    recursive: bool = typer.Option(
        False, "--recursive", "-r", help="Delete directories recursively"
    ),
    force: bool = typer.Option(False, "--force", "-f", help="Skip confirmation"),
):
    """Remove remote file or directory."""
    from navig.commands.files_advanced import delete_file_cmd

    ctx.obj["recursive"] = recursive
    ctx.obj["force"] = force
    delete_file_cmd(remote, ctx.obj)


# ============================================================================
# LOG OPERATIONS (Canonical 'log' group)
# ============================================================================
