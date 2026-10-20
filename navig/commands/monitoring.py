"""
Resource Monitoring and Health Check Module

Provides comprehensive server monitoring including:
- Real-time resource usage (CPU, RAM, disk, network)
- Service health checks
- Disk space monitoring with alerts
- System metrics collection
- Health reports generation

Author: Navig Team
"""

import json
import platform
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from navig.cli.recovery import require_active_server
from navig.config import get_config_manager
from navig.console_helper import _safe_symbol, console
from navig.monitoring.checks import (
    CPU_COMMAND,
    DISK_COMMAND,
    LOAD_COMMAND,
    MEM_COMMAND,
    NETWORK_COMMAND,
    RESOURCES_COMMAND,
    memory_metrics,
    parse_cpu,
    parse_disk_output,
    parse_memory_usage,
    parse_network,
    parse_resources,
    parse_services,
    service_status_command,
    services_command,
)
from navig.remote import RemoteOperations

try:
    from navig.console_helper import format_bytes as _fmt_bytes
except ImportError:

    def _fmt_bytes(value: Any) -> str:
        return str(value)


try:
    from navig.remote import is_local_host
except ImportError:

    def is_local_host(name: str | None) -> bool:
        return False


def _monitor_disk_local_windows(app_name: str, threshold: int, options: dict) -> None:
    """Disk monitoring for the local Windows machine.

    Uses ``monitor.get_disk_info`` — a raw ``psutil.disk_partitions()`` loop
    blocks indefinitely when a mapped network drive is cold (measured on this
    machine: never returned), and it does not release the GIL. The shared
    collector probes drives concurrently under an overall timeout.
    """
    from navig.commands.monitor import get_disk_info

    disks: list = []
    alerts: list = []
    gb = 1024 ** 3
    for d in get_disk_info():
        pct = int(d["percent"])
        disks.append(
            {
                "device": d["mountpoint"],
                "mount": d["mountpoint"],
                "size": _fmt_bytes(d["total_gb"] * gb),
                "used": _fmt_bytes(d["used_gb"] * gb),
                "available": _fmt_bytes(d["free_gb"] * gb),
                "usage_percent": pct,
            }
        )
        if pct > threshold:
            alerts.append(f"{d['mountpoint']} is {pct}% full (threshold: {threshold}%)")

    if options.get("json_output"):
        console.print(
            json.dumps(
                {
                    "timestamp": datetime.now().isoformat(),
                    "server": app_name,
                    "threshold": threshold,
                    "disks": disks,
                    "alerts": alerts,
                },
                indent=2,
            )
        )
        return

    table = Table(
        title=f"Disk Space - {app_name} (Threshold: {threshold}%)",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Drive", style="cyan")
    table.add_column("Device", style="dim")
    table.add_column("Size", justify="right")
    table.add_column("Used", justify="right")
    table.add_column("Free", justify="right")
    table.add_column("Usage", justify="right")
    table.add_column("Status")
    for d in disks:
        table.add_row(
            d["mount"],
            d["device"],
            d["size"],
            d["used"],
            d["available"],
            f"{d['usage_percent']}%",
            _disk_status(d["usage_percent"], threshold),
        )
    console.print(table)
    if alerts:
        console.print(f"\n[red]{_safe_symbol(chr(0x26A0), '!')} {len(alerts)} Alert(s):[/red]")
        for a in alerts:
            console.print(f"  [red]\u2022[/red] {a}")
    else:
        console.print("\n[green]\u2713[/green] All drives within normal range")


def _monitor_resources_local_windows(app_name: str, options: dict) -> None:
    """Resource monitoring for the local Windows machine via psutil (no SSH)."""
    import psutil

    metrics: dict = {}
    alerts: list = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as _prog:
        _prog.add_task("Collecting local metrics\u2026", total=None)

        cpu = psutil.cpu_percent(interval=1)
        metrics["cpu"] = round(cpu, 1)
        if cpu > 80:
            alerts.append(f"High CPU usage: {cpu}%")

        vm = psutil.virtual_memory()
        metrics["memory"] = {
            "usage_percent": round(vm.percent, 1),
            "used_mb": round(vm.used / 1024 / 1024, 1),
            "total_mb": round(vm.total / 1024 / 1024, 1),
        }
        if vm.percent > 80:
            alerts.append(f"High memory usage: {vm.percent:.1f}%")

        try:
            _root_path = Path.home().anchor or "/"
            _du = psutil.disk_usage(_root_path)
            metrics["disk"] = {
                "usage_percent": int(_du.percent),
                "used": _fmt_bytes(_du.used),
                "total": _fmt_bytes(_du.total),
                "available": _fmt_bytes(_du.free),
            }
            if _du.percent > 80:
                alerts.append(f"High disk usage: {_du.percent:.1f}%")
        except OSError:
            pass  # best-effort: skip on IO error
        try:
            _up_sec = int(datetime.now().timestamp() - psutil.boot_time())
            _h, _r = divmod(_up_sec, 3600)
            metrics["uptime"] = f"up {_h}h {_r // 60}m"
        except Exception:
            metrics["uptime"] = "N/A"

    if options.get("json_output"):
        console.print(
            json.dumps(
                {
                    "timestamp": datetime.now().isoformat(),
                    "server": app_name,
                    "metrics": metrics,
                    "alerts": alerts,
                },
                indent=2,
            )
        )
        return

    table = Table(
        title=f"Resource Usage \u2014 {app_name}", show_header=True, header_style="bold cyan"
    )
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")
    table.add_column("Status")
    _info = _safe_symbol("\u2139", "[i]") + " INFO"
    cpu_val = metrics.get("cpu", 0)
    table.add_row("CPU Usage", f"{cpu_val}%", _traffic_light(cpu_val))
    mem = metrics.get("memory", {})
    mem_val = mem.get("usage_percent", 0)
    table.add_row(
        "Memory",
        f"{mem_val}% ({mem.get('used_mb', 0)} MB / {mem.get('total_mb', 0)} MB)",
        _traffic_light(mem_val),
    )
    disk = metrics.get("disk", {})
    disk_val = disk.get("usage_percent", 0)
    table.add_row(
        "Disk (root)",
        f"{disk_val}% ({disk.get('used', '0')} / {disk.get('total', '0')})",
        _traffic_light(disk_val),
    )
    table.add_row("Uptime", metrics.get("uptime", "N/A"), _info)
    console.print(table)
    if alerts:
        console.print(
            f"\n[yellow]{_safe_symbol(chr(0x26A0), '!')} Alerts ({len(alerts)}):[/yellow]"
        )
        for a in alerts:
            console.print(f"  [yellow]\u2022[/yellow] {a}")
    else:
        console.print("\n[green]\u2713[/green] All metrics within normal range")


def _traffic_light(val: float, high: int = 80, med: int = 60) -> str:
    """Return a safe terminal status string for a percentage metric."""
    if val > high:
        return _safe_symbol("\U0001f534", "[!]") + " HIGH"
    if val > med:
        return _safe_symbol("\U0001f7e1", "[~]") + " MEDIUM"
    return _safe_symbol("\U0001f7e2", "[+]") + " OK"


def _disk_status(usage: float, threshold: int) -> str:
    """Return a safe terminal status string for disk usage against a threshold."""
    if usage > threshold:
        return _safe_symbol("\U0001f534", "[!]") + " ALERT"
    if usage > threshold - 10:
        return _safe_symbol("\U0001f7e1", "[~]") + " WARNING"
    return _safe_symbol("\U0001f7e2", "[+]") + " OK"


def _health_icon(health: str) -> str:
    """Return Rich-markup health icon with ASCII fallback for narrow terminals."""
    if health == "healthy":
        return f"[green]{_safe_symbol(chr(0x1F7E2), 'OK')} healthy[/green]"
    if health == "stopped":
        return f"[red]{_safe_symbol(chr(0x1F534), '!!')} stopped[/red]"
    if health == "not-installed":
        return f"[dim]{_safe_symbol(chr(0x26AA), '--')} N/A[/dim]"
    return f"[yellow]{_safe_symbol(chr(0x1F7E1), '?')} unknown[/yellow]"


def monitor_resources(options: dict[str, Any]) -> None:
    """
    Monitor real-time resource usage (CPU, RAM, disk, network).

    Shows:
    - CPU usage percentage
    - Memory usage (used/total)
    - Disk usage for all mounts
    - Load averages (1, 5, 15 min)
    - Network connections count

    Args:
        options: Command options including server config, dry_run, json_output
    """
    config = get_config_manager()
    app_name = require_active_server(options, config)

    server_config = config.load_server_config(app_name)
    remote = RemoteOperations(config)

    if options.get("dry_run"):
        console.print("[yellow]DRY RUN:[/yellow] Would monitor resources on", app_name)
        return

    console.print(
        f"\n[cyan]{_safe_symbol(chr(0x1F4CA), '>>')} Monitoring Resources:[/cyan] {app_name}\n"
    )

    # -- Local Windows fast-path (psutil, no SSH, no Linux commands) --
    if is_local_host(server_config) and platform.system() == "Windows":
        _monitor_resources_local_windows(app_name, options)
        return

    # -- QUANTUM VELOCITY E1: all 6 metrics in ONE SSH round-trip --
    # Previously: 6 separate SSH calls (~6x round-trip latency).
    # Now: 1 batched command with delimited sections (~1x round-trip). The recipe
    # and its parser live in navig.monitoring.checks, so the library API
    # (navig.monitoring.collect) reads a host through exactly this data path.
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task("Collecting metrics\u2026", total=None)
        batch_result = remote.execute_command(RESOURCES_COMMAND, server_config)

    # -- Parse delimited output (shared with navig.monitoring.collect) --
    metrics, alerts = parse_resources(batch_result.stdout, batch_result.returncode)

    # Display results
    if options.get("json_output"):
        output = {
            "timestamp": datetime.now().isoformat(),
            "server": app_name,
            "metrics": metrics,
            "alerts": alerts,
        }
        console.print(json.dumps(output, indent=2))
    else:
        # Create metrics table
        table = Table(title="Resource Usage", show_header=True, header_style="bold cyan")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white")
        table.add_column("Status", style="green")

        # CPU row
        cpu_val = metrics.get("cpu", 0)
        cpu_status = _traffic_light(cpu_val)
        table.add_row("CPU Usage", f"{cpu_val}%", cpu_status)

        # Memory row
        mem = metrics.get("memory", {})
        mem_val = mem.get("usage_percent", 0)
        mem_status = _traffic_light(mem_val)
        table.add_row(
            "Memory Usage",
            f"{mem_val}% ({mem.get('used_mb', 0)} MB / {mem.get('total_mb', 0)} MB)",
            mem_status,
        )

        # Disk row
        disk = metrics.get("disk", {})
        disk_val = disk.get("usage_percent", 0)
        disk_status = _traffic_light(disk_val)
        table.add_row(
            "Disk Usage",
            f"{disk_val}% ({disk.get('used', '0')} / {disk.get('total', '0')})",
            disk_status,
        )

        # Load average
        _info = _safe_symbol("\u2139", "[i]") + " INFO"
        table.add_row("Load Average", metrics.get("load_average", "N/A"), _info)

        # Connections
        table.add_row("TCP Connections", metrics.get("tcp_connections", "N/A"), _info)

        # Uptime
        table.add_row("Uptime", metrics.get("uptime", "N/A"), _info)

        console.print(table)

        # Display alerts
        if alerts:
            console.print(
                f"\n[yellow]{_safe_symbol(chr(0x26A0), '!')} Alerts ({len(alerts)}):[/yellow]"
            )
            for alert in alerts:
                console.print(f"  [yellow]\u2022[/yellow] {alert}")
        else:
            console.print("\n[green]\u2713[/green] All metrics within normal range")


def monitor_disk(threshold: int, options: dict[str, Any]) -> None:
    """
    Monitor disk space with custom threshold alerts.

    Args:
        threshold: Alert threshold percentage (default: 80)
        options: Command options including server config, dry_run, json_output
    """
    config = get_config_manager()
    app_name = require_active_server(options, config)

    server_config = config.load_server_config(app_name)
    remote = RemoteOperations(config)

    if options.get("dry_run"):
        console.print(
            f"[yellow]DRY RUN:[/yellow] Would check disk space on {app_name} (threshold: {threshold}%)"
        )
        return

    console.print(
        f"\n[cyan]{_safe_symbol(chr(0x1F4BE), '>>')} Disk Space Monitoring:[/cyan] {app_name}\n"
    )

    # -- Local Windows fast-path (psutil, no SSH, no Linux commands) --
    if is_local_host(server_config) and platform.system() == "Windows":
        _monitor_disk_local_windows(app_name, threshold, options)
        return

    # Get all disk partitions (shared recipe + parser with navig.monitoring.collect)
    disk_result = remote.execute_command(DISK_COMMAND, server_config)
    disks = parse_disk_output(disk_result.stdout, disk_result.returncode)

    if disks is None:
        console.print("[red]\u2717[/red] Failed to retrieve disk information")
        return

    alerts = [
        f"{disk['mount']} is {disk['usage_percent']}% full (threshold: {threshold}%)"
        for disk in disks
        if disk["usage_percent"] > threshold
    ]

    # Display results
    if options.get("json_output"):
        output = {
            "timestamp": datetime.now().isoformat(),
            "server": app_name,
            "threshold": threshold,
            "disks": disks,
            "alerts": alerts,
        }
        console.print(json.dumps(output, indent=2))
    else:
        table = Table(
            title=f"Disk Space (Threshold: {threshold}%)",
            show_header=True,
            header_style="bold cyan",
        )
        table.add_column("Mount Point", style="cyan")
        table.add_column("Device", style="dim")
        table.add_column("Size", justify="right")
        table.add_column("Used", justify="right")
        table.add_column("Available", justify="right")
        table.add_column("Usage", justify="right")
        table.add_column("Status")

        for disk in disks:
            usage = disk["usage_percent"]
            status = _disk_status(usage, threshold)

            table.add_row(
                disk["mount"],
                disk["device"],
                disk["size"],
                disk["used"],
                disk["available"],
                f"{usage}%",
                status,
            )

        console.print(table)

        if alerts:
            console.print(f"\n[red]{_safe_symbol(chr(0x26A0), '!')} {len(alerts)} Alert(s):[/red]")
            for alert in alerts:
                console.print(f"  [red]\u2022[/red] {alert}")
        else:
            console.print(f"\n[green]\u2713[/green] All disks below {threshold}% threshold")


def monitor_services(options: dict[str, Any]) -> None:
    """
    Check health status of critical services.

    Monitors: nginx, apache2, mysql, postgresql, php-fpm, hestia, fail2ban, ufw, ssh

    Args:
        options: Command options including server config, dry_run, json_output
    """
    config = get_config_manager()
    app_name = require_active_server(options, config)

    server_config = config.load_server_config(app_name)
    # RemoteOperations takes the ConfigManager; server_config is an argument to
    # each call (only execute_command_parallel reads self.config, so this was a
    # latent wrong-type constructor rather than an immediate crash).
    remote = RemoteOperations(config)

    if options.get("dry_run"):
        console.print(f"[yellow]DRY RUN:[/yellow] Would check services on {app_name}")
        return

    console.print(
        f"\n[cyan]{_safe_symbol(chr(0x1F527), '>>')} Service Health Check:[/cyan] {app_name}\n"
    )

    # -- QUANTUM VELOCITY E2: all service checks in ONE SSH round-trip --
    # Previously: 1 SSH call per service x 16 services = 16 round-trips.
    # Now: 1 batched command -> parse delimited output. Recipe + parser live in
    # navig.monitoring.checks (shared with navig.monitoring.collect).
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task("Checking services\u2026", total=None)
        svc_batch = remote.execute_command(services_command(), server_config)

    # -- Parse delimited output --
    service_status, inactive_services = parse_services(
        svc_batch.stdout, svc_batch.returncode
    )

    # Display results
    if options.get("json_output"):
        output = {
            "timestamp": datetime.now().isoformat(),
            "server": app_name,
            "services": service_status,
            "inactive_count": len(inactive_services),
        }
        console.print(json.dumps(output, indent=2))
    else:
        table = Table(title="Service Status", show_header=True, header_style="bold cyan")
        table.add_column("Service", style="cyan")
        table.add_column("Status", justify="center")
        table.add_column("Health", justify="center")

        for svc in service_status:
            status = svc["status"]

            if status == "active":
                status_icon = "[green]\u2713 active[/green]"
                health_icon = _health_icon("healthy")
            elif status == "inactive":
                status_icon = "[red]\u2717 inactive[/red]"
                health_icon = _health_icon("stopped")
            elif status == "not-installed":
                status_icon = "[dim]- not installed[/dim]"
                health_icon = _health_icon("not-installed")
            else:
                status_icon = f"[yellow]? {status}[/yellow]"
                health_icon = _health_icon("unknown")

            table.add_row(svc["name"], status_icon, health_icon)

        console.print(table)

        if inactive_services:
            console.print(
                f"\n[yellow]{_safe_symbol(chr(0x26A0), '!')} {len(inactive_services)} service(s) inactive:[/yellow]"
            )
            for svc in inactive_services:
                console.print(f"  [yellow]\u2022[/yellow] {svc}")
        else:
            console.print("\n[green]\u2713[/green] All installed services are running")


def monitor_network(options: dict[str, Any]) -> None:
    """
    Monitor network statistics and connections.

    Shows:
    - Active TCP/UDP connections
    - Listening ports
    - Network interface statistics
    - Bandwidth usage (if available)

    Args:
        options: Command options including server config, dry_run, json_output
    """
    config = get_config_manager()
    app_name = require_active_server(options, config)

    server_config = config.load_server_config(app_name)
    # RemoteOperations takes the ConfigManager; server_config is an argument to
    # each call (only execute_command_parallel reads self.config, so this was a
    # latent wrong-type constructor rather than an immediate crash).
    remote = RemoteOperations(config)

    if options.get("dry_run"):
        console.print(f"[yellow]DRY RUN:[/yellow] Would check network stats on {app_name}")
        return

    console.print(
        f"\n[cyan]{_safe_symbol(chr(0x1F310), '>>')} Network Statistics:[/cyan] {app_name}\n"
    )

    # -- QUANTUM VELOCITY E3: all network metrics in ONE SSH round-trip --
    # Recipe + parser live in navig.monitoring.checks (shared with collect).
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task("Collecting network stats\u2026", total=None)
        net_batch = remote.execute_command(NETWORK_COMMAND, server_config)

    # -- Parse delimited output --
    metrics = parse_network(net_batch.stdout, net_batch.returncode)

    # Display results
    if options.get("json_output"):
        output = {
            "timestamp": datetime.now().isoformat(),
            "server": app_name,
            "metrics": metrics,
        }
        console.print(json.dumps(output, indent=2))
    else:
        # Connection summary panel
        conn_text = metrics.get("connection_summary", "No data")
        panel = Panel(conn_text, title="[cyan]Connection Summary[/cyan]", border_style="cyan")
        console.print(panel)

        # Stats table
        table = Table(show_header=False, box=None)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white", justify="right")

        table.add_row("Listening Ports", str(metrics.get("listening_ports", 0)))
        table.add_row("Established Connections", str(metrics.get("established_connections", 0)))

        if "interfaces" in metrics:
            table.add_row("Network Interfaces", ", ".join(metrics["interfaces"]))

        console.print("\n", table)


def health_check(options: dict[str, Any]) -> None:
    """
    Comprehensive health check combining all monitoring aspects.

    Runs:
    - Resource monitoring (CPU, RAM, disk)
    - Service health checks
    - Disk space checks
    - Network statistics

    Args:
        options: Command options including server config, dry_run, json_output
    """
    config = get_config_manager()
    app_name = require_active_server(options, config)

    if options.get("dry_run"):
        console.print(
            f"[yellow]DRY RUN:[/yellow] Would run comprehensive health check on {app_name}"
        )
        return

    console.print(
        f"\n[bold cyan]{_safe_symbol(chr(0x1F3E5), '>>')} Comprehensive Health Check:[/bold cyan] {app_name}\n"
    )

    # Run all monitoring checks
    console.print(f"[cyan]{_safe_symbol(chr(0x2192), '->')}[/cyan] Checking resources...")
    monitor_resources(options)

    console.print(f"\n[cyan]{_safe_symbol(chr(0x2192), '->')}[/cyan] Checking services...")
    monitor_services(options)

    console.print(f"\n[cyan]{_safe_symbol(chr(0x2192), '->')}[/cyan] Checking disk space...")
    monitor_disk(80, options)

    console.print(f"\n[cyan]{_safe_symbol(chr(0x2192), '->')}[/cyan] Checking network...")
    monitor_network(options)

    console.print(f"\n[green]{_safe_symbol(chr(0x2713), 'OK')}[/green] Health check complete")


def run_health_check(options: dict[str, Any]) -> None:
    """Legacy compatibility wrapper for comprehensive health checks."""
    health_check(options)


def view_service_logs(service: str, tail: bool, lines: int, options: dict[str, Any]) -> None:
    """Legacy compatibility wrapper to show service logs via systemd journal."""
    config = get_config_manager()
    app_name = require_active_server(options, config)

    if not re.fullmatch(r"[A-Za-z0-9@_.-]+", service):
        console.print(f"[red]Invalid service name:[/red] {service}")
        return

    server_config = config.load_server_config(app_name)
    remote = RemoteOperations(config)

    follow_flag = " -f" if tail else ""
    line_count = max(1, int(lines or 100))
    cmd = f"journalctl -u {service} -n {line_count}{follow_flag} --no-pager"
    result = remote.execute_command(cmd, server_config)

    if result.returncode == 0:
        console.print(result.stdout)
    else:
        error_text = (result.stderr or result.stdout or "Failed to read service logs").strip()
        console.print(f"[red]{error_text}[/red]")


def restart_remote_service(service: str, options: dict[str, Any]) -> None:
    """Legacy compatibility wrapper to restart a remote service."""
    config = get_config_manager()
    app_name = require_active_server(options, config)

    if not re.fullmatch(r"[A-Za-z0-9@_.-]+", service):
        console.print(f"[red]Invalid service name:[/red] {service}")
        return

    # Multi-agent safety: claim the host before mutating it (navig.core.host_lock).
    # This lives in `monitoring`, which is otherwise read-only — restarting a service on a
    # server another session is deploying to is exactly the collision #1099 exists for.
    from navig.core import host_lock  # noqa: PLC0415

    host_lock.guard_remote(config, app_name, f"navig monitoring restart-service: {service}")

    server_config = config.load_server_config(app_name)
    remote = RemoteOperations(config)

    cmd = f"sudo systemctl restart {service}"
    result = remote.execute_command(cmd, server_config)

    if result.returncode == 0:
        console.print(f"[green]Restarted service:[/green] {service}")
    else:
        error_text = (result.stderr or result.stdout or "Service restart failed").strip()
        console.print(f"[red]{error_text}[/red]")


def generate_report(options: dict[str, Any]) -> None:
    """
    Generate comprehensive monitoring report and save to file.

    Creates JSON report with:
    - Timestamp
    - Server information
    - Resource metrics
    - Service status
    - Disk usage
    - Network statistics
    - Alerts and warnings

    Args:
        options: Command options including server config, dry_run
    """
    config = get_config_manager()
    app_name = require_active_server(options, config)

    # `get_app_config` never existed on ConfigManager (it was removed as API
    # drift — tests/security/test_security_fixes.py pins its absence), so this
    # line raised AttributeError on every real run: the MagicMock the tests hand
    # the command invented it. load_server_config is the documented replacement.
    server_config = config.load_server_config(app_name)
    # RemoteOperations takes the ConfigManager; server_config is an argument to
    # each call (only execute_command_parallel reads self.config, so this was a
    # latent wrong-type constructor rather than an immediate crash).
    remote = RemoteOperations(config)

    if options.get("dry_run"):
        console.print(f"[yellow]DRY RUN:[/yellow] Would generate report for {app_name}")
        return

    console.print(
        f"\n[cyan]{_safe_symbol(chr(0x1F4DD), '[report]')} Generating Health Report:[/cyan] {app_name}\n"
    )

    report = {
        "timestamp": datetime.now().isoformat(),
        "server": app_name,
        "metrics": {},
        "services": [],
        "disks": [],
        "network": {},
        "alerts": [],
    }

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Collecting data...", total=4)

        # Collect resource metrics — the recipes and parsers are the shared ones in
        # navig.monitoring.checks, so this report reads a host through the same data
        # path as `monitor_resources` and `navig.monitoring.collect`.
        progress.update(task, description="Collecting resources...")
        # CPU
        cpu_result = remote.execute_command(CPU_COMMAND, server_config)
        if cpu_result.returncode == 0:
            cpu_usage = parse_cpu(cpu_result.stdout)
            if cpu_usage is not None:
                report["metrics"]["cpu"] = round(cpu_usage, 1)
                if cpu_usage > 80:
                    report["alerts"].append(f"High CPU usage: {cpu_usage}%")

        # Memory — the report's own three-key dict (key order is its JSON shape).
        mem_result = remote.execute_command(MEM_COMMAND, server_config)
        if mem_result.returncode == 0:
            mem_raw = parse_memory_usage(mem_result.stdout)
            if mem_raw is not None:
                mem_usage, mem_used_kb, mem_total_kb = mem_raw
                full_memory = memory_metrics(mem_usage, mem_used_kb, mem_total_kb)
                report["metrics"]["memory"] = {
                    "usage_percent": full_memory["usage_percent"],
                    "used_mb": full_memory["used_mb"],
                    "total_mb": full_memory["total_mb"],
                }
                if mem_usage > 80:
                    report["alerts"].append(f"High memory usage: {mem_usage}%")

        # Disk
        disk_result = remote.execute_command(DISK_COMMAND, server_config)
        for disk in parse_disk_output(disk_result.stdout, disk_result.returncode) or []:
            report["disks"].append(
                {
                    "device": disk["device"],
                    "mount": disk["mount"],
                    "size": disk["size"],
                    "used": disk["used"],
                    "usage_percent": disk["usage_percent"],
                }
            )
            if disk["usage_percent"] > 80:
                report["alerts"].append(f"{disk['mount']} is {disk['usage_percent']}% full")

        progress.update(task, advance=1)

        # Collect service status
        progress.update(task, description="Checking services...")
        services = [
            "nginx",
            "apache2",
            "mysql",
            "mariadb",
            "postgresql",
            "php-fpm",
            "hestia",
            "fail2ban",
        ]
        for service in services:
            status_cmd = service_status_command(service)
            result = remote.execute_command(status_cmd, server_config)
            status = result.stdout.strip() if result.returncode == 0 else "unknown"

            report["services"].append({"name": service, "status": status})

            if status == "inactive":
                report["alerts"].append(f"Service {service} is inactive")

        progress.update(task, advance=1)

        # Network stats
        progress.update(task, description="Collecting network stats...")
        estab_cmd = "ss -tn | grep ESTAB | wc -l"
        estab_result = remote.execute_command(estab_cmd, server_config)
        if estab_result.returncode == 0:
            report["network"]["established_connections"] = int(estab_result.stdout.strip())

        progress.update(task, advance=1)

        # Load average
        load_result = remote.execute_command(LOAD_COMMAND, server_config)
        if load_result.returncode == 0:
            report["metrics"]["load_average"] = load_result.stdout.strip()

        progress.update(task, advance=1)

    # Save report
    reports_dir = config.base_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)

    timestamp_str = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_file = reports_dir / f"health-report_{app_name}_{timestamp_str}.json"

    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(report, indent=2, fp=f)

    # Display summary
    console.print(
        f"\n[green]{_safe_symbol(chr(0x2713), 'OK')}[/green] Report generated: {report_file}"
    )
    console.print("\n[cyan]Summary:[/cyan]")
    console.print(f"  {_safe_symbol(chr(0x2022), '-')} Server: {app_name}")
    console.print(f"  {_safe_symbol(chr(0x2022), '-')} Timestamp: {report['timestamp']}")
    console.print(f"  {_safe_symbol(chr(0x2022), '-')} Alerts: {len(report['alerts'])}")

    if report["alerts"]:
        console.print(f"\n[yellow]{_safe_symbol(chr(0x26A0), '!')} Alerts:[/yellow]")
        for alert in report["alerts"]:
            console.print(f"  [yellow]{_safe_symbol(chr(0x2022), '-')}[/yellow] {alert}")
    else:
        console.print(
            f"\n[green]{_safe_symbol(chr(0x2713), 'OK')}[/green] No alerts - system healthy"
        )
