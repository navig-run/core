# NAVIG Local Machine Management Commands
"""
Commands for managing the local machine as a managed host.

Provides:
- hosts view/edit: View and edit the system hosts file
- software list: List installed software packages
- security audit: Run local security audit
- system info: Display local system information
"""

import typer

from navig.cli._callbacks import show_subcommand_help
from navig import console_helper as ch

# Lazy imports for heavy modules
_local_ops = None


def _get_local_ops():
    """Lazy import LocalOperations to avoid loading on startup."""
    global _local_ops
    if _local_ops is None:
        from navig.local_operations import LocalOperations

        _local_ops = LocalOperations()
    return _local_ops


def _ensure_rich():
    """Ensure rich module is available."""
    try:
        from rich.console import Console
        from rich.panel import Panel
        from rich.syntax import Syntax
        from rich.table import Table

        return Console(), Table, Panel, Syntax
    except ImportError:
        ch.error("Rich library required for this command")
        raise


# ==================== System Info ====================


def system_info(options: dict):
    """
    Display local system information.

    Shows OS type, hostname, admin status, and paths.

    Args:
        options: Command options (json_output, plain)
    """
    console, Table, Panel, _ = _ensure_rich()
    local_ops = _get_local_ops()

    info = local_ops.get_system_info()

    if options.get("json_output"):
        import json

        console.print(json.dumps(info.to_dict(), indent=2))
        return

    if options.get("plain"):
        print(f"hostname={info.hostname}")
        print(f"os={info.os_name}")
        print(f"os_display={info.os_display_name}")
        print(f"is_admin={info.is_admin}")
        print(f"home={info.home_directory}")
        print(f"config={info.config_directory}")
        return

    # Rich display
    table = Table(title="🖥️  Local System Information", show_header=False)
    table.add_column("Property", style="cyan")
    table.add_column("Value", style="white")

    table.add_row("Hostname", info.hostname)
    table.add_row("Operating System", info.os_display_name)
    table.add_row("Admin Privileges", "✓ Yes" if info.is_admin else "✗ No")
    table.add_row("Home Directory", str(info.home_directory))
    table.add_row("Config Directory", str(info.config_directory))

    console.print()
    console.print(table)
    console.print()


def resource_usage(options: dict):
    """
    Display local resource usage (CPU, memory, disk).

    Args:
        options: Command options
    """
    console, _, Panel, _ = _ensure_rich()
    local_ops = _get_local_ops()

    console.print("\n[cyan]═══ Resource Usage ═══[/cyan]\n")

    result = local_ops.get_resource_usage()

    if result.exit_code != 0:
        ch.error(f"Failed to get resource usage: {result.stderr}")
        return

    console.print(result.stdout)


# ==================== Hosts File ====================


def hosts_view(options: dict):
    """
    View the system hosts file with syntax highlighting.

    Args:
        options: Command options (plain, json_output)
    """
    console, _, Panel, Syntax = _ensure_rich()
    local_ops = _get_local_ops()

    hosts_path = local_ops.get_hosts_file_path()
    content = local_ops.read_hosts_file()

    if options.get("json_output"):
        import json

        # Parse hosts file into structured data
        entries = []
        for line in content.split("\n"):
            line = line.strip()
            if line and not line.startswith("#"):
                parts = line.split()
                if len(parts) >= 2:
                    entries.append({"ip": parts[0], "hostnames": parts[1:]})
        console.print(json.dumps({"path": str(hosts_path), "entries": entries}, indent=2))
        return

    if options.get("plain"):
        print(f"# Path: {hosts_path}")
        print(content)
        return

    # Rich display with syntax highlighting
    console.print(f"\n[cyan]📄 Hosts File: {hosts_path}[/cyan]\n")

    if content.startswith("Permission denied") or content.startswith("Hosts file not found"):
        ch.error(content)
        return

    # Use Syntax for highlighting
    syntax = Syntax(content, "ini", theme="monokai", line_numbers=True)
    console.print(syntax)

    console.print()
    if not local_ops.can_edit_hosts_file():
        ch.warning("Note: Admin privileges required to edit hosts file")


def hosts_edit(options: dict):
    """
    Open the hosts file in the default editor.

    Requires admin privileges on most systems.

    Args:
        options: Command options
    """
    console, _, _, _ = _ensure_rich()
    local_ops = _get_local_ops()

    hosts_path = local_ops.get_hosts_file_path()

    if not local_ops.can_edit_hosts_file():
        ch.warning(f"Admin privileges required to edit {hosts_path}")
        ch.info("Please run NAVIG as Administrator/root to edit the hosts file.")

        # Show the path so user can manually edit
        console.print(f"\n[cyan]Hosts file location:[/cyan] {hosts_path}")

        os_adapter = local_ops.os_adapter
        if os_adapter.name == "windows":
            console.print("\n[dim]To edit manually:[/dim]")
            console.print("  1. Open Notepad as Administrator")
            console.print(f"  2. File → Open → {hosts_path}")
        else:
            console.print("\n[dim]To edit manually:[/dim]")
            console.print(f"  sudo nano {hosts_path}")
        return

    ch.info(f"Opening {hosts_path} in editor...")
    result = local_ops.open_hosts_in_editor()

    if result.exit_code != 0 and result.stderr:
        ch.error(f"Failed to open editor: {result.stderr}")


def hosts_add(ip: str, hostname: str, options: dict):
    """
    Add an entry to the hosts file.

    Args:
        ip: IP address
        hostname: Hostname to add
        options: Command options
    """
    local_ops = _get_local_ops()

    if not local_ops.can_edit_hosts_file():
        ch.error("Admin privileges required to modify hosts file")
        return

    hosts_path = local_ops.get_hosts_file_path()
    content = local_ops.read_hosts_file()

    if content.startswith("Permission denied"):
        ch.error(content)
        return

    # Check if entry already exists
    for line in content.split("\n"):
        if hostname in line.split() and not line.strip().startswith("#"):
            ch.warning(f"Hostname '{hostname}' already exists in hosts file")
            return

    # Add new entry
    new_entry = f"{ip}\t{hostname}"

    if options.get("dry_run"):
        ch.info(f"DRY RUN: Would add entry: {new_entry}")
        return

    try:
        with open(hosts_path, "a", encoding="utf-8") as f:
            f.write(f"\n{new_entry}")
        ch.success(f"Added hosts entry: {new_entry}")
    except PermissionError:
        ch.error("Permission denied. Run as Administrator/root.")
    except Exception as e:
        ch.error(f"Failed to add entry: {e}")


# ==================== Software Management ====================


def software_list(options: dict):
    """
    List installed software packages.

    Uses the OS-appropriate package manager:
    - Windows: winget
    - Linux: dpkg/rpm/pacman
    - macOS: brew

    Args:
        options: Command options (json_output, plain, limit)
    """
    console, Table, _, _ = _ensure_rich()
    local_ops = _get_local_ops()

    ch.info(f"Listing installed packages ({local_ops.os_adapter.display_name})...")

    packages = local_ops.list_packages()

    if not packages:
        ch.warning("No packages found or package manager not available")
        return

    # Apply limit if specified
    limit = options.get("limit")
    if limit and isinstance(limit, int):
        packages = packages[:limit]

    if options.get("json_output"):
        import json

        console.print(json.dumps([p.to_dict() for p in packages], indent=2))
        return

    if options.get("plain"):
        for pkg in packages:
            print(f"{pkg.name}\t{pkg.version}")
        return

    # Rich table display
    table = Table(title=f"📦 Installed Packages ({len(packages)} shown)")
    table.add_column("Package", style="cyan")
    table.add_column("Version", style="green")
    table.add_column("Source", style="dim")

    for pkg in packages:
        table.add_row(pkg.name, pkg.version, pkg.source or "")

    console.print()
    console.print(table)
    console.print()


def software_search(query: str, options: dict):
    """
    Search installed packages by name.

    Args:
        query: Search term
        options: Command options
    """
    console, Table, _, _ = _ensure_rich()
    local_ops = _get_local_ops()

    packages = local_ops.list_packages()

    # Filter by query
    query_lower = query.lower()
    matches = [p for p in packages if query_lower in p.name.lower()]

    if not matches:
        ch.info(f"No packages matching '{query}' found")
        return

    if options.get("json_output"):
        import json

        console.print(json.dumps([p.to_dict() for p in matches], indent=2))
        return

    if options.get("plain"):
        for pkg in matches:
            print(f"{pkg.name}\t{pkg.version}")
        return

    # Rich table
    table = Table(title=f"🔍 Packages matching '{query}'")
    table.add_column("Package", style="cyan")
    table.add_column("Version", style="green")

    for pkg in matches:
        table.add_row(pkg.name, pkg.version)

    console.print()
    console.print(table)


# ==================== Security ====================


def security_audit(options: dict):
    """
    Run a local security audit.

    Checks:
    - Privilege level (running as admin?)
    - Firewall status
    - Open ports
    - Running services

    Args:
        options: Command options (json_output, verbose, ai)
    """
    console, Table, Panel, _ = _ensure_rich()
    local_ops = _get_local_ops()

    console.print("\n[cyan]═══ Local Security Audit ═══[/cyan]\n")

    checks = local_ops.run_security_audit()

    if options.get("json_output"):
        import json

        console.print(json.dumps([c.to_dict() for c in checks], indent=2))
        return

    # Display results
    table = Table(title="Security Checks")
    table.add_column("Category", style="cyan")
    table.add_column("Status")
    table.add_column("Message")

    for check in checks:
        status_style = {
            "ok": "[green]✓ OK[/green]",
            "warning": "[yellow]⚠ Warning[/yellow]",
            "critical": "[red]✗ Critical[/red]",
        }.get(check.status, check.status)

        table.add_row(check.category.title(), status_style, check.message)

    console.print(table)
    console.print()

    # Verbose mode: show more details
    if options.get("verbose"):
        console.print("[cyan]═══ Open Ports ═══[/cyan]\n")
        ports_result = local_ops.get_open_ports()
        if ports_result.exit_code == 0:
            console.print(ports_result.stdout)
        else:
            ch.warning("Could not list open ports")

        console.print("\n[cyan]═══ Running Services ═══[/cyan]\n")
        services_result = local_ops.get_running_services()
        if services_result.exit_code == 0:
            # Truncate if too long
            output = services_result.stdout
            if len(output) > 3000:
                output = output[:3000] + "\n... (truncated)"
            console.print(output)

    # AI analysis if requested
    if options.get("ai"):
        _run_ai_security_analysis(checks, local_ops, options)


def _run_ai_security_analysis(checks, local_ops, options: dict):
    """Run AI-powered security analysis."""
    console, _, Panel, _ = _ensure_rich()

    console.print("\n[cyan]═══ AI Security Analysis ═══[/cyan]\n")

    try:
        from navig.ai import query_ai

        # Gather context
        system_info = local_ops.get_system_info()
        ports_result = local_ops.get_open_ports()

        context = f"""
Local System Security Audit:
- OS: {system_info.os_display_name}
- Hostname: {system_info.hostname}
- Running as Admin: {system_info.is_admin}

Security Checks:
{chr(10).join(f"- {c.category}: {c.status} - {c.message}" for c in checks)}

Open Ports:
{ports_result.stdout[:1500] if ports_result.exit_code == 0 else "Not available"}
"""

        prompt = """Analyze this local system security audit and provide:
1. Overall security assessment (1-10 scale)
2. Top 3 security concerns (if any)
3. Specific recommendations to improve security
4. Any immediate actions needed

Be concise and actionable."""

        ch.info("Querying AI for security analysis...")
        response = query_ai(prompt, context=context)

        if response:
            console.print(Panel(response, title="🤖 AI Security Analysis", border_style="cyan"))
        else:
            ch.warning("AI analysis not available")

    except ImportError:
        ch.warning("AI module not available for security analysis")
    except Exception as e:
        ch.error(f"AI analysis failed: {e}")


def security_ports(options: dict):
    """
    Show open/listening ports on the local machine.

    Args:
        options: Command options
    """
    console, _, _, _ = _ensure_rich()
    local_ops = _get_local_ops()

    console.print("\n[cyan]═══ Open Ports ═══[/cyan]\n")

    result = local_ops.get_open_ports()

    if result.exit_code != 0:
        ch.error(f"Failed to list ports: {result.stderr}")
        return

    if options.get("plain"):
        print(result.stdout)
    else:
        console.print(result.stdout)


def security_firewall(options: dict):
    """
    Show local firewall status.

    Args:
        options: Command options
    """
    console, _, _, _ = _ensure_rich()
    local_ops = _get_local_ops()

    console.print("\n[cyan]═══ Firewall Status ═══[/cyan]\n")

    result = local_ops.get_firewall_status()

    if result.exit_code != 0:
        ch.warning(f"Could not get firewall status: {result.stderr}")
        ch.info("This may require admin privileges or the firewall service may not be running.")
        return

    if options.get("plain"):
        print(result.stdout)
    else:
        console.print(result.stdout)


# ==================== Network ====================


def network_interfaces(options: dict):
    """
    Show network interfaces.

    Args:
        options: Command options
    """
    console, _, _, _ = _ensure_rich()
    local_ops = _get_local_ops()

    console.print("\n[cyan]═══ Network Interfaces ═══[/cyan]\n")

    result = local_ops.get_network_interfaces()

    if result.exit_code != 0:
        ch.error(f"Failed to list interfaces: {result.stderr}")
        return

    console.print(result.stdout)


def network_ping(host: str, count: int = 4, options: dict = None):
    """
    Ping a host.

    Args:
        host: Host to ping
        count: Number of pings
        options: Command options
    """
    options = options or {}
    console, _, _, _ = _ensure_rich()
    local_ops = _get_local_ops()

    console.print(f"\n[cyan]Pinging {host}...[/cyan]\n")

    result = local_ops.ping(host, count)

    if options.get("plain"):
        print(result.stdout)
        if result.stderr:
            print(result.stderr)
    else:
        console.print(result.stdout)
        if result.stderr:
            console.print(f"[red]{result.stderr}[/red]")


def network_dns(hostname: str, options: dict = None):
    """
    Perform DNS lookup.

    Args:
        hostname: Hostname to lookup
        options: Command options
    """
    options = options or {}
    console, _, _, _ = _ensure_rich()
    local_ops = _get_local_ops()

    console.print(f"\n[cyan]DNS lookup: {hostname}[/cyan]\n")

    result = local_ops.dns_lookup(hostname)

    if result.exit_code != 0:
        ch.warning(f"DNS lookup failed: {result.stderr}")
        return

    console.print(result.stdout)


local_app = typer.Typer(
    help="Local machine management (system info, security, network)",
    invoke_without_command=True,
    no_args_is_help=False,
)


@local_app.callback()
def local_callback(ctx: typer.Context):
    """Local system management - run without subcommand for help."""
    if ctx.invoked_subcommand is None:
        show_subcommand_help("local", ctx)
        raise typer.Exit()


@local_app.command("show")
def local_show_cmd(
    ctx: typer.Context,
    info: bool = typer.Option(True, "--info", "-i", help="Show system information"),
    resources: bool = typer.Option(False, "--resources", "-r", help="Show resource usage"),
):
    """Show local system information."""
    if resources:
        resource_usage(ctx.obj)
    else:
        system_info(ctx.obj)


@local_app.command("audit")
def local_audit_cmd(
    ctx: typer.Context,
    ai: bool = typer.Option(False, "--ai", "-a", help="Include AI analysis"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show detailed information"),
):
    """Run local security audit."""
    ctx.obj["ai"] = ai
    ctx.obj["verbose"] = verbose
    security_audit(ctx.obj)


@local_app.command("ports")
def local_ports_cmd(ctx: typer.Context):
    """Show open/listening ports on local machine."""
    security_ports(ctx.obj)


@local_app.command("firewall")
def local_firewall_cmd(ctx: typer.Context):
    """Show local firewall status."""
    security_firewall(ctx.obj)


@local_app.command("ping")
def local_ping_cmd(
    ctx: typer.Context,
    host: str = typer.Argument(..., help="Host to ping"),
    count: int = typer.Option(4, "--count", "-c", help="Number of pings"),
):
    """Ping a host from local machine."""
    network_ping(host, count, ctx.obj)


@local_app.command("dns")
def local_dns_cmd(
    ctx: typer.Context,
    hostname: str = typer.Argument(..., help="Hostname to lookup"),
):
    """Perform DNS lookup."""
    network_dns(hostname, ctx.obj)


@local_app.command("interfaces")
def local_interfaces_cmd(ctx: typer.Context):
    """Show network interfaces."""
    network_interfaces(ctx.obj)


software_app = typer.Typer(
    help="Local software package management (list, search)",
    invoke_without_command=True,
    no_args_is_help=False,
)


@software_app.callback()
def software_callback(ctx: typer.Context):
    """Software management - run without subcommand to list packages."""
    if ctx.invoked_subcommand is None:
        software_list(ctx.obj)
        raise typer.Exit()


@software_app.command("list")
def software_list_cmd(
    ctx: typer.Context,
    limit: int | None = typer.Option(None, "--limit", "-l", help="Limit number of results"),
):
    """List installed software packages."""
    ctx.obj["limit"] = limit
    software_list(ctx.obj)


@software_app.command("search")
def software_search_cmd(
    ctx: typer.Context,
    query: str = typer.Argument(..., help="Search term"),
):
    """Search installed packages by name."""
    software_search(query, ctx.obj)
