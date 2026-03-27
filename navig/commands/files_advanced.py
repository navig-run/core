"""Advanced File Operation Commands - SECURE VERSION WITH COMMAND INJECTION PROTECTION"""

import json
import shlex  # CRITICAL: Import shlex for secure shell escaping
from pathlib import Path
from typing import Any

from navig import console_helper as ch


def delete_file_cmd(remote: str, options: dict[str, Any]):
    """Delete remote file or directory.

    Args:
        remote: Remote path to delete
        options: Command options (app, recursive, force, dry_run, json)
    """
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    server_name = options.get("app") or config_manager.get_active_server()
    if not server_name:
        ch.error("No active server.")
        return False

    server_config = config_manager.load_server_config(server_name)

    # SECURITY: Quote remote path to prevent command injection
    remote_quoted = shlex.quote(remote)

    # Check if path exists
    check_cmd = f"test -e {remote_quoted} && echo 'exists' || echo 'not_found'"
    result = remote_ops.execute_command(check_cmd, server_config)

    if "not_found" in result.stdout:
        msg = f"Path not found: {remote}"
        if options.get("json"):
            ch.raw_print(json.dumps({"success": False, "error": msg}))
        else:
            ch.error(msg)
        return False

    # Determine if directory
    is_dir_cmd = f"test -d {remote_quoted} && echo 'dir' || echo 'file'"
    result = remote_ops.execute_command(is_dir_cmd, server_config)
    is_directory = "dir" in result.stdout

    # Build delete command
    if is_directory:
        if not options.get("recursive"):
            msg = "Use --recursive to delete directories"
            if options.get("json"):
                ch.raw_print(json.dumps({"success": False, "error": msg}))
            else:
                ch.error(msg)
            return False
        delete_cmd = f"rm -rf {remote_quoted}"
    else:
        delete_cmd = f"rm -f {remote_quoted}"

    # Dry run mode
    if options.get("dry_run"):
        msg = f"Would delete: {remote} ({'directory' if is_directory else 'file'})"
        if options.get("json"):
            ch.raw_print(json.dumps({"success": True, "dry_run": True, "action": delete_cmd}))
        else:
            ch.info(f"[DRY RUN] {msg}")
        return True

    # Confirm unless forced
    if not options.get("force"):
        if not options.get("json"):
            if not ch.confirm_action(f"Delete {remote}?"):
                msg = "Cancelled."
                ch.warning(msg)
                return False
        else:
            # JSON mode: always require --force
            ch.raw_print(json.dumps({"success": False, "error": "Use --force in JSON mode"}))
            return False

    # Execute delete
    result = remote_ops.execute_command(delete_cmd, server_config)

    if result.returncode == 0:
        msg = f"Deleted: {remote}"
        if options.get("json"):
            ch.raw_print(json.dumps({"success": True, "path": remote}))
        else:
            ch.success(f"✓ {msg}")
        return True
    else:
        msg = result.stderr
        if options.get("json"):
            ch.raw_print(json.dumps({"success": False, "error": msg}))
        else:
            ch.error(f"Delete failed: {msg}")
        return False


def mkdir_cmd(remote: str, options: dict[str, Any]):
    """Create remote directory.

    Args:
        remote: Remote path to create
        options: Command options (app, parents, mode, dry_run, json)
    """
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    server_name = options.get("app") or config_manager.get_active_server()
    if not server_name:
        ch.error("No active server.")
        return False

    server_config = config_manager.load_server_config(server_name)

    # SECURITY: Validate and quote mode
    mode = options.get("mode", "755")
    if not mode.isdigit() or len(mode) not in [3, 4]:
        ch.error("Invalid mode. Use numeric format like '755' or '0644'")
        return False
    mode_quoted = shlex.quote(mode)

    # SECURITY: Quote remote path
    remote_quoted = shlex.quote(remote)

    # Build mkdir command
    mkdir_flags = []
    if options.get("parents"):
        mkdir_flags.append("-p")

    mkdir_cmd = f"mkdir {' '.join(mkdir_flags)} -m {mode_quoted} {remote_quoted}"

    # Dry run mode
    if options.get("dry_run"):
        msg = f"Would create: {remote} (mode: {mode})"
        if options.get("json"):
            ch.raw_print(json.dumps({"success": True, "dry_run": True, "action": mkdir_cmd}))
        else:
            ch.info(f"[DRY RUN] {msg}")
        return True

    # Execute mkdir
    result = remote_ops.execute_command(mkdir_cmd, server_config)

    if result.returncode == 0:
        msg = f"Created directory: {remote}"
        if options.get("json"):
            ch.raw_print(json.dumps({"success": True, "path": remote, "mode": mode}))
        else:
            ch.success(f"✓ {msg}")
        return True
    else:
        msg = result.stderr
        if options.get("json"):
            ch.raw_print(json.dumps({"success": False, "error": msg}))
        else:
            ch.error(f"mkdir failed: {msg}")
        return False


def chmod_cmd(remote: str, mode: str, options: dict[str, Any]):
    """Change file/directory permissions.

    Args:
        remote: Remote path
        mode: Permission mode (e.g., '755', '644')
        options: Command options (app, recursive, dry_run, json)
    """
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    server_name = options.get("app") or config_manager.get_active_server()
    if not server_name:
        ch.error("No active server.")
        return False

    server_config = config_manager.load_server_config(server_name)

    # SECURITY: Validate mode format
    if not mode.isdigit() or len(mode) not in [3, 4]:
        msg = "Invalid mode. Use numeric format like '755' or '0644'"
        if options.get("json"):
            ch.raw_print(json.dumps({"success": False, "error": msg}))
        else:
            ch.error(msg)
        return False

    # SECURITY: Quote all parameters
    mode_quoted = shlex.quote(mode)
    remote_quoted = shlex.quote(remote)

    # Build chmod command
    chmod_flags = []
    if options.get("recursive"):
        chmod_flags.append("-R")

    chmod_cmd = f"chmod {' '.join(chmod_flags)} {mode_quoted} {remote_quoted}"

    # Dry run mode
    if options.get("dry_run"):
        msg = f"Would set permissions: {remote} -> {mode}"
        if options.get("json"):
            ch.raw_print(json.dumps({"success": True, "dry_run": True, "action": chmod_cmd}))
        else:
            ch.info(f"[DRY RUN] {msg}")
        return True

    # Execute chmod
    result = remote_ops.execute_command(chmod_cmd, server_config)

    if result.returncode == 0:
        msg = f"Permissions set: {remote} -> {mode}"
        if options.get("json"):
            ch.raw_print(json.dumps({"success": True, "path": remote, "mode": mode}))
        else:
            ch.success(f"✓ {msg}")
        return True
    else:
        msg = result.stderr
        if options.get("json"):
            ch.raw_print(json.dumps({"success": False, "error": msg}))
        else:
            ch.error(f"chmod failed: {msg}")
        return False


def chown_cmd(remote: str, owner: str, options: dict[str, Any]):
    """Change file/directory ownership.

    Args:
        remote: Remote path
        owner: New owner (user or user:group)
        options: Command options (app, recursive, dry_run, json)
    """
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    server_name = options.get("app") or config_manager.get_active_server()
    if not server_name:
        ch.error("No active server.")
        return False

    server_config = config_manager.load_server_config(server_name)

    # SECURITY: Quote all parameters to prevent command injection
    owner_quoted = shlex.quote(owner)
    remote_quoted = shlex.quote(remote)

    # Build chown command
    chown_flags = []
    if options.get("recursive"):
        chown_flags.append("-R")

    chown_cmd = f"chown {' '.join(chown_flags)} {owner_quoted} {remote_quoted}"

    # Dry run mode
    if options.get("dry_run"):
        msg = f"Would change owner: {remote} -> {owner}"
        if options.get("json"):
            ch.raw_print(json.dumps({"success": True, "dry_run": True, "action": chown_cmd}))
        else:
            ch.info(f"[DRY RUN] {msg}")
        return True

    # Execute chown
    result = remote_ops.execute_command(chown_cmd, server_config)

    if result.returncode == 0:
        msg = f"Owner changed: {remote} -> {owner}"
        if options.get("json"):
            ch.raw_print(json.dumps({"success": True, "path": remote, "owner": owner}))
        else:
            ch.success(f"✓ {msg}")
        return True
    else:
        msg = result.stderr
        if options.get("json"):
            ch.raw_print(json.dumps({"success": False, "error": msg}))
        else:
            ch.error(f"chown failed: {msg}")
        return False


def cat_file_cmd(
    remote: str,
    options: dict[str, Any],
    lines: str | None = None,
    head: bool = False,
    tail: bool = False,
):
    """Read and display remote file contents.

    This is a convenience command that replaces:
        navig run "cat /path/to/file"

    Args:
        remote: Remote file path
        options: Command options
        lines: Number of lines to show (for head/tail) or range (e.g., "100-200")
        head: Show first N lines
        tail: Show last N lines
    """
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    host_name = options.get("host") or config_manager.get_active_host()
    if not host_name:
        ch.error("No active host.", "Use 'navig host use <name>' to set one.")
        return

    host_config = config_manager.load_host_config(host_name)

    # SECURITY: Quote remote path
    remote_quoted = shlex.quote(remote)

    # Parse lines parameter - could be single number or range
    start_line = None
    end_line = None
    line_count = None
    if lines:
        # Check if it's a range (e.g., "100-200" or "100:200")
        if "-" in lines and not lines.startswith("-"):
            parts = lines.split("-", 1)
            try:
                start_line = int(parts[0])
                end_line = int(parts[1])
            except ValueError:
                ch.error(f"Invalid line range: {lines}", "Use format: 100-200")
                return
        elif ":" in lines:
            parts = lines.split(":", 1)
            try:
                start_line = int(parts[0])
                end_line = int(parts[1])
            except ValueError:
                ch.error(f"Invalid line range: {lines}", "Use format: 100:200")
                return
        else:
            # Single number
            try:
                line_count = int(lines)
            except ValueError:
                ch.error(
                    f"Invalid line number: {lines}",
                    "Use an integer or range (e.g., 100-200)",
                )
                return

    # Build command based on options
    if start_line and end_line:
        # Range specified - use sed
        cmd = f"sed -n '{start_line},{end_line}p' {remote_quoted}"
    elif head and line_count:
        cmd = f"head -n {line_count} {remote_quoted}"
    elif tail and line_count:
        cmd = f"tail -n {line_count} {remote_quoted}"
    elif line_count:
        cmd = f"head -n {line_count} {remote_quoted}"
    else:
        cmd = f"cat {remote_quoted}"

    # Check if file exists first
    check_cmd = f"test -f {remote_quoted} && echo 'exists' || echo 'not_found'"
    result = remote_ops.execute_command(check_cmd, host_config)

    if "not_found" in result.stdout:
        if options.get("json"):
            ch.raw_print(
                json.dumps(
                    {
                        "schema_version": "1.0.0",
                        "command": "file.show",
                        "success": False,
                        "path": remote,
                        "error": "file_not_found",
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
        else:
            ch.error(f"File not found: {remote}")
        return

    if options.get("json"):
        # JSON mode captures output instead of streaming.
        out = remote_ops.execute_command(cmd, host_config, capture_output=True)
        ch.raw_print(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "command": "file.show",
                    "success": out.returncode == 0,
                    "path": remote,
                    "stdout": out.stdout,
                    "stderr": out.stderr,
                    "returncode": out.returncode,
                    "lines": lines,
                    "head": bool(head),
                    "tail": bool(tail),
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    if not options.get("quiet") and not options.get("raw"):
        ch.info(f"Contents of {remote}:")
        ch.console.print()

    # Execute and display
    remote_ops.execute_command(cmd, host_config, capture_output=False)


def write_file_cmd(
    remote: str,
    content: str | None,
    options: dict[str, Any],
    stdin: bool = False,
    local_file: Path | None = None,
    append: bool = False,
    mode: str | None = None,
    owner: str | None = None,
):
    """Write content to a remote file.

    This is a convenience command that replaces complex heredoc patterns like:
        navig run "cat > /path/file << 'EOF' ... EOF"

    Instead, you can use:
        navig write-file /path/file --content "file contents"
        navig write-file /path/file --stdin  (pipe content)
        navig write-file /path/file --from-file local.txt

    Args:
        remote: Remote file path to write
        content: Content to write (string)
        options: Command options
        stdin: Read content from stdin
        local_file: Read content from local file
        append: Append to file instead of overwrite
        mode: Set file permissions after writing
        owner: Set file owner after writing
    """
    import os
    import sys
    import tempfile

    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    host_name = options.get("host") or config_manager.get_active_host()
    if not host_name:
        ch.error("No active host.", "Use 'navig host use <name>' to set one.")
        return False

    host_config = config_manager.load_host_config(host_name)

    # Resolve content from the appropriate source
    final_content = None

    if stdin:
        # Read from stdin
        if sys.stdin.isatty():
            ch.error(
                "No input provided via stdin.",
                "Pipe content to this command or use --content.",
            )
            return False
        final_content = sys.stdin.read()
    elif local_file:
        # Read from local file
        if not local_file.exists():
            ch.error(f"Local file not found: {local_file}")
            return False
        final_content = local_file.read_text()
    elif content:
        final_content = content
    else:
        ch.error("No content provided.", "Use --content, --stdin, or --from-file.")
        return False

    if not final_content:
        ch.error("Content is empty.")
        return False

    # Confirm write operation
    if not ch.confirm_operation(
        operation_name=f"Write to: {remote}",
        operation_type="standard",
        host=host_name,
        details=f"{'Append' if append else 'Overwrite'} with {len(final_content)} bytes",
        auto_confirm=options.get("yes", False),
        force_confirm=options.get("confirm", False),
    ):
        ch.warning("Cancelled.")
        return False

    # Strategy: Write content to a temp file locally, upload via SCP, then move to final destination
    # This is more reliable than trying to escape content for shell commands

    try:
        # Create temp file with content
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".tmp", delete=False, encoding="utf-8"
        ) as tf:
            tf.write(final_content)
            temp_path = Path(tf.name)

        # Determine temp remote path
        remote_temp = f"/tmp/.navig_write_{os.getpid()}.tmp"

        if not options.get("quiet"):
            ch.info(f"Writing to {remote}...")

        # Upload to temp location
        success = remote_ops.upload_file(temp_path, remote_temp, host_config)

        if not success:
            ch.error("Failed to upload content to server.")
            return False

        # Move to final destination (or append)
        remote_quoted = shlex.quote(remote)

        if append:
            move_cmd = f"cat {shlex.quote(remote_temp)} >> {remote_quoted} && rm -f {shlex.quote(remote_temp)}"
        else:
            move_cmd = f"mv -f {shlex.quote(remote_temp)} {remote_quoted}"

        result = remote_ops.execute_command(move_cmd, host_config)

        if result.returncode != 0:
            ch.error(f"Failed to write file: {result.stderr}")
            return False

        # Set permissions if specified
        if mode:
            chmod_result = remote_ops.execute_command(
                f"chmod {shlex.quote(mode)} {remote_quoted}", host_config
            )
            if chmod_result.returncode != 0:
                ch.warning(f"Failed to set permissions: {chmod_result.stderr}")

        # Set owner if specified
        if owner:
            chown_result = remote_ops.execute_command(
                f"chown {shlex.quote(owner)} {remote_quoted}", host_config
            )
            if chown_result.returncode != 0:
                ch.warning(f"Failed to set owner: {chown_result.stderr}")

        ch.success(f"✓ Written {len(final_content)} bytes to {remote}")
        return True

    finally:
        # Clean up local temp file
        if temp_path.exists():
            temp_path.unlink()


def list_dir_cmd(
    remote: str,
    options: dict[str, Any],
    all: bool = False,
    long: bool = True,
    human: bool = True,
):
    """List remote directory contents.

    This is a convenience command that replaces:
        navig run "ls -la /path"

    Args:
        remote: Remote directory path
        options: Command options
        all: Show hidden files (dotfiles)
        long: Long format with details
        human: Human-readable sizes
    """
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    host_name = options.get("host") or config_manager.get_active_host()
    if not host_name:
        ch.error("No active host.", "Use 'navig host use <name>' to set one.")
        return

    host_config = config_manager.load_host_config(host_name)

    # SECURITY: Quote remote path
    remote_quoted = shlex.quote(remote)

    # Build ls command
    flags = []
    if long:
        flags.append("l")
    if all:
        flags.append("a")
    if human:
        flags.append("h")

    if flags:
        cmd = f"ls -{''.join(flags)} {remote_quoted}"
    else:
        cmd = f"ls {remote_quoted}"

    if options.get("json"):
        # Prefer a structured listing from find. Fallback to ls -1.
        # %y: file type (f,d,l,...) %s: size bytes %TY-%Tm-%Td %TH:%TM:%TS: mtime
        find_cmd = (
            f"find {remote_quoted} -maxdepth 1 -mindepth 1 "
            f"-printf '%f|%y|%s|%TY-%Tm-%TdT%TH:%TM:%TS\\n' 2>/dev/null"
        )
        out = remote_ops.execute_command(find_cmd, host_config, capture_output=True)

        entries = []
        if out.returncode == 0 and out.stdout:
            for line in out.stdout.splitlines():
                parts = line.split("|", 3)
                if len(parts) != 4:
                    continue
                name, ftype, size, mtime = parts
                entries.append(
                    {
                        "name": name,
                        "type": ftype,
                        "size": int(size) if str(size).isdigit() else None,
                        "mtime": mtime,
                    }
                )
        else:
            # Fallback: best-effort names.
            ls_flags = "-1A" if all else "-1"
            ls_out = remote_ops.execute_command(
                f"ls {ls_flags} {remote_quoted}", host_config, capture_output=True
            )
            if ls_out.stdout:
                for name in ls_out.stdout.splitlines():
                    if name.strip():
                        entries.append(
                            {
                                "name": name.strip(),
                                "type": None,
                                "size": None,
                                "mtime": None,
                            }
                        )
            out = ls_out

        ch.raw_print(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "command": "file.list",
                    "success": out.returncode == 0,
                    "path": remote,
                    "entries": entries,
                    "count": len(entries),
                    "stderr": out.stderr,
                    "returncode": out.returncode,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    if not options.get("quiet") and not options.get("raw"):
        ch.info(f"Contents of {remote}:")
        ch.console.print()

    remote_ops.execute_command(cmd, host_config, capture_output=False)


def tree_cmd(remote: str, options: dict[str, Any], depth: int = 2, dirs_only: bool = False):
    """Show directory tree structure.

    Args:
        remote: Remote directory path
        options: Command options
        depth: Maximum depth to display
        dirs_only: Show only directories
    """
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    host_name = options.get("host") or config_manager.get_active_host()
    if not host_name:
        ch.error("No active host.", "Use 'navig host use <name>' to set one.")
        return

    host_config = config_manager.load_host_config(host_name)

    # SECURITY: Quote remote path
    remote_quoted = shlex.quote(remote)

    # Build tree command (fallback to find if tree not installed)
    cmd_parts = ["tree"]
    cmd_parts.append(f"-L {depth}")
    if dirs_only:
        cmd_parts.append("-d")
    cmd_parts.append(remote_quoted)

    tree_cmd = " ".join(cmd_parts)

    # Fallback using find if tree is not installed
    fallback_cmd = f"find {remote_quoted} -maxdepth {depth} -print | sort | head -100"

    full_cmd = f"command -v tree >/dev/null 2>&1 && {tree_cmd} || ({fallback_cmd})"

    if not options.get("quiet") and not options.get("raw"):
        ch.info(f"Tree view of {remote}:")
        ch.console.print()

    remote_ops.execute_command(full_cmd, host_config, capture_output=False)
