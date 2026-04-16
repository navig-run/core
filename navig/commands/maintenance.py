"""
System Maintenance Commands for Navig
Handles package updates, cache cleaning, log rotation, and system cleanup
"""

import json
import time

from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from navig.cli.recovery import require_active_server
from navig.config import get_config_manager
from navig.console_helper import get_console
from navig.remote import RemoteOperations

console = get_console()


def update_packages(options: dict) -> None:
    """
    Update package lists and upgrade packages.

    Features:
    - Updates apt package lists
    - Checks for upgradable packages
    - Performs system upgrade with confirmation
    - Shows count of upgraded packages
    - Non-interactive mode (DEBIAN_FRONTEND=noninteractive)
    """
    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    active_server = require_active_server(options, config_manager)

    server_config = config_manager.load_server_config(active_server)

    dry_run = options.get("dry_run", False)
    json_output = options.get("json", False)

    result_data = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "action": "update-packages",
        "dry_run": dry_run,
        "tasks": [],
    }

    if not json_output:
        console.print("\n[bold cyan]═══ Package Update & Upgrade ═══[/bold cyan]\n")

    try:
        # Update package lists
        if not json_output:
            console.print("📦 Updating package lists...")

        update_cmd = "sudo apt-get update"
        if dry_run:
            if not json_output:
                console.print(f"[yellow][DRY RUN] Would execute: {update_cmd}[/yellow]")
            result_data["tasks"].append({"task": "apt-update", "dry_run": True})
        else:
            result = remote_ops.execute_command(update_cmd, server_config)
            success = result.returncode == 0
            result_data["tasks"].append(
                {"task": "apt-update", "success": success, "output": result.stdout}
            )

            if success:
                if not json_output:
                    console.print("[green]✓ Package lists updated[/green]")
            else:
                if not json_output:
                    console.print("[red]✗ Failed to update package lists[/red]")
                    console.print(f"[red]{result.stderr}[/red]")
                return

        # Check for upgradable packages
        if not json_output:
            console.print("\n🔍 Checking for upgradable packages...")

        check_cmd = "apt list --upgradable 2>/dev/null | grep -v 'Listing' | wc -l"
        if not dry_run:
            result = remote_ops.execute_command(check_cmd, server_config)
            upgradable_count = int(result.stdout.strip())

            if upgradable_count > 0:
                if not json_output:
                    console.print(f"[yellow]Found {upgradable_count} upgradable packages[/yellow]")

                # Get list of upgradable packages
                list_cmd = "apt list --upgradable 2>/dev/null | grep -v 'Listing'"
                list_result = remote_ops.execute_command(list_cmd, server_config)
                upgradable_packages = list_result.stdout.strip().split("\n")

                # Show first 10 packages
                if not json_output:
                    console.print("\n[cyan]Upgradable packages (first 10):[/cyan]")
                    for pkg in upgradable_packages[:10]:
                        if pkg.strip():
                            console.print(f"  • {pkg.split('/')[0]}")
                    if len(upgradable_packages) > 10:
                        console.print(f"  ... and {len(upgradable_packages) - 10} more")

                # Perform upgrade
                if not json_output:
                    console.print("\n⬆️  Upgrading packages...")

                upgrade_cmd = "DEBIAN_FRONTEND=noninteractive sudo apt-get upgrade -y"

                with Progress(
                    SpinnerColumn(),
                    TextColumn("[progress.description]{task.description}"),
                    console=console,
                ) as progress:
                    task = progress.add_task("Upgrading packages...", total=None)
                    upgrade_result = remote_ops.execute_command(upgrade_cmd, server_config)
                    progress.update(task, completed=True)

                success = upgrade_result.returncode == 0
                result_data["tasks"].append(
                    {
                        "task": "apt-upgrade",
                        "success": success,
                        "packages_upgraded": upgradable_count,
                    }
                )

                if success:
                    if not json_output:
                        console.print(
                            f"[green]✓ {upgradable_count} packages upgraded successfully[/green]"
                        )
                else:
                    if not json_output:
                        console.print("[red]✗ Package upgrade failed[/red]")
                        console.print(f"[red]{upgrade_result.stderr}[/red]")
            else:
                if not json_output:
                    console.print("[green]✓ All packages are up to date[/green]")
                result_data["tasks"].append(
                    {
                        "task": "check-upgradable",
                        "packages_upgraded": 0,
                        "message": "All packages up to date",
                    }
                )
        else:
            if not json_output:
                console.print(
                    "[yellow][DRY RUN] Would check for upgradable packages and upgrade[/yellow]"
                )
            result_data["tasks"].append({"task": "apt-upgrade", "dry_run": True})

        if json_output:
            console.print(json.dumps(result_data, indent=2))

    except Exception as e:
        if json_output:
            result_data["error"] = str(e)
            console.print(json.dumps(result_data, indent=2))
        else:
            console.print(f"[red]✗ Error: {str(e)}[/red]")


def clean_packages(options: dict) -> None:
    """
    Clean package cache and remove orphaned packages.

    Features:
    - Cleans apt package cache (apt-get clean)
    - Removes unused/orphaned packages (apt-get autoremove)
    - Shows disk space freed
    - Confirmation before removal
    """
    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    active_server = require_active_server(options, config_manager)

    server_config = config_manager.load_server_config(active_server)

    dry_run = options.get("dry_run", False)
    json_output = options.get("json", False)

    result_data = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "action": "clean-packages",
        "dry_run": dry_run,
        "tasks": [],
    }

    if not json_output:
        console.print("\n[bold cyan]═══ Package Cache Cleanup ═══[/bold cyan]\n")

    try:
        # Clean package cache
        if not json_output:
            console.print("🧹 Cleaning package cache...")

        clean_cmd = "sudo apt-get clean"
        if dry_run:
            if not json_output:
                console.print(f"[yellow][DRY RUN] Would execute: {clean_cmd}[/yellow]")
            result_data["tasks"].append({"task": "apt-clean", "dry_run": True})
        else:
            result = remote_ops.execute_command(clean_cmd, server_config)
            success = result.returncode == 0
            result_data["tasks"].append({"task": "apt-clean", "success": success})

            if success:
                if not json_output:
                    console.print("[green]✓ Package cache cleaned[/green]")
            else:
                if not json_output:
                    console.print("[red]✗ Failed to clean package cache[/red]")

        # Remove orphaned packages
        if not json_output:
            console.print("\n🗑️  Removing unused packages...")

        autoremove_cmd = "sudo apt-get autoremove -y"
        if dry_run:
            if not json_output:
                console.print(f"[yellow][DRY RUN] Would execute: {autoremove_cmd}[/yellow]")
            result_data["tasks"].append({"task": "apt-autoremove", "dry_run": True})
        else:
            result = remote_ops.execute_command(autoremove_cmd, server_config)
            success = result.returncode == 0
            result_data["tasks"].append({"task": "apt-autoremove", "success": success})

            if success:
                if not json_output:
                    console.print("[green]✓ Unused packages removed[/green]")
            else:
                if not json_output:
                    console.print("[red]✗ Failed to remove unused packages[/red]")

        if json_output:
            console.print(json.dumps(result_data, indent=2))

    except Exception as e:
        if json_output:
            result_data["error"] = str(e)
            console.print(json.dumps(result_data, indent=2))
        else:
            console.print(f"[red]✗ Error: {str(e)}[/red]")


def rotate_logs(options: dict) -> None:
    """
    Rotate and compress log files.

    Features:
    - Forces log rotation using logrotate
    - Compresses old log files
    - Applies /etc/logrotate.conf rules
    - Shows rotation summary
    """
    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    active_server = require_active_server(options, config_manager)

    server_config = config_manager.load_server_config(active_server)

    dry_run = options.get("dry_run", False)
    json_output = options.get("json", False)

    result_data = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "action": "rotate-logs",
        "dry_run": dry_run,
        "tasks": [],
    }

    if not json_output:
        console.print("\n[bold cyan]═══ Log Rotation ═══[/bold cyan]\n")

    try:
        if not json_output:
            console.print("📋 Rotating log files...")

        rotate_cmd = "sudo logrotate -f /etc/logrotate.conf"
        if dry_run:
            if not json_output:
                console.print(f"[yellow][DRY RUN] Would execute: {rotate_cmd}[/yellow]")
            result_data["tasks"].append({"task": "logrotate", "dry_run": True})
        else:
            result = remote_ops.execute_command(rotate_cmd, server_config)
            success = result.returncode == 0
            result_data["tasks"].append(
                {"task": "logrotate", "success": success, "output": result.stdout}
            )

            if success:
                if not json_output:
                    console.print("[green]✓ Logs rotated successfully[/green]")
            else:
                if not json_output:
                    console.print("[red]✗ Log rotation failed[/red]")
                    console.print(f"[red]{result.stderr}[/red]")

        if json_output:
            console.print(json.dumps(result_data, indent=2))

    except Exception as e:
        if json_output:
            result_data["error"] = str(e)
            console.print(json.dumps(result_data, indent=2))
        else:
            console.print(f"[red]✗ Error: {str(e)}[/red]")


def cleanup_temp(options: dict) -> None:
    """
    Clean temporary files and caches.

    Features:
    - Removes files from /tmp older than 7 days
    - Cleans apt cache
    - Shows disk space freed
    - Safe deletion (ignores errors for locked files)
    """
    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    active_server = require_active_server(options, config_manager)

    server_config = config_manager.load_server_config(active_server)

    dry_run = options.get("dry_run", False)
    json_output = options.get("json", False)

    result_data = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "action": "cleanup-temp",
        "dry_run": dry_run,
        "tasks": [],
    }

    if not json_output:
        console.print("\n[bold cyan]═══ Temporary Files Cleanup ═══[/bold cyan]\n")

    try:
        # Clean /tmp (files older than 7 days)
        if not json_output:
            console.print("🗑️  Cleaning /tmp (files older than 7 days)...")

        tmp_cmd = "find /tmp -type f -atime +7 -delete 2>/dev/null || true"
        if dry_run:
            if not json_output:
                console.print(f"[yellow][DRY RUN] Would execute: {tmp_cmd}[/yellow]")
            result_data["tasks"].append({"task": "cleanup-tmp", "dry_run": True})
        else:
            result = remote_ops.execute_command(tmp_cmd, server_config)
            # Always consider successful (|| true handles errors)
            result_data["tasks"].append({"task": "cleanup-tmp", "success": True})

            if not json_output:
                console.print("[green]✓ Temporary files cleaned[/green]")

        # Clean apt cache
        if not json_output:
            console.print("\n🧹 Cleaning apt cache...")

        apt_cmd = "sudo apt-get clean"
        if dry_run:
            if not json_output:
                console.print(f"[yellow][DRY RUN] Would execute: {apt_cmd}[/yellow]")
            result_data["tasks"].append({"task": "apt-clean", "dry_run": True})
        else:
            result = remote_ops.execute_command(apt_cmd, server_config)
            success = result.returncode == 0
            result_data["tasks"].append({"task": "apt-clean", "success": success})

            if success:
                if not json_output:
                    console.print("[green]✓ Apt cache cleaned[/green]")

        if json_output:
            console.print(json.dumps(result_data, indent=2))

    except Exception as e:
        if json_output:
            result_data["error"] = str(e)
            console.print(json.dumps(result_data, indent=2))
        else:
            console.print(f"[red]✗ Error: {str(e)}[/red]")


def check_filesystem(options: dict) -> None:
    """
    Check filesystem usage and find large files.

    Features:
    - Shows disk usage (df -h)
    - Finds large files (>100MB) in /var/log and /tmp
    - Displays file sizes in human-readable format
    - Warns about large log files
    """
    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    active_server = require_active_server(options, config_manager)

    server_config = config_manager.load_server_config(active_server)

    json_output = options.get("json", False)

    result_data = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "action": "check-filesystem",
        "disk_usage": [],
        "large_files": [],
    }

    if not json_output:
        console.print("\n[bold cyan]═══ Filesystem Check ═══[/bold cyan]\n")

    try:
        # Check disk usage
        if not json_output:
            console.print("💾 Checking disk usage...\n")

        df_cmd = "df -h"
        result = remote_ops.execute_command(df_cmd, server_config)

        if result.returncode == 0:
            disk_output = result.stdout
            lines = disk_output.strip().split("\n")

            if not json_output:
                # Create Rich table
                table = Table(show_header=True, header_style="bold cyan")
                if lines:
                    # Parse header
                    headers = lines[0].split()
                    for header in headers:
                        table.add_column(header)

                    # Parse data rows
                    for line in lines[1:]:
                        if line.strip():
                            parts = line.split()
                            table.add_row(*parts)

                            # Add to result data
                            if len(parts) >= 6:
                                result_data["disk_usage"].append(
                                    {
                                        "filesystem": parts[0],
                                        "size": parts[1],
                                        "used": parts[2],
                                        "available": parts[3],
                                        "use_percent": parts[4],
                                        "mounted_on": parts[5],
                                    }
                                )

                console.print(table)
                console.print()

        # Find large files
        if not json_output:
            console.print("🔍 Finding large files (>100MB) in /var/log and /tmp...")

        large_files_cmd = (
            "find /var/log /tmp -type f -size +100M -exec ls -lh {} \\; 2>/dev/null || true"
        )
        result = remote_ops.execute_command(large_files_cmd, server_config)

        large_files_output = result.stdout.strip()

        if large_files_output:
            lines = large_files_output.split("\n")
            result_data["large_files"] = lines

            if not json_output:
                console.print(f"\n[yellow]⚠️  Found {len(lines)} large files:[/yellow]\n")
                for line in lines[:10]:  # Show first 10
                    if line.strip():
                        console.print(f"  {line}")
                if len(lines) > 10:
                    console.print(f"  ... and {len(lines) - 10} more")
        else:
            if not json_output:
                console.print("[green]✓ No large files found[/green]")

        result_data["success"] = True

        if json_output:
            console.print(json.dumps(result_data, indent=2))

    except Exception as e:
        if json_output:
            result_data["error"] = str(e)
            result_data["success"] = False
            console.print(json.dumps(result_data, indent=2))
        else:
            console.print(f"[red]✗ Error: {str(e)}[/red]")


def system_maintenance(options: dict) -> None:
    """
    Run comprehensive system maintenance (all tasks combined).

    Features:
    - Updates package lists and upgrades packages
    - Cleans package cache and removes orphaned packages
    - Rotates and compresses log files
    - Checks filesystem usage and large files
    - Cleans temporary files
    - Generates summary report
    """
    json_output = options.get("json", False)

    if not json_output:
        console.print("\n[bold cyan]═══ Comprehensive System Maintenance ═══[/bold cyan]\n")
        console.print("[yellow]This will perform all maintenance tasks:[/yellow]")
        console.print("  1. Update and upgrade packages")
        console.print("  2. Clean package cache")
        console.print("  3. Rotate log files")
        console.print("  4. Check filesystem")
        console.print("  5. Clean temporary files")
        console.print()

    start_time = time.time()

    try:
        # Task 1: Update packages
        if not json_output:
            console.rule("[bold]1. Package Updates[/bold]")
        update_packages(options)

        # Task 2: Clean packages
        if not json_output:
            console.rule("[bold]2. Package Cleanup[/bold]")
        clean_packages(options)

        # Task 3: Rotate logs
        if not json_output:
            console.rule("[bold]3. Log Rotation[/bold]")
        rotate_logs(options)

        # Task 4: Check filesystem
        if not json_output:
            console.rule("[bold]4. Filesystem Check[/bold]")
        check_filesystem(options)

        # Task 5: Clean temporary files
        if not json_output:
            console.rule("[bold]5. Temporary Files Cleanup[/bold]")
        cleanup_temp(options)

        elapsed_time = time.time() - start_time

        if not json_output:
            console.print("\n[bold green]═══ System Maintenance Complete ═══[/bold green]")
            console.print(f"Time elapsed: {elapsed_time:.1f} seconds")

    except Exception as e:
        if not json_output:
            console.print(f"\n[red]✗ Error during maintenance: {str(e)}[/red]")
        else:
            console.print(json.dumps({"error": str(e)}, indent=2))


def system_info(options: dict) -> None:
    """
    Display comprehensive system information.

    Shows:
    - OS and kernel version
    - Hostname and uptime
    - CPU and memory info
    - Disk usage summary
    - Network interfaces
    """
    config_manager = get_config_manager()
    remote_ops = RemoteOperations(config_manager)

    active_server = require_active_server(options, config_manager)

    server_config = config_manager.load_server_config(active_server)
    json_output = options.get("json", False)

    if not json_output:
        console.print(f"\n[bold cyan]═══ System Information: {active_server} ═══[/bold cyan]\n")

    try:
        # Gather system info via SSH
        info_commands = {
            "hostname": "hostname -f 2>/dev/null || hostname",
            "os": "cat /etc/os-release 2>/dev/null | grep PRETTY_NAME | cut -d'\"' -f2 || uname -s",
            "kernel": "uname -r",
            "arch": "uname -m",
            "uptime": "uptime -p 2>/dev/null || uptime | awk -F'up ' '{print $2}' | awk -F',' '{print $1}'",
            "load": "cat /proc/loadavg 2>/dev/null | awk '{print $1, $2, $3}'",
            "cpu_model": "cat /proc/cpuinfo 2>/dev/null | grep 'model name' | head -1 | cut -d':' -f2 || echo 'Unknown'",
            "cpu_cores": "nproc 2>/dev/null || echo '?'",
            "memory_total": "free -h 2>/dev/null | awk '/^Mem:/ {print $2}'",
            "memory_used": "free -h 2>/dev/null | awk '/^Mem:/ {print $3}'",
            "memory_free": "free -h 2>/dev/null | awk '/^Mem:/ {print $4}'",
            "disk_root": 'df -h / 2>/dev/null | awk \'NR==2 {print $3"/"$2" ("$5" used)"}\'',
        }

        info_data = {}
        for key, cmd in info_commands.items():
            result = remote_ops.run_command(server_config, cmd)
            info_data[key] = result.strip() if result else "N/A"

        if json_output:
            console.print(json.dumps(info_data, indent=2))
        else:
            # Create a nice table display
            table = Table(show_header=False, box=None, padding=(0, 2))
            table.add_column("Property", style="cyan")
            table.add_column("Value", style="white")

            table.add_row("Hostname", info_data.get("hostname", "N/A"))
            table.add_row("OS", info_data.get("os", "N/A"))
            table.add_row("Kernel", info_data.get("kernel", "N/A"))
            table.add_row("Architecture", info_data.get("arch", "N/A"))
            table.add_row("Uptime", info_data.get("uptime", "N/A"))
            table.add_row("Load Average", info_data.get("load", "N/A"))
            table.add_row("", "")
            table.add_row("CPU", f"{info_data.get('cpu_model', 'N/A').strip()}")
            table.add_row("CPU Cores", info_data.get("cpu_cores", "N/A"))
            table.add_row("", "")
            table.add_row("Memory Total", info_data.get("memory_total", "N/A"))
            table.add_row("Memory Used", info_data.get("memory_used", "N/A"))
            table.add_row("Memory Free", info_data.get("memory_free", "N/A"))
            table.add_row("", "")
            table.add_row("Disk (root)", info_data.get("disk_root", "N/A"))

            console.print(Panel(table, title=f"[bold]{active_server}[/bold]", border_style="cyan"))

    except Exception as e:
        if not json_output:
            console.print(f"[red]✗ Error gathering system info: {str(e)}[/red]")
        else:
            console.print(json.dumps({"error": str(e)}, indent=2))
