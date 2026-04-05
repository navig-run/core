"""Remote Command Execution"""

import base64
import os
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import typer

from navig import console_helper as ch


def _check_powershell_quoting_issues(
    command: str | None,
    stdin: bool,
    file: Path | None,
    interactive: bool,
    options: dict[str, Any],
) -> None:
    """Detect PowerShell quoting problems and provide actionable guidance.

    PowerShell parses parentheses, brackets, and special chars as syntax,
    breaking complex commands. This detects the issue and suggests stdin/file.
    """
    # Skip if already using a safe input method
    if stdin or file or interactive or command is None:
        return

    # Skip if command is too simple to have issues
    if len(command) < 20:
        return

    # Detect if we're running in PowerShell
    is_powershell = False
    parent_process = os.environ.get("TERM_PROGRAM", "").lower()
    shell_name = os.environ.get("SHELL", "").lower()

    if sys.platform == "win32":
        # On Windows, PowerShell is the default
        is_powershell = True
        # cmd.exe sets PROMPT, PowerShell doesn't
        if os.environ.get("PROMPT"):
            is_powershell = False
    elif "powershell" in parent_process or "pwsh" in shell_name:
        is_powershell = True

    if not is_powershell:
        return

    # Check for PowerShell-problematic patterns
    powershell_problems = [
        ("(", "parentheses - PowerShell treats them as syntax"),
        (")", "parentheses - PowerShell treats them as syntax"),
        ("{", "curly braces - PowerShell treats them as script blocks"),
        ("$", "dollar signs - PowerShell treats them as variables"),
        ("`", "backticks - PowerShell treats them as escape characters"),
    ]

    found_issues = []
    for pattern, reason in powershell_problems:
        if pattern in command:
            found_issues.append(reason)

    # Only warn if multiple problematic patterns found (avoid false positives)
    if len(found_issues) >= 2 and not options.get("b64"):
        ch.warning(
            "PowerShell detected: This command has special characters that PowerShell may misinterpret.",
            "Use one of these methods to avoid quoting issues:",
        )
        ch.console.print()
        ch.info("Method 1: Use stdin (recommended for one-liners)")
        ch.dim("  @'")
        ch.dim(f"  {command}")
        ch.dim("  '@ | navig run --b64 --stdin")
        ch.console.print()
        ch.info("Method 2: Save to file (recommended for reusable commands)")
        ch.dim(f"  echo '{command}' > cmd.txt")
        ch.dim("  navig run --b64 --file cmd.txt")
        ch.console.print()
        ch.info("Method 3: Use stop-parsing token")
        ch.dim(f'  navig run --b64 --% "{command}"')
        ch.console.print()
        ch.dim("Issues detected: " + ", ".join(set(found_issues)))
        ch.console.print()


def run_remote_command(
    command: str | None,
    options: dict[str, Any],
    stdin: bool = False,
    file: Path | None = None,
    interactive: bool = False,
):
    """Execute arbitrary shell command on remote host.

    Args:
        command: Shell command to execute (from CLI argument), or @- for stdin, @file for file
        options: CLI context options
        stdin: If True, read command from stdin (bypasses shell escaping)
        file: If provided, read command from this file (bypasses shell escaping)
        interactive: If True, open editor for multi-line input

    For complex commands with heredocs, JSON, or special characters,
    use --b64 flag to encode as Base64 and bypass all shell escaping issues.
    """
    from navig.cli.recovery import require_active_host
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    host_name = require_active_host(options, config_manager)

    host_config = config_manager.load_host_config(host_name)
    is_local_host = config_manager.is_local_host(host_name)

    # Detect PowerShell and warn about quoting issues for complex commands
    _check_powershell_quoting_issues(command, stdin, file, interactive, options)

    # Resolve the command from the appropriate source
    final_command = _resolve_command(command, stdin, file, interactive)
    if final_command is None:
        return  # Error already printed by _resolve_command

    # Store original command for display purposes before base64 encoding
    display_command = final_command

    # Apply Base64 encoding if requested
    use_b64 = options.get("b64", False)
    if use_b64:
        # Check if input is already base64-encoded (auto-detection)
        decoded_cmd = _try_decode_b64(final_command)
        if is_local_host:
            if decoded_cmd:
                display_command = decoded_cmd
                final_command = decoded_cmd
            else:
                ch.dim("Local host detected: skipping SSH Base64 wrapper.")
        elif decoded_cmd:
            # Was already base64 - use decoded for display, original for execution
            display_command = decoded_cmd
            final_command = _encode_b64_command(decoded_cmd)
        else:
            # Not base64 - encode it now
            final_command = _encode_b64_command(final_command)

        if final_command is None:
            return  # Error already printed

    if options.get("dry_run"):
        # For multi-line commands, show preview
        preview = final_command if len(final_command) < 200 else final_command[:200] + "..."
        ch.dim(f"Would execute: {preview}")
        if "\n" in final_command:
            ch.dim(f"(Multi-line command with {final_command.count(chr(10)) + 1} lines)")
        return

    # Check if command requires confirmation based on configured level
    command_type = ch.classify_command(display_command)
    preview = display_command if len(display_command) < 80 else display_command[:80] + "..."

    if not ch.confirm_operation(
        operation_name=f"Run: {preview}",
        operation_type=command_type,
        host=host_name,
        auto_confirm=options.get("yes", False),
        force_confirm=options.get("confirm", False),
    ):
        ch.warning("Cancelled.")
        return

    # Add blank line before execution info for visual separation
    if not options.get("json"):
        ch.console.print()

    # Show command info - use original command for display when b64 was used
    if not options.get("json"):
        if "\n" in display_command:
            # Multi-line command - show first line and line count
            first_line = display_command.split("\n")[0]
            line_count = display_command.count("\n") + 1
            ch.info(f"Executing multi-line command ({line_count} lines):")
            ch.dim(f"  {first_line}...")
        elif len(display_command) > 100:
            # Long command - show preview
            preview = display_command[:100]
            ch.info(f"Executing: {preview}...", no_wrap=True)
        else:
            # Short command - show full
            ch.info(f"Executing: {display_command}", no_wrap=True)

    # Warn early if host uses mDNS — unreliable and slow on Windows
    _host_addr = host_config.get("host", "")
    if _host_addr.endswith(".local") and sys.platform == "win32":
        ch.warning(
            f"Host uses mDNS ({_host_addr}) — can be slow or unreliable on "
            "Windows. Consider setting the host to an IP address instead.",
        )

    if options.get("json"):
        # JSON mode: capture output and emit a single JSON object.
        try:
            if is_local_host:
                result = _execute_local_command(final_command, capture_output=True)
            else:
                result = remote_ops.execute_command(final_command, host_config, capture_output=True)
        except RuntimeError as e:
            import json as _json

            ch.raw_print(_json.dumps({"error": str(e), "success": False}, indent=2))
            raise typer.Exit(1) from e
        import json as _json

        ch.raw_print(
            _json.dumps(
                {
                    "schema_version": "1.0.0",
                    "command": "run",
                    "success": result.returncode == 0,
                    "host": host_name,
                    "returncode": result.returncode,
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    # Print newline before command output for clean separation
    ch.console.print()

    # Check if output is piped (non-interactive) - skip progress indicator
    is_interactive = sys.stdout.isatty()
    is_raw_mode = options.get("raw", False)

    try:
        if is_local_host:
            result = _execute_local_command(final_command, capture_output=False)
        elif is_interactive and not is_raw_mode:
            # Show progress indicator for interactive sessions
            result = _execute_with_progress(remote_ops, final_command, host_config)
        else:
            # Direct execution for piped/raw output
            result = remote_ops.execute_command(final_command, host_config, capture_output=False)
    except RuntimeError as e:
        ch.error(str(e))
        raise typer.Exit(1) from e

    # Print newline after command output for clean separation
    ch.console.print()

    if result.returncode != 0:
        ch.warning(f"Command exited with code: {result.returncode}")


def _execute_with_progress(remote_ops, command: str, host_config: dict[str, Any]):
    """Execute command with elapsed time indicator for long-running commands.

    Shows elapsed time after 3 seconds of waiting. The indicator updates
    in the terminal line above the command output area.
    """
    import pathlib as _pl
    import shutil
    import subprocess

    # Resolve ssh binary — 32-bit Python on 64-bit Windows cannot find System32\OpenSSH via PATH
    def _find_ssh():
        b = shutil.which("ssh") or shutil.which("ssh.exe")
        if b:
            return b
        _sr = os.environ.get("SystemRoot", "C:/Windows")
        for _c in [
            _pl.Path(_sr) / "SysNative" / "OpenSSH" / "ssh.exe",
            _pl.Path(_sr) / "System32" / "OpenSSH" / "ssh.exe",
        ]:
            if _c.exists():
                return str(_c)
        raise FileNotFoundError("ssh.exe not found")

    # Build SSH command (same logic as RemoteOperations.execute_command)
    ssh_args = [_find_ssh()]
    ssh_args.extend(["-o", "StrictHostKeyChecking=yes"])
    ssh_args.extend(["-o", "ConnectTimeout=10"])

    if host_config.get("port", 22) != 22:
        ssh_args.extend(["-p", str(host_config["port"])])

    if host_config.get("ssh_key"):
        ssh_args.extend(["-i", host_config["ssh_key"]])

    ssh_args.append(f"{host_config['user']}@{host_config['host']}")
    ssh_args.append(command)

    # Track execution state
    stop_event = threading.Event()
    start_time = time.time()
    output_started = threading.Event()

    def progress_indicator():
        """Background thread showing elapsed time."""
        # Wait 3 seconds before showing timer (short commands won't trigger it)
        for _ in range(30):  # 3 seconds in 0.1s increments
            if stop_event.is_set() or output_started.is_set():
                return
            time.sleep(0.1)

        # Now show elapsed time updates
        spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        spinner_idx = 0

        while not stop_event.is_set() and not output_started.is_set():
            elapsed = time.time() - start_time
            spinner = spinner_chars[spinner_idx % len(spinner_chars)]
            # Print status on same line using carriage return
            sys.stderr.write(f"\r\033[K{spinner} Running... ({elapsed:.0f}s)")
            sys.stderr.flush()
            spinner_idx += 1
            time.sleep(0.2)

        # Clear the progress line when done
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()

    # Start progress indicator thread
    progress_thread = threading.Thread(target=progress_indicator, daemon=True)
    progress_thread.start()

    try:
        # Execute command — output streams directly to terminal
        _timeout = int(os.environ.get("NAVIG_SSH_TIMEOUT", "30"))
        try:
            result = subprocess.run(ssh_args, timeout=_timeout)
        except subprocess.TimeoutExpired as _exc:
            _host = host_config.get("host", "?")
            raise RuntimeError(
                f"SSH connection timed out after {_timeout}s — "
                f"'{_host}' is unreachable or not responding."
            ) from _exc
        return result
    finally:
        # Signal progress thread to stop
        stop_event.set()
        progress_thread.join(timeout=1)


def _execute_local_command(
    command: str, capture_output: bool = True
) -> subprocess.CompletedProcess:
    """Execute command on the local machine directly (no SSH)."""
    try:
        if capture_output:
            return subprocess.run(  # noqa: S602
                command,
                shell=True,
                capture_output=True,
                text=True,
            )
        return subprocess.run(command, shell=True)  # noqa: S602
    except Exception as _exc:  # noqa: BLE001
        raise RuntimeError(f"Local command failed: {_exc}") from _exc


def _resolve_command(
    command: str | None, stdin: bool, file: Path | None, interactive: bool = False
) -> str | None:
    """Resolve command from the appropriate source.

    Priority:
    1. --interactive: Open editor for multi-line input
    2. --stdin or @-: Read from standard input
    3. --file or @filename: Read from file
    4. command argument: Use directly

    Returns:
        The command string, or None if error.
    """
    # Handle @- syntax (read from stdin)
    if command == "@-":
        stdin = True
        command = None

    # Handle @filename syntax (read from file)
    if command and command.startswith("@") and command != "@-":
        file = Path(command[1:])  # Remove @ prefix
        command = None

    # Handle interactive mode
    if interactive:
        return _read_from_editor()

    sources_count = sum([stdin, file is not None, command is not None and command.strip() != ""])

    if sources_count == 0:
        ch.error("No command specified.")
        ch.info("Examples:")
        ch.dim("  navig run 'ls -la'              # Direct command")
        ch.dim("  navig run --b64 'curl ...'      # Base64 encoded")
        ch.dim("  navig run @script.sh            # From file")
        ch.dim("  cat script.sh | navig run @-    # From stdin")
        ch.dim("  navig run -i                    # Interactive editor")
        return None

    if sources_count > 1:
        ch.error("Multiple command sources specified.")
        ch.info("Use only ONE of: command argument, @-, @file, --stdin, or --file")
        return None

    # Read from stdin
    if stdin:
        if sys.stdin.isatty():
            ch.error("Stdin mode but no input piped.")
            ch.info("Usage: echo 'command' | navig run @-")
            ch.info("       cat script.sh | navig run --stdin")
            return None

        try:
            cmd = sys.stdin.read()
            if not cmd.strip():
                ch.error("Empty command received from stdin.")
                return None
            ch.dim(f"Read {len(cmd)} bytes from stdin")
            return cmd
        except Exception as e:
            ch.error(f"Failed to read from stdin: {e}")
            return None

    # Read from file
    if file is not None:
        file_path = Path(file).expanduser()
        if not file_path.exists():
            ch.error(f"File not found: '{file_path}'")
            ch.info("Check:")
            ch.dim("  • File path is correct (relative to current directory)")
            ch.dim("  • File exists and is readable")
            return None
        if not file_path.is_file():
            ch.error(f"Not a file: {file_path}")
            return None

        try:
            cmd = file_path.read_text(encoding="utf-8")
            if not cmd.strip():
                ch.error(f"Command file is empty: {file_path}")
                return None
            ch.dim(f"Read {len(cmd)} bytes from {file_path.name}")
            return cmd
        except Exception as e:
            ch.error(f"Failed to read command file: {e}")
            return None

    # Use command argument directly
    return command


def _read_from_editor() -> str | None:
    """Open editor for multi-line command input.

    Uses $EDITOR environment variable, falls back to platform defaults.
    """
    # Determine editor
    editor = os.environ.get("EDITOR")
    if not editor:
        if sys.platform == "win32":
            editor = "notepad.exe"
        else:
            # Try common editors
            for ed in ["nano", "vim", "vi"]:
                try:
                    subprocess.run(["which", ed], capture_output=True, check=True)
                    editor = ed
                    break
                except (subprocess.CalledProcessError, FileNotFoundError):
                    continue

    if not editor:
        ch.error("No text editor found.")
        ch.info("Solutions:")
        ch.dim("  • Set EDITOR environment variable: export EDITOR=nano")
        ch.dim("  • Use file input instead: navig run @script.sh")
        ch.dim("  • Use stdin: cat script.sh | navig run @-")
        return None

    # Create temp file with helpful template
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".sh", delete=False, encoding="utf-8"
        ) as f:
            f.write("# Enter your command below (this line will be removed)\n")
            f.write("# Save and close the editor to execute\n")
            f.write("\n")
            temp_path = f.name

        ch.info(f"Opening editor: {editor}")
        ch.dim("Save and close the editor to execute the command")

        # Open editor
        result = subprocess.run([editor, temp_path])
        if result.returncode != 0:
            ch.error(f"Editor exited with code {result.returncode}")
            return None

        # Read the edited content
        content = Path(temp_path).read_text(encoding="utf-8")

        # Remove comment lines at the start
        lines = content.split("\n")
        while lines and lines[0].strip().startswith("#"):
            lines.pop(0)

        cmd = "\n".join(lines).strip()

        if not cmd:
            ch.warning("No command entered. Cancelled.")
            return None

        ch.dim(f"Read {len(cmd)} bytes from editor")
        return cmd

    except Exception as e:
        ch.error(f"Editor error: {e}")
        return None
    finally:
        # Clean up temp file
        try:
            if "temp_path" in locals():
                Path(temp_path).unlink(missing_ok=True)
        except Exception:  # noqa: BLE001
            pass  # best-effort; failure is non-critical


def _encode_b64_command(command: str) -> str:
    """Encode command as Base64 for escape-proof transmission.

    The command is encoded locally and decoded on the remote server,
    completely bypassing all shell escaping issues.

    Returns:
        SSH command that decodes and executes the Base64 payload
    """
    try:
        # Encode the command as Base64
        encoded = base64.b64encode(command.encode("utf-8")).decode("ascii")

        # Wrap in decode-and-execute shell command
        # Using single quotes around the Base64 string (safe - only alphanumeric + /+=)
        b64_command = f"echo '{encoded}' | base64 -d | bash"

        ch.dim(f"Encoded {len(command)} bytes → {len(encoded)} bytes Base64")
        return b64_command

    except Exception as e:
        ch.error(f"Failed to encode command as Base64: {e}")
        return None


def _try_decode_b64(text: str) -> str | None:
    """Try to decode a string as Base64. Returns decoded string or None.

    Used to detect if user already passed base64-encoded command.
    """
    text = text.strip()

    # Quick check: base64 strings are alphanumeric + /+= only
    if not all(
        c in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=\n\r" for c in text
    ):
        return None

    # Try to decode
    try:
        decoded_bytes = base64.b64decode(text, validate=True)
        decoded = decoded_bytes.decode("utf-8")

        # Sanity check: decoded text should look like a shell command
        # (contains common shell characters, not just random bytes)
        if len(decoded) > 0 and any(c in decoded for c in " /.-;|&"):
            return decoded
        return None
    except Exception:
        return None


def install_remote_package(package: str, options: dict[str, Any]):
    """Auto-detect package manager and install."""
    from navig.cli.recovery import require_active_host
    from navig.config import get_config_manager
    from navig.remote import RemoteOperations

    config_manager = get_config_manager()
    host_name = require_active_host(options, config_manager)

    host_config = config_manager.load_host_config(host_name)
    remote_ops = RemoteOperations(host_config)

    ch.info(f"📦 Installing package: {package}")

    # Detect package manager based on OS metadata or by checking commands
    os_type = host_config.get("metadata", {}).get("os", "").lower()

    # Try to detect package manager
    package_managers = [
        {
            "cmd": "apt-get",
            "install": f"apt-get install -y {package}",
            "systems": ["ubuntu", "debian"],
        },
        {
            "cmd": "yum",
            "install": f"yum install -y {package}",
            "systems": ["centos", "rhel", "fedora", "rocky", "alma"],
        },
        {
            "cmd": "dnf",
            "install": f"dnf install -y {package}",
            "systems": ["fedora", "rhel 8", "centos 8"],
        },
        {
            "cmd": "pacman",
            "install": f"pacman -S --noconfirm {package}",
            "systems": ["arch", "manjaro"],
        },
        {
            "cmd": "zypper",
            "install": f"zypper install -y {package}",
            "systems": ["opensuse", "suse"],
        },
        {"cmd": "apk", "install": f"apk add {package}", "systems": ["alpine"]},
    ]

    # First try OS-based detection
    detected_pm = None
    for pm in package_managers:
        if any(sys in os_type for sys in pm["systems"]):
            detected_pm = pm
            break

    # If no OS match, check which package manager exists
    if not detected_pm:
        ch.info("   Auto-detecting package manager...")
        for pm in package_managers:
            result = remote_ops.execute_command(f"which {pm['cmd']}")
            if result["success"] and result["exit_code"] == 0:
                detected_pm = pm
                ch.success(f"   ✓ Detected: {pm['cmd']}")
                break

    if not detected_pm:
        ch.error("Could not detect package manager.")
        ch.info("Supported: apt-get, yum, dnf, pacman, zypper, apk")
        ch.info(f'Try manually: navig run "<package-manager> install {package}"')
        return

    # Dry-run check
    if options.get("dry_run"):
        ch.info(f"[DRY RUN] Would execute: {detected_pm['install']}")
        return

    # Execute installation
    ch.info(f"   Using: {detected_pm['cmd']}")
    result = remote_ops.execute_command(detected_pm["install"])

    if result["success"] and result["exit_code"] == 0:
        ch.success(f"✅ Package installed: {package}")
        if result["output"]:
            ch.dim(f"\n{result['output']}")
    else:
        ch.error(f"❌ Installation failed: {package}")
        if result.get("error"):
            ch.error(f"Error: {result['error']}")
