"""
Security management commands for Navig.

This module provides comprehensive security management including:
- UFW firewall management (status, add/remove rules, enable/disable)
- Fail2Ban monitoring (status, banned IPs, unban)
- SSH security auditing
- Security updates checking
- Connection auditing
- Comprehensive security scans
- Host-secret migration (plaintext host YAML passwords → the vault)

Migrated from: security-manager.ps1 (400 lines)
"""

import ipaddress
import json
import re

import typer
from rich.progress import Progress
from rich.table import Table

from navig.cli.recovery import require_active_server
from navig.config import get_config_manager
from navig.console_helper import get_console
from navig.remote import RemoteOperations

console = get_console()


def _validate_firewall_rule_params(port, protocol):
    """
    Validate and sanitize firewall rule parameters.
    Ensures:
    - port is an integer between 1 and 65535
    - protocol is 'tcp' or 'udp' (case-insensitive)
    Returns:
        Tuple (port_str, protocol_str) if valid, otherwise (None, None).
    """
    try:
        port_int = int(str(port))
    except (TypeError, ValueError):
        console.print("[red]✗[/red] Invalid port or protocol")
        return None, None
    if not (1 <= port_int <= 65535):
        console.print("[red]✗[/red] Invalid port or protocol")
        return None, None
    protocol_str = str(protocol).lower()
    if protocol_str not in {"tcp", "udp"}:
        console.print("[red]✗[/red] Invalid port or protocol")
        return None, None
    return str(port_int), protocol_str


def firewall_status(options):
    """
    Display UFW firewall status and rules.

    Shows:
    - Firewall status (active/inactive)
    - Default policies
    - All configured rules
    - Logging level

    Args:
        options: Global options (dry_run, json_output, etc.)
    """
    config_manager = get_config_manager()
    server_name = require_active_server(options, config_manager)

    server_config = config_manager.load_server_config(server_name)
    remote_ops = RemoteOperations(config_manager)

    if options.get("dry_run"):
        console.print("[yellow]DRY RUN:[/yellow] Would check firewall status")
        return

    # The header is for humans; under --json it corrupts the document a script parses.
    if not options.get("json_output"):
        console.print(f"\n[cyan]═══ Firewall Status - {server_name} ═══[/cyan]\n")

    # Get UFW status
    result = remote_ops.execute_command("sudo ufw status verbose", server_config)

    if result.returncode != 0:
        console.print(
            f"[red]✗[/red] Failed to get firewall status: {(result.stderr or 'Unknown error')}"
        )
        return

    output = (result.stdout or "")

    if options.get("json_output"):
        # Parse UFW output for JSON
        lines = output.strip().split("\n")
        status = "inactive"
        rules = []

        for line in lines:
            if "Status:" in line:
                status = "active" if "active" in line.lower() else "inactive"
            elif " ALLOW " in line or " DENY " in line:
                rules.append(line.strip())

        json_data = {"server": server_name, "status": status, "rules": rules}
        console.print(json.dumps(json_data, indent=2))
    else:
        # Display with Rich formatting
        console.print(output)

        # Count rules
        rule_count = len([line for line in output.split("\n") if "ALLOW" in line or "DENY" in line])
        console.print(f"\n[dim]Total rules: {rule_count}[/dim]")


def firewall_add_rule(port, protocol, allow_from, options):
    """
    Add a new UFW firewall rule.

    Args:
        port: Port number to allow
        protocol: Protocol (tcp/udp)
        allow_from: IP address or subnet (default: any)
        options: Global options
    """
    config_manager = get_config_manager()
    server_name = require_active_server(options, config_manager)

    # Multi-agent safety: claim the host before mutating it (navig.core.host_lock).
    from navig.core import host_lock  # noqa: PLC0415

    host_lock.guard_remote(config_manager, server_name, "navig security firewall add-rule")

    server_config = config_manager.load_server_config(server_name)
    remote_ops = RemoteOperations(config_manager)

    port_str, protocol_str = _validate_firewall_rule_params(port, protocol)
    if port_str is None:
        return

    # Build UFW command
    allow_from_str = str(allow_from).strip()
    if allow_from_str == "any":
        command = f"sudo ufw allow {port_str}/{protocol_str}"
        rule_desc = f"{port_str}/{protocol_str} from any"
    else:
        # Strictly validate allow_from as an IP address or network (IPv4/IPv6)
        try:
            # Try network first (supports CIDR as well as single IP when strict=False)
            ipaddress.ip_network(allow_from_str, strict=False)
        except ValueError:
            try:
                ipaddress.ip_address(allow_from_str)
            except ValueError:
                console.print("[red]✗[/red] Invalid allow_from address")
                return
        command = (
            f"sudo ufw allow from {allow_from_str} to any port {port_str} proto {protocol_str}"
        )
        rule_desc = f"{port_str}/{protocol_str} from {allow_from_str}"

    if options.get("dry_run"):
        console.print(f"[yellow]DRY RUN:[/yellow] Would add firewall rule: {rule_desc}")
        console.print(f"[dim]Command: {command}[/dim]")
        return

    console.print(f"\n[cyan]Adding firewall rule:[/cyan] {rule_desc}")

    result = remote_ops.execute_command(command, server_config)

    if result.returncode == 0:
        console.print("[green]✓[/green] Firewall rule added successfully")
        if (result.stdout or ""):
            console.print(f"[dim]{result.stdout or ''}[/dim]")
    else:
        console.print(
            f"[red]✗[/red] Failed to add firewall rule: {(result.stderr or 'Unknown error')}"
        )


def firewall_remove_rule(port, protocol, options):
    """
    Remove a UFW firewall rule.

    Args:
        port: Port number
        protocol: Protocol (tcp/udp)
        options: Global options
    """
    config_manager = get_config_manager()
    server_name = require_active_server(options, config_manager)

    # Multi-agent safety: claim the host before mutating it (navig.core.host_lock).
    from navig.core import host_lock  # noqa: PLC0415

    host_lock.guard_remote(config_manager, server_name, "navig security firewall remove-rule")

    server_config = config_manager.load_server_config(server_name)
    remote_ops = RemoteOperations(config_manager)

    port_str, protocol_str = _validate_firewall_rule_params(port, protocol)
    if port_str is None:
        return

    command = f"sudo ufw delete allow {port_str}/{protocol_str}"
    rule_desc = f"{port_str}/{protocol_str}"

    if options.get("dry_run"):
        console.print(f"[yellow]DRY RUN:[/yellow] Would remove firewall rule: {rule_desc}")
        console.print(f"[dim]Command: {command}[/dim]")
        return

    console.print(f"\n[cyan]Removing firewall rule:[/cyan] {rule_desc}")

    result = remote_ops.execute_command(command, server_config)

    if result.returncode == 0:
        console.print("[green]✓[/green] Firewall rule removed successfully")
        if (result.stdout or ""):
            console.print(f"[dim]{result.stdout or ''}[/dim]")
    else:
        console.print(
            f"[red]✗[/red] Failed to remove firewall rule: {(result.stderr or 'Unknown error')}"
        )


def firewall_enable(options):
    """
    Enable UFW firewall.

    Args:
        options: Global options
    """
    config_manager = get_config_manager()
    server_name = require_active_server(options, config_manager)

    # Multi-agent safety: claim the host before mutating it (navig.core.host_lock).
    from navig.core import host_lock  # noqa: PLC0415

    host_lock.guard_remote(config_manager, server_name, "navig security firewall enable")

    server_config = config_manager.load_server_config(server_name)
    remote_ops = RemoteOperations(config_manager)

    if options.get("dry_run"):
        console.print("[yellow]DRY RUN:[/yellow] Would enable UFW firewall")
        return

    console.print("\n[cyan]Enabling UFW firewall...[/cyan]")

    # Use --force to avoid interactive prompt
    result = remote_ops.execute_command("sudo ufw --force enable", server_config)

    if result.returncode == 0:
        console.print("[green]✓[/green] Firewall enabled successfully")
        console.print("[yellow]⚠[/yellow] Make sure SSH (port 22) is allowed to avoid lockout")
    else:
        console.print(
            f"[red]✗[/red] Failed to enable firewall: {(result.stderr or 'Unknown error')}"
        )


def firewall_disable(options):
    """
    Disable UFW firewall.

    Args:
        options: Global options
    """
    config_manager = get_config_manager()
    server_name = require_active_server(options, config_manager)

    # Multi-agent safety: claim the host before mutating it (navig.core.host_lock).
    from navig.core import host_lock  # noqa: PLC0415

    host_lock.guard_remote(config_manager, server_name, "navig security firewall disable")

    server_config = config_manager.load_server_config(server_name)
    remote_ops = RemoteOperations(config_manager)

    if options.get("dry_run"):
        console.print("[yellow]DRY RUN:[/yellow] Would disable UFW firewall")
        return

    console.print("\n[cyan]Disabling UFW firewall...[/cyan]")

    result = remote_ops.execute_command("sudo ufw disable", server_config)

    if result.returncode == 0:
        console.print("[green]✓[/green] Firewall disabled successfully")
        console.print("[yellow]⚠[/yellow] Server is now unprotected by firewall")
    else:
        console.print(
            f"[red]✗[/red] Failed to disable firewall: {(result.stderr or 'Unknown error')}"
        )


def fail2ban_status(options):
    """
    Display Fail2Ban status and banned IPs.

    Shows:
    - Fail2Ban service status
    - Active jails
    - Banned IP addresses per jail
    - Ban statistics

    Args:
        options: Global options
    """
    config_manager = get_config_manager()
    server_name = require_active_server(options, config_manager)

    server_config = config_manager.load_server_config(server_name)
    remote_ops = RemoteOperations(config_manager)

    if options.get("dry_run"):
        console.print("[yellow]DRY RUN:[/yellow] Would check Fail2Ban status")
        return

    console.print(f"\n[cyan]═══ Fail2Ban Status - {server_name} ═══[/cyan]\n")

    # Check if Fail2Ban is running
    service_result = remote_ops.execute_command(
        "systemctl is-active fail2ban 2>/dev/null", server_config
    )
    service_status = (
        (service_result.stdout or "").strip() if service_result.returncode == 0 else "inactive"
    )

    if service_status != "active":
        console.print("[red]✗[/red] Fail2Ban service is not running")
        console.print("[dim]Install with: sudo apt-get install fail2ban[/dim]")
        return

    console.print(f"[green]✓[/green] Fail2Ban service: {service_status}")

    # Get jail status
    jails_result = remote_ops.execute_command(
        "sudo fail2ban-client status 2>/dev/null", server_config
    )

    if jails_result.returncode != 0:
        console.print("[red]✗[/red] Failed to get Fail2Ban status")
        return

    # Parse jails
    jails_output = (jails_result.stdout or "")
    console.print(f"\n{jails_output}\n")

    # Extract jail names
    jail_match = re.search(r"Jail list:\s*(.+)", jails_output)
    if jail_match:
        jail_names = [j.strip() for j in jail_match.group(1).split(",")]

        # Create table for banned IPs
        table = Table(title="Banned IPs by Jail")
        table.add_column("Jail", style="cyan")
        table.add_column("Currently Banned", style="yellow")
        table.add_column("Total Banned", style="dim")

        for jail in jail_names:
            # Get jail-specific status
            jail_status = remote_ops.execute_command(
                f"sudo fail2ban-client status {jail} 2>/dev/null", server_config
            )

            if jail_status.returncode == 0:
                output = jail_status.stdout or ""

                # Parse banned IPs
                currently_banned = 0
                total_banned = 0

                current_match = re.search(r"Currently banned:\s*(\d+)", output)
                total_match = re.search(r"Total banned:\s*(\d+)", output)

                if current_match:
                    currently_banned = int(current_match.group(1))
                if total_match:
                    total_banned = int(total_match.group(1))

                # Color code based on banned count
                banned_color = "red" if currently_banned > 0 else "green"

                table.add_row(
                    jail,
                    f"[{banned_color}]{currently_banned}[/{banned_color}]",
                    str(total_banned),
                )

                # Show banned IPs if any
                if currently_banned > 0:
                    ips_match = re.search(r"Banned IP list:\s*(.+)", output)
                    if ips_match:
                        ips = ips_match.group(1).strip()
                        if ips:
                            console.print(f"[yellow]⚠[/yellow] {jail} banned IPs: {ips}")

        console.print(table)


def fail2ban_unban(ip_address, jail, options):
    """
    Unban an IP address from Fail2Ban.

    Args:
        ip_address: IP address to unban
        jail: Jail name (e.g., sshd, default: all jails)
        options: Global options
    """
    config_manager = get_config_manager()
    server_name = require_active_server(options, config_manager)

    # Multi-agent safety: claim the host before mutating it (navig.core.host_lock).
    from navig.core import host_lock  # noqa: PLC0415

    host_lock.guard_remote(config_manager, server_name, "navig security fail2ban unban")

    server_config = config_manager.load_server_config(server_name)
    remote_ops = RemoteOperations(config_manager)

    try:
        # Validate IP address (supports IPv4 and IPv6)
        ipaddress.ip_address(str(ip_address))
    except ValueError:
        console.print("[red]✗[/red] Invalid IP address")
        return

    if jail:
        if not re.match(r"^[a-zA-Z0-9_-]+$", str(jail)):
            console.print("[red]✗[/red] Invalid jail name")
            return
        command = f"sudo fail2ban-client set {jail} unbanip {ip_address}"
        target = f"{ip_address} from {jail}"
    else:
        command = f"sudo fail2ban-client unban {ip_address}"
        target = f"{ip_address} from all jails"

    if options.get("dry_run"):
        console.print(f"[yellow]DRY RUN:[/yellow] Would unban {target}")
        console.print(f"[dim]Command: {command}[/dim]")
        return

    console.print(f"\n[cyan]Unbanning:[/cyan] {target}")

    result = remote_ops.execute_command(command, server_config)

    if result.returncode == 0:
        console.print("[green]✓[/green] IP address unbanned successfully")
    else:
        console.print(f"[red]✗[/red] Failed to unban IP: {(result.stderr or 'Unknown error')}")


def ssh_audit(options):
    """
    Audit SSH configuration for security best practices.

    Checks:
    - PermitRootLogin (should be: prohibit-password or no)
    - PasswordAuthentication (should be: no)
    - PermitEmptyPasswords (should be: no)
    - X11Forwarding (should be: no)
    - MaxAuthTries (should be: 3 or less)

    Args:
        options: Global options
    """
    config_manager = get_config_manager()
    server_name = require_active_server(options, config_manager)

    server_config = config_manager.load_server_config(server_name)
    remote_ops = RemoteOperations(config_manager)

    if options.get("dry_run"):
        console.print("[yellow]DRY RUN:[/yellow] Would audit SSH configuration")
        return

    console.print(f"\n[cyan]═══ SSH Security Audit - {server_name} ═══[/cyan]\n")

    # Define security checks
    checks = [
        {
            "name": "PermitRootLogin",
            "pattern": "^PermitRootLogin",
            "recommended": ["prohibit-password", "no"],
        },
        {
            "name": "PasswordAuthentication",
            "pattern": "^PasswordAuthentication",
            "recommended": ["no"],
        },
        {
            "name": "PermitEmptyPasswords",
            "pattern": "^PermitEmptyPasswords",
            "recommended": ["no"],
        },
        {"name": "X11Forwarding", "pattern": "^X11Forwarding", "recommended": ["no"]},
        {
            "name": "MaxAuthTries",
            "pattern": "^MaxAuthTries",
            "recommended": ["3", "2", "1"],
        },
    ]

    # Create table
    table = Table(title="SSH Configuration Security Audit")
    table.add_column("Setting", style="cyan")
    table.add_column("Current Value", style="yellow")
    table.add_column("Recommended", style="green")
    table.add_column("Status", style="bold")

    issues_found = []

    with Progress() as progress:
        task = progress.add_task("[cyan]Checking SSH settings...", total=len(checks))

        for check in checks:
            result = remote_ops.execute_command(
                f"grep '{check['pattern']}' /etc/ssh/sshd_config | grep -v '^#' | tail -1",
                server_config,
            )

            current_value = "not set"
            if result.returncode == 0 and (result.stdout or "").strip():
                parts = (result.stdout or "").strip().split()
                if len(parts) >= 2:
                    current_value = parts[1]

            # Check if current value is recommended
            is_ok = current_value in check["recommended"]
            status = "✓ OK" if is_ok else "⚠ REVIEW"
            status_color = "green" if is_ok else "yellow"

            table.add_row(
                check["name"],
                current_value,
                " or ".join(check["recommended"]),
                f"[{status_color}]{status}[/{status_color}]",
            )

            if not is_ok:
                issues_found.append(
                    {
                        "setting": check["name"],
                        "current": current_value,
                        "recommended": check["recommended"],
                    }
                )

            progress.update(task, advance=1)

    console.print(table)

    # Summary
    if issues_found:
        console.print(f"\n[yellow]⚠[/yellow] Found {len(issues_found)} settings to review")
        console.print(
            "[dim]Edit /etc/ssh/sshd_config and restart SSH: sudo systemctl restart sshd[/dim]"
        )
    else:
        console.print("\n[green]✓[/green] All SSH security settings are configured correctly")


def check_security_updates(options):
    """
    Check for available security updates.

    Args:
        options: Global options
    """
    config_manager = get_config_manager()
    server_name = require_active_server(options, config_manager)

    # Multi-agent safety: claim the host before mutating it (navig.core.host_lock).
    from navig.core import host_lock  # noqa: PLC0415

    host_lock.guard_remote(config_manager, server_name, "navig security updates (apt-get update)")

    server_config = config_manager.load_server_config(server_name)
    remote_ops = RemoteOperations(config_manager)

    if options.get("dry_run"):
        console.print("[yellow]DRY RUN:[/yellow] Would check for security updates")
        return

    console.print(f"\n[cyan]═══ Security Updates - {server_name} ═══[/cyan]\n")

    with Progress() as progress:
        task = progress.add_task("[cyan]Updating package lists...", total=100)

        # Update package lists
        remote_ops.execute_command("sudo apt-get update -qq", server_config)
        progress.update(task, completed=50)

        # Check for security updates
        result = remote_ops.execute_command(
            "apt-get upgrade -s 2>/dev/null | grep -i security", server_config
        )
        progress.update(task, completed=100)

    if result.returncode == 0 and (result.stdout or "").strip():
        console.print("[yellow]⚠[/yellow] Security updates available:\n")
        console.print(result.stdout or "")

        # Count updates
        update_count = len((result.stdout or "").strip().split("\n"))
        console.print(f"\n[yellow]Total security updates: {update_count}[/yellow]")
        console.print("[dim]Install with: sudo apt-get upgrade[/dim]")
    else:
        console.print("[green]✓[/green] No security updates available")
        console.print("[dim]System is up to date[/dim]")


def audit_connections(options):
    """
    Audit active network connections and listening ports.

    Shows:
    - Established connections
    - Listening ports
    - Suspicious processes (netcat, etc.)

    Args:
        options: Global options
    """
    config_manager = get_config_manager()
    server_name = require_active_server(options, config_manager)

    server_config = config_manager.load_server_config(server_name)
    remote_ops = RemoteOperations(config_manager)

    if options.get("dry_run"):
        console.print("[yellow]DRY RUN:[/yellow] Would audit network connections")
        return

    console.print(f"\n[cyan]═══ Network Connection Audit - {server_name} ═══[/cyan]\n")

    # Established connections
    console.print("[bold]Established Connections:[/bold]")
    est_result = remote_ops.execute_command("ss -tunap 2>/dev/null | grep ESTAB", server_config)

    if est_result.returncode == 0 and (est_result.stdout or "").strip():
        lines = (est_result.stdout or "").strip().split("\n")
        console.print(f"[yellow]Found {len(lines)} established connections[/yellow]\n")

        # Show first 10
        for line in lines[:10]:
            console.print(f"[dim]{line}[/dim]")

        if len(lines) > 10:
            console.print(f"\n[dim]... and {len(lines) - 10} more[/dim]")
    else:
        console.print("[green]No established connections[/green]")

    # Listening ports
    console.print("\n[bold]Listening Ports:[/bold]")
    listen_result = remote_ops.execute_command("ss -tuln 2>/dev/null | grep LISTEN", server_config)

    if listen_result.returncode == 0 and (listen_result.stdout or "").strip():
        lines = (listen_result.stdout or "").strip().split("\n")
        console.print(f"[yellow]Found {len(lines)} listening ports[/yellow]\n")

        for line in lines:
            console.print(f"[dim]{line}[/dim]")
    else:
        console.print("[green]No listening ports found[/green]")

    # Check for suspicious processes
    console.print("\n[bold]Suspicious Process Check:[/bold]")
    susp_result = remote_ops.execute_command(
        "ps aux 2>/dev/null | grep -E 'nc|ncat|netcat' | grep -v grep", server_config
    )

    if susp_result.returncode == 0 and (susp_result.stdout or "").strip():
        console.print("[red]⚠[/red] Suspicious processes found:\n")
        console.print(susp_result.stdout or "")
    else:
        console.print("[green]✓[/green] No suspicious processes detected")


def security_scan(options):
    """
    Run comprehensive security scan.

    Executes all security checks:
    - Firewall status
    - Fail2Ban status
    - SSH audit
    - Security updates
    - Connection audit

    Args:
        options: Global options
    """
    config_manager = get_config_manager()
    server_name = require_active_server(options, config_manager)

    if options.get("dry_run"):
        console.print("[yellow]DRY RUN:[/yellow] Would run comprehensive security scan")
        return

    console.print("\n[bold cyan]═══ COMPREHENSIVE SECURITY SCAN ═══[/bold cyan]")
    console.print(f"[dim]Server: {server_name}[/dim]\n")

    # Run all checks
    firewall_status(options)
    fail2ban_status(options)
    ssh_audit(options)
    check_security_updates(options)
    audit_connections(options)

    console.print("\n[bold cyan]═══ SECURITY SCAN COMPLETE ═══[/bold cyan]")


def config_audit(options):
    """
    Audit local NAVIG configuration for security issues.

    Based on advanced security audit patterns. Checks:
    - Hardcoded credentials in config files
    - File permissions (SSH keys, config files)
    - Environment variable usage
    - Database connection security

    This runs locally - no remote server required.

    Args:
        options: Global options (json_output, verbose, etc.)
    """

    console.print("\n[bold cyan]═══ NAVIG Configuration Security Audit ═══[/bold cyan]\n")

    config_manager = get_config_manager()

    try:
        from navig.core.security import (
            check_config_security,  # noqa: F401
            check_file_permissions,  # noqa: F401
            run_security_audit,
        )
    except ImportError:
        console.print("[red]✗[/red] Security module not available")
        console.print("[dim]Install with: pip install navig[security][/dim]")
        return

    # Load global config
    global_config = config_manager.get_global_config()

    # Run audit
    report = run_security_audit(config=global_config, config_dir=config_manager.base_dir)

    if options.get("json_output"):
        console.print(json.dumps(report, indent=2))
        return

    # Display findings
    findings = report.get("findings", [])
    summary = report.get("summary", {})

    if not findings:
        console.print("[green]✓[/green] No security issues found!")
        console.print(f"[dim]Checked: {config_manager.base_dir}[/dim]")
        return

    # Create table
    table = Table(title="Security Findings")
    table.add_column("Severity", style="bold")
    table.add_column("Issue", style="cyan")
    table.add_column("Details")
    table.add_column("Fix", style="green")

    severity_colors = {
        "critical": "red",
        "warn": "yellow",
        "info": "blue",
    }

    for finding in findings:
        severity = finding.get("severity", "info")
        color = severity_colors.get(severity, "white")

        table.add_row(
            f"[{color}]{severity.upper()}[/{color}]",
            finding.get("title", "Unknown"),
            finding.get("detail", ""),
            finding.get("remediation", "-"),
        )

    console.print(table)

    # Summary
    console.print("\n[bold]Summary:[/bold]")
    console.print(f"  Critical: [red]{summary.get('critical', 0)}[/red]")
    console.print(f"  Warnings: [yellow]{summary.get('warn', 0)}[/yellow]")
    console.print(f"  Info: [blue]{summary.get('info', 0)}[/blue]")

    if report.get("passed"):
        console.print("\n[green]✓[/green] Audit passed (no critical issues)")
    else:
        console.print("\n[red]✗[/red] Audit failed - critical issues found")
        console.print("[dim]Fix critical issues before deploying to production[/dim]")


def check_secrets(options):
    """
    Scan configuration files for accidentally committed secrets.

    Checks for:
    - API keys (OpenAI, Anthropic, GitHub, etc.)
    - Passwords and tokens
    - Private keys
    - Connection strings with credentials

    Args:
        options: Global options
    """

    console.print("\n[bold cyan]═══ Secret Detection Scan ═══[/bold cyan]\n")

    config_manager = get_config_manager()

    try:
        from navig.core.security import (  # noqa: F401
            DEFAULT_REDACT_PATTERNS,
            redact_sensitive_text,
        )
    except ImportError:
        console.print("[red]✗[/red] Security module not available")
        return

    # Files to scan
    config_files = []

    # Global config
    if config_manager.config_file.exists():
        config_files.append(config_manager.config_file)

    # Host configs
    if config_manager.hosts_dir.exists():
        config_files.extend(config_manager.hosts_dir.glob("*.yaml"))
        config_files.extend(config_manager.hosts_dir.glob("*.yml"))

    # App configs (legacy)
    if config_manager.apps_dir.exists():
        config_files.extend(config_manager.apps_dir.glob("*.yaml"))
        config_files.extend(config_manager.apps_dir.glob("*.yml"))

    if not config_files:
        console.print("[yellow]⚠[/yellow] No configuration files found to scan")
        return

    console.print(f"[dim]Scanning {len(config_files)} configuration files...[/dim]\n")

    secrets_found = []

    with Progress() as progress:
        task = progress.add_task("[cyan]Scanning...", total=len(config_files))

        for config_file in config_files:
            try:
                content = config_file.read_text(errors="replace", encoding="utf-8")

                # Check if redaction changes the content (indicates secrets present)
                redacted = redact_sensitive_text(content)

                if redacted != content:
                    # Count how many patterns matched
                    match_count = (
                        content.count("sk-")
                        + content.count("ghp_")
                        + content.count("password:")
                        + content.count("token:")
                    )

                    secrets_found.append(
                        {
                            "file": str(config_file.relative_to(config_manager.base_dir)),
                            "matches": match_count,
                        }
                    )
            except Exception as e:
                if options.get("verbose"):
                    console.print(f"[dim]Error reading {config_file}: {e}[/dim]")

            progress.update(task, advance=1)

    if not secrets_found:
        console.print("[green]✓[/green] No secrets detected in configuration files")
        console.print("[dim]Your configs appear to use environment variables correctly[/dim]")
        return

    # Display findings
    table = Table(title="Potential Secrets Detected")
    table.add_column("File", style="cyan")
    table.add_column("Matches", style="yellow")
    table.add_column("Action", style="red")

    for finding in secrets_found:
        table.add_row(finding["file"], str(finding["matches"]), "Move to env vars")

    console.print(table)

    console.print(f"\n[red]⚠[/red] Found potential secrets in {len(secrets_found)} files")
    console.print("\n[bold]Recommendations:[/bold]")
    console.print("  1. Use environment variables: api_key: ${OPENROUTER_API_KEY}")
    console.print("  2. Add to .gitignore if not already")
    console.print("  3. Rotate any exposed credentials immediately")


# ============================================================================
# HOST SECRET MIGRATION — plaintext host YAML passwords → the vault (Gate 0F)
# ============================================================================


def _print_secret_rows(title: str, rows: list[dict]) -> None:
    """Render the migration table. Rows carry value LENGTHS, never values."""
    table = Table(title=title)
    table.add_column("Host", style="cyan", no_wrap=True)
    table.add_column("Key", style="magenta", no_wrap=True)
    table.add_column("Source file", style="dim")
    table.add_column("Target ref", style="green")
    table.add_column("Value length", justify="right", no_wrap=True)
    for row in rows:
        table.add_row(
            str(row["host"]),
            str(row["key"]),
            str(row["file"]),
            str(row["ref"]),
            str(row["length"]),
        )
    console.print(table)


def migrate_host_secrets(
    options: dict | None = None,
    *,
    apply: bool = False,
    rollback: bool = False,
    purge_backups: bool = False,
) -> int:
    """Move plaintext host secrets (`ssh_password`, `database.*password`) to the vault.

    Default is a DRY RUN: a table of Host | Key | Source file | Target ref |
    Value length — the value itself is never printed. Exit 0.

    ``--apply`` vaults each secret, writes ``<host>.yaml.bak.<timestamp>``
    beside the file, then atomically rewrites the YAML with ``*_ref`` only.
    ``--rollback`` restores the newest ``*.yaml.bak.*`` per host and refuses
    (exit 1) when no backup exists.
    ``--purge-backups`` deletes those backups — the last plaintext copies — but only
    for hosts whose refs decrypt from the vault right now; any other backup is kept
    and the run exits 1.

    Returns the process exit code (0 ok · 1 refusal/failure · 2 bad usage).
    """
    options = options or {}
    from navig.core.host_secrets import (  # noqa: PLC0415 — lazy, keeps startup fast
        HostSecretError,
        find_host_backups,
        migrate_host_file,
        purge_host_backups,
        restore_host_backup,
        scan_plaintext_host_secrets,
    )

    if sum((apply, rollback, purge_backups)) > 1:
        console.print(
            "[red]✗[/red] --apply, --rollback and --purge-backups are mutually exclusive"
        )
        return 2

    config_manager = get_config_manager()
    # Hosts load from EVERY config directory the loader merges — app-local + global,
    # new `hosts/` + legacy `apps/` (HostManager.list_hosts). Scanning only the
    # active scope's hosts_dir makes a run from inside a space report "nothing to
    # migrate" while the global dir still holds plaintext. Scope the scan AND the
    # rollback to the loader's real surface.
    scope_dirs: list = []
    for cd in config_manager.get_config_directories():
        for sub in ("hosts", "apps"):
            d = cd / sub
            if d not in scope_dirs:
                scope_dirs.append(d)
    if not any(d.is_dir() for d in scope_dirs):
        scope_dirs = [config_manager.hosts_dir]

    if purge_backups:
        purged: list = []
        kept: list[tuple] = []
        for d in scope_dirs:
            p, k = purge_host_backups(d)
            purged.extend(p)
            kept.extend(k)
        if not purged and not kept:
            console.print("[green]✓[/green] No host-secret backups (*.yaml.bak.*) to purge.")
            return 0
        table = Table(title="Host-secret backups")
        table.add_column("Backup", style="cyan", no_wrap=True)
        table.add_column("Result")
        for path in purged:
            table.add_row(path.name, "[green]● deleted[/green]")
        for path, reason in kept:
            table.add_row(path.name, f"[yellow]○ kept — {reason}[/yellow]")
        console.print(table)
        if kept:
            console.print(
                f"[yellow]⚠[/yellow] {len(kept)} backup(s) kept: their host does not resolve "
                "from the vault, so the backup is still the only usable copy. Fix the vault "
                "(`navig vault list`) or roll back, then purge again."
            )
            return 1
        console.print(
            f"[green]✓[/green] {len(purged)} plaintext backup(s) deleted. Rollback is no "
            "longer possible; the vault holds the only copy — keep a vault backup."
        )
        return 0

    if rollback:
        found: list[tuple] = []  # (scope_dir, host_file, backup_path)
        for d in scope_dirs:
            for host_file, backup in sorted(find_host_backups(d).items()):
                found.append((d, host_file, backup))
        if not found:
            console.print(
                "[red]✗[/red] No host-secret backups (*.yaml.bak.*) under "
                + ", ".join(str(d) for d in scope_dirs)
                + " — nothing to roll back"
            )
            return 1
        table = Table(title="Restored host-secret backups")
        table.add_column("Host file", style="cyan", no_wrap=True)
        table.add_column("Restored from", style="dim")
        for d, host_file, backup in found:
            restore_host_backup(d, host_file, backup)
            table.add_row(str(d / host_file), str(backup))
        console.print(table)
        console.print(
            "[green]✓[/green] Rollback complete — plaintext is back in the YAML files "
            "(the vaulted copies are left in place, harmlessly)."
        )
        return 0

    rows: list[dict] = []
    for d in scope_dirs:
        rows.extend(scan_plaintext_host_secrets(d))
    rows.sort(key=lambda r: (str(r["file"]), r["host"], r["key"]))
    if not rows:
        console.print("[green]✓[/green] No plaintext host secrets found — nothing to migrate.")
        return 0

    if not apply:
        _print_secret_rows(
            "Plaintext host secrets — DRY RUN (nothing written)", rows
        )
        console.print(
            f"[dim]{len(rows)} secret(s) across {len({r['host'] for r in rows})} host(s) "
            "would move to the vault. Run "
            "`navig security migrate-host-secrets --apply` to migrate them.[/dim]"
        )
        return 0

    done: list[dict] = []
    for path in sorted({r["file"] for r in rows}):
        try:
            done.extend(migrate_host_file(path))
        except HostSecretError as exc:
            console.print(f"[red]✗[/red] {exc}")
            console.print(
                f"[dim]{len(done)} secret(s) migrated before the failure; "
                f"{path.name} was left unchanged.[/dim]"
            )
            return 1
        except OSError as exc:
            console.print(f"[red]✗[/red] Cannot rewrite {path.name}: {type(exc).__name__}")
            return 1
    _print_secret_rows(f"Migrated to the vault ({len(done)} secret(s))", done)
    console.print(
        "[green]✓[/green] Host YAML now stores *_ref only; each original file kept a "
        "*.yaml.bak.<timestamp> backup. Roll back with "
        "`navig security migrate-host-secrets --rollback`."
    )
    console.print(
        "[dim]Those backups hold the plaintext. Once `navig host test <name>` passes, "
        "delete them with `navig security migrate-host-secrets --purge-backups`.[/dim]"
    )
    return 0


# `navig security …` — the top-level group. Registered lazily through
# navig.cli.registration._EXTERNAL_CMD_MAP; `navig host security …` (host.py)
# stays the remote-audit surface it always was.
security_app = typer.Typer(
    name="security",
    help="Security management — host-secret migration and local audits.",
    invoke_without_command=True,
    no_args_is_help=True,
)


@security_app.callback()
def security_callback(ctx: typer.Context):
    """Local security operations — run without a subcommand for the help."""
    if ctx.invoked_subcommand is None:
        raise typer.Exit()


@security_app.command("migrate-host-secrets")
def security_migrate_host_secrets(
    apply: bool = typer.Option(
        False, "--apply", help="Vault the secrets and rewrite the host YAML (default: dry run)"
    ),
    rollback: bool = typer.Option(
        False, "--rollback", help="Restore the newest *.yaml.bak.* per host"
    ),
    purge_backups: bool = typer.Option(
        False,
        "--purge-backups",
        help="Delete the plaintext *.yaml.bak.* backups of hosts that resolve from the vault",
    ),
):
    """Move plaintext host passwords into the vault (dry run unless --apply)."""
    code = migrate_host_secrets(
        {}, apply=apply, rollback=rollback, purge_backups=purge_backups
    )
    if code:
        raise typer.Exit(code)
