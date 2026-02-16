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
import re
from datetime import datetime
from typing import Dict, Any

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from navig.config import get_config_manager
from navig.remote import RemoteOperations


console = Console()


def monitor_resources(options: Dict[str, Any]) -> None:
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
    app_name = options.get('app') or config.get_active_server()
    
    if not app_name:
        console.print("[red]✗[/red] No active server. Use --app or set active server.")
        return
    
    server_config = config.load_server_config(app_name)
    remote = RemoteOperations(config)
    
    if options.get('dry_run'):
        console.print("[yellow]DRY RUN:[/yellow] Would monitor resources on", app_name)
        return
    
    console.print(f"\n[cyan]📊 Monitoring Resources:[/cyan] {app_name}\n")
    
    metrics = {}
    alerts = []
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True
    ) as progress:
        task = progress.add_task("Collecting metrics...", total=None)
        
        # CPU usage
        cpu_cmd = "top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1"
        cpu_result = remote.execute_command(cpu_cmd, server_config)
        if cpu_result.returncode == 0:
            cpu_usage = float(cpu_result.stdout.strip())
            metrics['cpu'] = round(cpu_usage, 1)
            
            if cpu_usage > 80:
                alerts.append(f"High CPU usage: {cpu_usage}%")
        
        # Memory usage
        mem_cmd = "free | grep Mem | awk '{print ($3/$2) * 100.0, $3, $2}'"
        mem_result = remote.execute_command(mem_cmd, server_config)
        if mem_result.returncode == 0:
            parts = mem_result.stdout.strip().split()
            mem_usage = float(parts[0])
            mem_used = int(parts[1])
            mem_total = int(parts[2])
            
            metrics['memory'] = {
                'usage_percent': round(mem_usage, 1),
                'used_kb': mem_used,
                'total_kb': mem_total,
                'used_mb': round(mem_used / 1024, 1),
                'total_mb': round(mem_total / 1024, 1)
            }
            
            if mem_usage > 80:
                alerts.append(f"High memory usage: {mem_usage}%")
        
        # Disk usage (root partition)
        disk_cmd = "df -h / | tail -1 | awk '{print $5, $3, $2, $4}' | sed 's/%//'"
        disk_result = remote.execute_command(disk_cmd, server_config)
        if disk_result.returncode == 0:
            parts = disk_result.stdout.strip().split()
            disk_usage = int(parts[0])
            
            metrics['disk'] = {
                'usage_percent': disk_usage,
                'used': parts[1],
                'total': parts[2],
                'available': parts[3]
            }
            
            if disk_usage > 80:
                alerts.append(f"High disk usage: {disk_usage}%")
        
        # Load average
        load_cmd = "uptime | awk -F'load average:' '{print $2}' | xargs"
        load_result = remote.execute_command(load_cmd, server_config)
        if load_result.returncode == 0:
            metrics['load_average'] = load_result.stdout.strip()
        
        # Network connections
        conn_cmd = "ss -s | grep 'TCP:' | awk '{print $2}'"
        conn_result = remote.execute_command(conn_cmd, server_config)
        if conn_result.returncode == 0:
            metrics['tcp_connections'] = conn_result.stdout.strip()
        
        # Uptime
        uptime_cmd = "uptime -p"
        uptime_result = remote.execute_command(uptime_cmd, server_config)
        if uptime_result.returncode == 0:
            metrics['uptime'] = uptime_result.stdout.strip()
    
    # Display results
    if options.get('json_output'):
        output = {
            'timestamp': datetime.now().isoformat(),
            'server': app_name,
            'metrics': metrics,
            'alerts': alerts
        }
        console.print(json.dumps(output, indent=2))
    else:
        # Create metrics table
        table = Table(title="Resource Usage", show_header=True, header_style="bold cyan")
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white")
        table.add_column("Status", style="green")
        
        # CPU row
        cpu_val = metrics.get('cpu', 0)
        cpu_status = "🔴 HIGH" if cpu_val > 80 else ("🟡 MEDIUM" if cpu_val > 60 else "🟢 OK")
        table.add_row("CPU Usage", f"{cpu_val}%", cpu_status)
        
        # Memory row
        mem = metrics.get('memory', {})
        mem_val = mem.get('usage_percent', 0)
        mem_status = "🔴 HIGH" if mem_val > 80 else ("🟡 MEDIUM" if mem_val > 60 else "🟢 OK")
        table.add_row(
            "Memory Usage",
            f"{mem_val}% ({mem.get('used_mb', 0)} MB / {mem.get('total_mb', 0)} MB)",
            mem_status
        )
        
        # Disk row
        disk = metrics.get('disk', {})
        disk_val = disk.get('usage_percent', 0)
        disk_status = "🔴 HIGH" if disk_val > 80 else ("🟡 MEDIUM" if disk_val > 60 else "🟢 OK")
        table.add_row(
            "Disk Usage",
            f"{disk_val}% ({disk.get('used', '0')} / {disk.get('total', '0')})",
            disk_status
        )
        
        # Load average
        table.add_row("Load Average", metrics.get('load_average', 'N/A'), "ℹ️ INFO")
        
        # Connections
        table.add_row("TCP Connections", metrics.get('tcp_connections', 'N/A'), "ℹ️ INFO")
        
        # Uptime
        table.add_row("Uptime", metrics.get('uptime', 'N/A'), "ℹ️ INFO")
        
        console.print(table)
        
        # Display alerts
        if alerts:
            console.print(f"\n[yellow]⚠ Alerts ({len(alerts)}):[/yellow]")
            for alert in alerts:
                console.print(f"  [yellow]•[/yellow] {alert}")
        else:
            console.print("\n[green]✓[/green] All metrics within normal range")


def monitor_disk(threshold: int, options: Dict[str, Any]) -> None:
    """
    Monitor disk space with custom threshold alerts.
    
    Args:
        threshold: Alert threshold percentage (default: 80)
        options: Command options including server config, dry_run, json_output
    """
    config = get_config_manager()
    app_name = options.get('app') or config.get_active_server()
    
    if not app_name:
        console.print("[red]✗[/red] No active server. Use --app or set active server.")
        return
    
    server_config = config.load_server_config(app_name)
    remote = RemoteOperations(config)
    
    if options.get('dry_run'):
        console.print(f"[yellow]DRY RUN:[/yellow] Would check disk space on {app_name} (threshold: {threshold}%)")
        return
    
    console.print(f"\n[cyan]💾 Disk Space Monitoring:[/cyan] {app_name}\n")
    
    # Get all disk partitions
    disk_cmd = "df -h | grep -E '^/dev/'"
    result = remote.execute_command(disk_cmd, server_config)
    
    if result.returncode != 0:
        console.print("[red]✗[/red] Failed to retrieve disk information")
        return
    
    disks = []
    alerts = []
    
    for line in result.stdout.strip().split('\n'):
        # Parse: /dev/sda1  20G  15G  4.2G  79% /
        match = re.match(r'(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d+)%\s+(\S+)', line)
        if match:
            device, size, used, available, usage, mount = match.groups()
            usage_int = int(usage)
            
            disk_info = {
                'device': device,
                'size': size,
                'used': used,
                'available': available,
                'usage_percent': usage_int,
                'mount': mount
            }
            disks.append(disk_info)
            
            if usage_int > threshold:
                alerts.append(f"{mount} is {usage_int}% full (threshold: {threshold}%)")
    
    # Display results
    if options.get('json_output'):
        output = {
            'timestamp': datetime.now().isoformat(),
            'server': app_name,
            'threshold': threshold,
            'disks': disks,
            'alerts': alerts
        }
        console.print(json.dumps(output, indent=2))
    else:
        table = Table(title=f"Disk Space (Threshold: {threshold}%)", show_header=True, header_style="bold cyan")
        table.add_column("Mount Point", style="cyan")
        table.add_column("Device", style="dim")
        table.add_column("Size", justify="right")
        table.add_column("Used", justify="right")
        table.add_column("Available", justify="right")
        table.add_column("Usage", justify="right")
        table.add_column("Status")
        
        for disk in disks:
            usage = disk['usage_percent']
            status = "🔴 ALERT" if usage > threshold else ("🟡 WARNING" if usage > threshold - 10 else "🟢 OK")
            
            table.add_row(
                disk['mount'],
                disk['device'],
                disk['size'],
                disk['used'],
                disk['available'],
                f"{usage}%",
                status
            )
        
        console.print(table)
        
        if alerts:
            console.print(f"\n[red]⚠ {len(alerts)} Alert(s):[/red]")
            for alert in alerts:
                console.print(f"  [red]•[/red] {alert}")
        else:
            console.print(f"\n[green]✓[/green] All disks below {threshold}% threshold")


def monitor_services(options: Dict[str, Any]) -> None:
    """
    Check health status of critical services.
    
    Monitors: nginx, apache2, mysql, postgresql, php-fpm, hestia, fail2ban, ufw, ssh
    
    Args:
        options: Command options including server config, dry_run, json_output
    """
    config = get_config_manager()
    app_name = options.get('app_name') or config.get_active_server()
    
    if not app_name:
        console.print("[red]✗[/red] No active server. Use --app or set active server.")
        return
    
    server_config = config.get_app_config(app_name)
    remote = RemoteOperations(server_config)
    
    if options.get('dry_run'):
        console.print(f"[yellow]DRY RUN:[/yellow] Would check services on {app_name}")
        return
    
    console.print(f"\n[cyan]🔧 Service Health Check:[/cyan] {app_name}\n")
    
    # Critical services to monitor
    services = [
        'nginx',
        'apache2',
        'mysql',
        'mariadb',
        'postgresql',
        'php8.3-fpm',
        'php8.2-fpm',
        'php8.1-fpm',
        'php-fpm',
        'hestia',
        'fail2ban',
        'ufw',
        'ssh',
        'sshd',
        'redis-server',
        'memcached'
    ]
    
    service_status = []
    inactive_services = []
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True
    ) as progress:
        task = progress.add_task("Checking services...", total=len(services))
        
        for service in services:
            status_cmd = f"systemctl is-active {service} 2>/dev/null || echo 'not-installed'"
            result = remote.execute_command(status_cmd, server_config)
            
            status = result.stdout.strip() if result.returncode == 0 else 'unknown'
            
            service_info = {
                'name': service,
                'status': status
            }
            
            if status == 'active':
                service_info['health'] = 'healthy'
            elif status == 'inactive':
                service_info['health'] = 'stopped'
                inactive_services.append(service)
            elif status == 'not-installed':
                service_info['health'] = 'not-installed'
            else:
                service_info['health'] = 'unknown'
            
            service_status.append(service_info)
            progress.update(task, advance=1)
    
    # Display results
    if options.get('json_output'):
        output = {
            'timestamp': datetime.now().isoformat(),
            'server': app_name,
            'services': service_status,
            'inactive_count': len(inactive_services)
        }
        console.print(json.dumps(output, indent=2))
    else:
        table = Table(title="Service Status", show_header=True, header_style="bold cyan")
        table.add_column("Service", style="cyan")
        table.add_column("Status", justify="center")
        table.add_column("Health", justify="center")
        
        for svc in service_status:
            status = svc['status']
            
            if status == 'active':
                status_icon = "[green]✓ active[/green]"
                health_icon = "[green]🟢 healthy[/green]"
            elif status == 'inactive':
                status_icon = "[red]✗ inactive[/red]"
                health_icon = "[red]🔴 stopped[/red]"
            elif status == 'not-installed':
                status_icon = "[dim]- not installed[/dim]"
                health_icon = "[dim]⚪ N/A[/dim]"
            else:
                status_icon = f"[yellow]? {status}[/yellow]"
                health_icon = "[yellow]🟡 unknown[/yellow]"
            
            table.add_row(svc['name'], status_icon, health_icon)
        
        console.print(table)
        
        if inactive_services:
            console.print(f"\n[yellow]⚠ {len(inactive_services)} service(s) inactive:[/yellow]")
            for svc in inactive_services:
                console.print(f"  [yellow]•[/yellow] {svc}")
        else:
            console.print("\n[green]✓[/green] All installed services are running")


def monitor_network(options: Dict[str, Any]) -> None:
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
    app_name = options.get('app_name') or config.get_active_server()
    
    if not app_name:
        console.print("[red]✗[/red] No active server. Use --app or set active server.")
        return
    
    server_config = config.get_app_config(app_name)
    remote = RemoteOperations(server_config)
    
    if options.get('dry_run'):
        console.print(f"[yellow]DRY RUN:[/yellow] Would check network stats on {app_name}")
        return
    
    console.print(f"\n[cyan]🌐 Network Statistics:[/cyan] {app_name}\n")
    
    metrics = {}
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
        transient=True
    ) as progress:
        task = progress.add_task("Collecting network stats...", total=None)
        
        # Connection summary
        conn_cmd = "ss -s"
        conn_result = remote.execute_command(conn_cmd, server_config)
        if conn_result.returncode == 0:
            metrics['connection_summary'] = conn_result.stdout.strip()
        
        # Listening ports
        listen_cmd = "ss -tuln | grep LISTEN | wc -l"
        listen_result = remote.execute_command(listen_cmd, server_config)
        if listen_result.returncode == 0:
            metrics['listening_ports'] = int(listen_result.stdout.strip())
        
        # Established connections
        estab_cmd = "ss -tn | grep ESTAB | wc -l"
        estab_result = remote.execute_command(estab_cmd, server_config)
        if estab_result.returncode == 0:
            metrics['established_connections'] = int(estab_result.stdout.strip())
        
        # Network interfaces
        iface_cmd = "ip -s link show | grep -E '^[0-9]+:' | awk '{print $2}' | sed 's/:$//' | head -5"
        iface_result = remote.execute_command(iface_cmd, server_config)
        if iface_result.returncode == 0:
            metrics['interfaces'] = iface_result.stdout.strip().split('\n')
    
    # Display results
    if options.get('json_output'):
        output = {
            'timestamp': datetime.now().isoformat(),
            'server': app_name,
            'metrics': metrics
        }
        console.print(json.dumps(output, indent=2))
    else:
        # Connection summary panel
        conn_text = metrics.get('connection_summary', 'No data')
        panel = Panel(conn_text, title="[cyan]Connection Summary[/cyan]", border_style="cyan")
        console.print(panel)
        
        # Stats table
        table = Table(show_header=False, box=None)
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="white", justify="right")
        
        table.add_row("Listening Ports", str(metrics.get('listening_ports', 0)))
        table.add_row("Established Connections", str(metrics.get('established_connections', 0)))
        
        if 'interfaces' in metrics:
            table.add_row("Network Interfaces", ', '.join(metrics['interfaces']))
        
        console.print("\n", table)


def health_check(options: Dict[str, Any]) -> None:
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
    app_name = options.get('app_name') or config.get_active_server()
    
    if not app_name:
        console.print("[red]✗[/red] No active server. Use --app or set active server.")
        return
    
    if options.get('dry_run'):
        console.print(f"[yellow]DRY RUN:[/yellow] Would run comprehensive health check on {app_name}")
        return
    
    console.print(f"\n[bold cyan]🏥 Comprehensive Health Check:[/bold cyan] {app_name}\n")
    
    # Run all monitoring checks
    console.print("[cyan]→[/cyan] Checking resources...")
    monitor_resources(options)
    
    console.print("\n[cyan]→[/cyan] Checking services...")
    monitor_services(options)
    
    console.print("\n[cyan]→[/cyan] Checking disk space...")
    monitor_disk(80, options)
    
    console.print("\n[cyan]→[/cyan] Checking network...")
    monitor_network(options)
    
    console.print("\n[green]✓[/green] Health check complete")


def generate_report(options: Dict[str, Any]) -> None:
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
    app_name = options.get('app_name') or config.get_active_server()
    
    if not app_name:
        console.print("[red]✗[/red] No active server. Use --app or set active server.")
        return
    
    server_config = config.get_app_config(app_name)
    remote = RemoteOperations(server_config)
    
    if options.get('dry_run'):
        console.print(f"[yellow]DRY RUN:[/yellow] Would generate report for {app_name}")
        return
    
    console.print(f"\n[cyan]📝 Generating Health Report:[/cyan] {app_name}\n")
    
    report = {
        'timestamp': datetime.now().isoformat(),
        'server': app_name,
        'metrics': {},
        'services': [],
        'disks': [],
        'network': {},
        'alerts': []
    }
    
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        task = progress.add_task("Collecting data...", total=4)
        
        # Collect resource metrics
        progress.update(task, description="Collecting resources...")
        temp_options = {**options, 'json_output': False}
        
        # CPU
        cpu_cmd = "top -bn1 | grep 'Cpu(s)' | awk '{print $2}' | cut -d'%' -f1"
        cpu_result = remote.execute_command(cpu_cmd, server_config)
        if cpu_result.returncode == 0:
            cpu_usage = float(cpu_result.stdout.strip())
            report['metrics']['cpu'] = round(cpu_usage, 1)
            if cpu_usage > 80:
                report['alerts'].append(f"High CPU usage: {cpu_usage}%")
        
        # Memory
        mem_cmd = "free | grep Mem | awk '{print ($3/$2) * 100.0, $3, $2}'"
        mem_result = remote.execute_command(mem_cmd, server_config)
        if mem_result.returncode == 0:
            parts = mem_result.stdout.strip().split()
            mem_usage = float(parts[0])
            report['metrics']['memory'] = {
                'usage_percent': round(mem_usage, 1),
                'used_mb': round(int(parts[1]) / 1024, 1),
                'total_mb': round(int(parts[2]) / 1024, 1)
            }
            if mem_usage > 80:
                report['alerts'].append(f"High memory usage: {mem_usage}%")
        
        # Disk
        disk_cmd = "df -h | grep -E '^/dev/'"
        disk_result = remote.execute_command(disk_cmd, server_config)
        if disk_result.returncode == 0:
            for line in disk_result.stdout.strip().split('\n'):
                match = re.match(r'(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d+)%\s+(\S+)', line)
                if match:
                    device, size, used, available, usage, mount = match.groups()
                    usage_int = int(usage)
                    report['disks'].append({
                        'device': device,
                        'mount': mount,
                        'size': size,
                        'used': used,
                        'usage_percent': usage_int
                    })
                    if usage_int > 80:
                        report['alerts'].append(f"{mount} is {usage_int}% full")
        
        progress.update(task, advance=1)
        
        # Collect service status
        progress.update(task, description="Checking services...")
        services = ['nginx', 'apache2', 'mysql', 'mariadb', 'postgresql', 'php-fpm', 'hestia', 'fail2ban']
        for service in services:
            status_cmd = f"systemctl is-active {service} 2>/dev/null || echo 'not-installed'"
            result = remote.execute_command(status_cmd, server_config)
            status = result.stdout.strip() if result.returncode == 0 else 'unknown'
            
            report['services'].append({'name': service, 'status': status})
            
            if status == 'inactive':
                report['alerts'].append(f"Service {service} is inactive")
        
        progress.update(task, advance=1)
        
        # Network stats
        progress.update(task, description="Collecting network stats...")
        estab_cmd = "ss -tn | grep ESTAB | wc -l"
        estab_result = remote.execute_command(estab_cmd, server_config)
        if estab_result.returncode == 0:
            report['network']['established_connections'] = int(estab_result.stdout.strip())
        
        progress.update(task, advance=1)
        
        # Load average
        load_cmd = "uptime | awk -F'load average:' '{print $2}' | xargs"
        load_result = remote.execute_command(load_cmd, server_config)
        if load_result.returncode == 0:
            report['metrics']['load_average'] = load_result.stdout.strip()
        
        progress.update(task, advance=1)
    
    # Save report
    reports_dir = config.base_dir / 'reports'
    reports_dir.mkdir(parents=True, exist_ok=True)
    
    timestamp_str = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_file = reports_dir / f'health-report_{app_name}_{timestamp_str}.json'
    
    with open(report_file, 'w') as f:
        json.dump(report, indent=2, fp=f)
    
    # Display summary
    console.print(f"\n[green]✓[/green] Report generated: {report_file}")
    console.print("\n[cyan]Summary:[/cyan]")
    console.print(f"  • Server: {app_name}")
    console.print(f"  • Timestamp: {report['timestamp']}")
    console.print(f"  • Alerts: {len(report['alerts'])}")
    
    if report['alerts']:
        console.print("\n[yellow]⚠ Alerts:[/yellow]")
        for alert in report['alerts']:
            console.print(f"  [yellow]•[/yellow] {alert}")
    else:
        console.print("\n[green]✓[/green] No alerts - system healthy")



