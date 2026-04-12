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
from typing import Any

from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from navig.cli.recovery import require_active_server
from navig.config import get_config_manager
from navig.console_helper import _safe_symbol, console
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
    """Disk monitoring for the local Windows machine via psutil (no SSH)."""
    import psutil

    disks: list = []
    alerts: list = []
    for part in psutil.disk_partitions(all=False):
        try:
            usage = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        pct = int(usage.percent)
        disks.append({
            "device": part.device,
            "mount": part.mountpoint,
            "size": _fmt_bytes(usage.total),
            "used": _fmt_bytes(usage.used),
            "available": _fmt_bytes(usage.free),
            "usage_percent": pct,
        })
        if pct > threshold:
            alerts.append(f"{part.mountpoint} is {pct}% full (threshold: {threshold}%)")

    if options.get("json_output"):
        console.print(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "server": app_name, "threshold": threshold,
            "disks": disks, "alerts": alerts,
        }, indent=2))
        return

    table = Table(title=f"Disk Space - {app_name} (Threshold: {threshold}%)",
                  show_header=True, header_style="bold cyan")
    table.add_column("Drive", style="cyan")
    table.add_column("Device", style="dim")
    table.add_column("Size", justify="right")
    table.add_column("Used", justify="right")
    table.add_column("Free", justify="right")
    table.add_column("Usage", justify="right")
    table.add_column("Status")
    for d in disks:
        table.add_row(
            d["mount"], d["device"],
            d["size"], d["used"], d["available"],
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

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                  console=console, transient=True) as _prog:
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
            _root_path = "C:\\" if platform.system() == "Windows" else "/"
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
        console.print(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "server": app_name, "metrics": metrics, "alerts": alerts,
        }, indent=2))
        return

    table = Table(title=f"Resource Usage \u2014 {app_name}", show_header=True, header_style="bold cyan")
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="white")
    table.add_column("Status")
    _info = _safe_symbol("\u2139", "[i]") + " INFO"
    cpu_val = metrics.get("cpu", 0)
    table.add_row("CPU Usage", f"{cpu_val}%", _traffic_light(cpu_val))
    mem = metrics.get("memory", {})
    mem_val = mem.get("usage_percent", 0)
    table.add_row("Memory",
                  f"{mem_val}% ({mem.get('used_mb', 0)} MB / {mem.get('total_mb', 0)} MB)",
                  _traffic_light(mem_val))
    disk = metrics.get("disk", {})
    disk_val = disk.get("usage_percent", 0)
    table.add_row("Disk (root)",
                  f"{disk_val}% ({disk.get('used', '0')} / {disk.get('total', '0')})",
                  _traffic_light(disk_val))
    table.add_row("Uptime", metrics.get("uptime", "N/A"), _info)
    console.print(table)
    if alerts:
        console.print(f"\n[yellow]{_safe_symbol(chr(0x26A0), '!')} Alerts ({len(alerts)}):[/yellow]")
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

    metrics = {}
    alerts = []

    # -- QUANTUM VELOCITY E1: all 6 metrics in ONE SSH round-trip --
    # Previously: 6 separate SSH calls (~6x round-trip latency).
    # Now: 1 batched command with delimited sections (~1x round-trip).
    _BATCH = (
        "echo '===CPU==='; top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1; "
        "echo '===MEM==='; free | grep Mem | awk '{print ($3/$2) * 100.0, $3, $2}'; "
        "echo '===DISK==='; df -h / | tail -1 | awk '{print $5, $3, $2, $4}' | sed 's/%//'; "
        "echo '===LOAD==='; uptime | awk -F'load average:' '{print $2}' | xargs; "
        "echo '===CONN==='; ss -s 2>/dev/null | grep 'TCP:' | awk '{print $2}'; "
        "echo '===UPTIME==='; uptime -p"
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task("Collecting metrics\u2026", total=None)
        batch_result = remote.execute_command(_BATCH, server_config)

    # -- Parse delimited output --
    if batch_result.returncode == 0:
        _sections: dict = {}
        _cur_key = None
        for _line in batch_result.stdout.splitlines():
            if _line.startswith("===") and _line.endswith("==="):
                _cur_key = _line.strip("=").strip()
                _sections[_cur_key] = []
            elif _cur_key is not None:
                _sections[_cur_key].append(_line)

        # CPU
        try:
            cpu_usage = float(_sections.get("CPU", ["0"])[0].strip())
            metrics["cpu"] = round(cpu_usage, 1)
            if cpu_usage > 80:
                alerts.append(f"High CPU usage: {cpu_usage}%")
        except (ValueError, IndexError):
            pass  # malformed value; skip

        # Memory
        try:
            _p = _sections.get("MEM", ["0 0 0"])[0].strip().split()
            mem_usage = float(_p[0])
            mem_used = int(_p[1])
            mem_total = int(_p[2])
            metrics["memory"] = {
                "usage_percent": round(mem_usage, 1),
                "used_kb": mem_used,
                "total_kb": mem_total,
                "used_mb": round(mem_used / 1024, 1),
                "total_mb": round(mem_total / 1024, 1),
            }
            if mem_usage > 80:
                alerts.append(f"High memory usage: {mem_usage}%")
        except (ValueError, IndexError):
            pass  # malformed value; skip

        # Disk
        try:
            _p = _sections.get("DISK", ["0 0 0 0"])[0].strip().split()
            disk_usage = int(_p[0])
            metrics["disk"] = {
                "usage_percent": disk_usage,
                "used": _p[1],
                "total": _p[2],
                "available": _p[3],
            }
            if disk_usage > 80:
                alerts.append(f"High disk usage: {disk_usage}%")
        except (ValueError, IndexError):
            pass  # malformed value; skip

        # Scalar metrics
        if _sections.get("LOAD"):
            metrics["load_average"] = _sections["LOAD"][0].strip()
        if _sections.get("CONN"):
            metrics["tcp_connections"] = _sections["CONN"][0].strip()
        if _sections.get("UPTIME"):
            metrics["uptime"] = _sections["UPTIME"][0].strip()

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

    # Get all disk partitions
    disk_cmd = "df -h | grep -E '^/dev/'"
    result = remote.execute_command(disk_cmd, server_config)

    if result.returncode != 0:
        console.print("[red]\u2717[/red] Failed to retrieve disk information")
        return

    disks = []
    alerts = []

    for line in result.stdout.strip().split("\n"):
        # Parse: /dev/sda1  20G  15G  4.2G  79% /
        match = re.match(r"(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d+)%\s+(\S+)", line)
        if match:
            device, size, used, available, usage, mount = match.groups()
            usage_int = int(usage)

            disk_info = {
                "device": device,
                "size": size,
                "used": used,
                "available": available,
                "usage_percent": usage_int,
                "mount": mount,
            }
            disks.append(disk_info)

            if usage_int > threshold:
                alerts.append(f"{mount} is {usage_int}% full (threshold: {threshold}%)")

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

    server_config = config.get_app_config(app_name)
    remote = RemoteOperations(server_config)

    if options.get("dry_run"):
        console.print(f"[yellow]DRY RUN:[/yellow] Would check services on {app_name}")
        return

    console.print(
        f"\n[cyan]{_safe_symbol(chr(0x1F527), '>>')} Service Health Check:[/cyan] {app_name}\n"
    )

    # Critical services to monitor
    services = [
        "nginx",
        "apache2",
        "mysql",
        "mariadb",
        "postgresql",
        "php8.3-fpm",
        "php8.2-fpm",
        "php8.1-fpm",
        "php-fpm",
        "hestia",
        "fail2ban",
        "ufw",
        "ssh",
        "sshd",
        "redis-server",
        "memcached",
    ]

    service_status = []
    inactive_services = []

    # -- QUANTUM VELOCITY E2: all service checks in ONE SSH round-trip --
    # Previously: 1 SSH call per service x 16 services = 16 round-trips.
    # Now: 1 batched command -> parse delimited output.
    _BATCH_SVC = "; ".join(
        f"echo '===SVC:{s}==='; systemctl is-active {s} 2>/dev/null || echo 'not-installed'"
        for s in services
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task("Checking services\u2026", total=None)
        svc_batch = remote.execute_command(_BATCH_SVC, server_config)

    # -- Parse delimited output --
    _svc_map: dict = {}
    if svc_batch.returncode == 0:
        _cur_svc = None
        for _line in svc_batch.stdout.splitlines():
            if _line.startswith("===SVC:") and _line.endswith("==="):
                _cur_svc = _line[7:-3].strip()
                _svc_map[_cur_svc] = []
            elif _cur_svc is not None and _line.strip():
                _svc_map[_cur_svc].append(_line.strip())

    for service in services:
        status = (_svc_map.get(service, ["unknown"])[0] or "unknown").strip()
        service_info = {"name": service, "status": status}
        if status == "active":
            service_info["health"] = "healthy"
        elif status == "inactive":
            service_info["health"] = "stopped"
            inactive_services.append(service)
        elif status == "not-installed":
            service_info["health"] = "not-installed"
        else:
            service_info["health"] = "unknown"
        service_status.append(service_info)

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

    server_config = config.get_app_config(app_name)
    remote = RemoteOperations(server_config)

    if options.get("dry_run"):
        console.print(f"[yellow]DRY RUN:[/yellow] Would check network stats on {app_name}")
        return

    console.print(
        f"\n[cyan]{_safe_symbol(chr(0x1F310), '>>')} Network Statistics:[/cyan] {app_name}\n"
    )

    metrics = {}

    # -- QUANTUM VELOCITY E3: all network metrics in ONE SSH round-trip --
    _BATCH_NET = (
        "echo '===CONN==='; ss -s; "
        "echo '===LISTEN==='; ss -tuln 2>/dev/null | grep -c LISTEN || echo 0; "
        "echo '===ESTAB==='; ss -tn 2>/dev/null | grep -c ESTAB || echo 0; "
        "echo '===IFACE==='; ip -s link show | grep -E '^[0-9]+:' | awk '{print $2}' | sed 's/:$//' | head -5"
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True,
    ) as progress:
        progress.add_task("Collecting network stats\u2026", total=None)
        net_batch = remote.execute_command(_BATCH_NET, server_config)

    # -- Parse delimited output --
    if net_batch.returncode == 0:
        _net_sections: dict = {}
        _cur_net = None
        for _line in net_batch.stdout.splitlines():
            if _line.startswith("===") and _line.endswith("==="):
                _cur_net = _line.strip("=").strip()
                _net_sections[_cur_net] = []
            elif _cur_net is not None:
                _net_sections[_cur_net].append(_line)

        if _net_sections.get("CONN"):
            metrics["connection_summary"] = "\n".join(_net_sections["CONN"]).strip()
        try:
            metrics["listening_ports"] = int((_net_sections.get("LISTEN", ["0"])[0] or "0").strip())
        except ValueError:
            pass  # malformed value; skip
        try:
            metrics["established_connections"] = int(
                (_net_sections.get("ESTAB", ["0"])[0] or "0").strip()
            )
        except ValueError:
            pass  # malformed value; skip
        if _net_sections.get("IFACE"):
            metrics["interfaces"] = [ln for ln in _net_sections["IFACE"] if ln.strip()]

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

    server_config = config.get_app_config(app_name)
    remote = RemoteOperations(server_config)

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

        # Collect resource metrics
        progress.update(task, description="Collecting resources...")
        # CPU
        cpu_cmd = "top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1"
        cpu_result = remote.execute_command(cpu_cmd, server_config)
        if cpu_result.returncode == 0:
            cpu_usage = float(cpu_result.stdout.strip())
            report["metrics"]["cpu"] = round(cpu_usage, 1)
            if cpu_usage > 80:
                report["alerts"].append(f"High CPU usage: {cpu_usage}%")

        # Memory
        mem_cmd = "free | grep Mem | awk '{print ($3/$2) * 100.0, $3, $2}'"
        mem_result = remote.execute_command(mem_cmd, server_config)
        if mem_result.returncode == 0:
            parts = mem_result.stdout.strip().split()
            mem_usage = float(parts[0])
            report["metrics"]["memory"] = {
                "usage_percent": round(mem_usage, 1),
                "used_mb": round(int(parts[1]) / 1024, 1),
                "total_mb": round(int(parts[2]) / 1024, 1),
            }
            if mem_usage > 80:
                report["alerts"].append(f"High memory usage: {mem_usage}%")

        # Disk
        disk_cmd = "df -h | grep -E '^/dev/'"
        disk_result = remote.execute_command(disk_cmd, server_config)
        if disk_result.returncode == 0:
            for line in disk_result.stdout.strip().split("\n"):
                match = re.match(r"(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d+)%\s+(\S+)", line)
                if match:
                    device, size, used, available, usage, mount = match.groups()
                    usage_int = int(usage)
                    report["disks"].append(
                        {
                            "device": device,
                            "mount": mount,
                            "size": size,
                            "used": used,
                            "usage_percent": usage_int,
                        }
                    )
                    if usage_int > 80:
                        report["alerts"].append(f"{mount} is {usage_int}% full")

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
            status_cmd = f"systemctl is-active {service} 2>/dev/null || echo 'not-installed'"
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
        load_cmd = "uptime | awk -F'load average:' '{print $2}' | xargs"
        load_result = remote.execute_command(load_cmd, server_config)
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
    console.print(f"\n[green]{_safe_symbol(chr(0x2713), 'OK')}[/green] Report generated: {report_file}")
    console.print("\n[cyan]Summary:[/cyan]")
    console.print(f"  {_safe_symbol(chr(0x2022), '-')} Server: {app_name}")
    console.print(f"  {_safe_symbol(chr(0x2022), '-')} Timestamp: {report['timestamp']}")
    console.print(f"  {_safe_symbol(chr(0x2022), '-')} Alerts: {len(report['alerts'])}")

    if report["alerts"]:
        console.print(f"\n[yellow]{_safe_symbol(chr(0x26A0), '!')} Alerts:[/yellow]")
        for alert in report["alerts"]:
            console.print(f"  [yellow]{_safe_symbol(chr(0x2022), '-')}[/yellow] {alert}")
    else:
        console.print(f"\n[green]{_safe_symbol(chr(0x2713), 'OK')}[/green] No alerts - system healthy")
