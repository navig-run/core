"""CLI registrations for tunnel, monitor, and security commands.

Extracted from `navig.cli.__init__` to keep the root CLI module focused on
bootstrap and routing while preserving legacy aliases and command behavior.
"""

from __future__ import annotations

import typer

from navig.cli._callbacks import show_subcommand_help
from navig.deprecation import deprecation_warning
from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")


def register_host_infra_commands(app: typer.Typer) -> None:
    """Register tunnel, monitor, and security command groups on the root app."""

    tunnel_app = typer.Typer(
        help="Manage SSH tunnels",
        invoke_without_command=True,
        no_args_is_help=False,
    )
    app.add_typer(tunnel_app, name="tunnel")
    app.add_typer(tunnel_app, name="t", hidden=True)

    @tunnel_app.callback()
    def tunnel_callback(ctx: typer.Context):
        """Tunnel management - run without subcommand for help."""
        if ctx.invoked_subcommand is None:
            show_subcommand_help("tunnel", ctx)
            raise typer.Exit()

    @tunnel_app.command("run")
    def tunnel_run(ctx: typer.Context):
        """Start SSH tunnel for active server (canonical command)."""
        from navig.commands.tunnel import start_tunnel

        start_tunnel(ctx.obj)

    @tunnel_app.command("start", hidden=True)
    def tunnel_start(ctx: typer.Context):
        """[DEPRECATED: Use 'navig tunnel run'] Start SSH tunnel."""
        deprecation_warning("navig tunnel start", "navig tunnel run")
        from navig.commands.tunnel import start_tunnel

        start_tunnel(ctx.obj)

    @tunnel_app.command("remove")
    def tunnel_remove(ctx: typer.Context):
        """Stop and remove SSH tunnel (canonical command)."""
        from navig.commands.tunnel import stop_tunnel

        stop_tunnel(ctx.obj)

    @tunnel_app.command("stop", hidden=True)
    def tunnel_stop(ctx: typer.Context):
        """[DEPRECATED: Use 'navig tunnel remove'] Stop SSH tunnel."""
        deprecation_warning("navig tunnel stop", "navig tunnel remove")
        from navig.commands.tunnel import stop_tunnel

        stop_tunnel(ctx.obj)

    @tunnel_app.command("update")
    def tunnel_update(ctx: typer.Context):
        """Restart tunnel (canonical command)."""
        from navig.commands.tunnel import restart_tunnel

        restart_tunnel(ctx.obj)

    @tunnel_app.command("restart", hidden=True)
    def tunnel_restart(ctx: typer.Context):
        """[DEPRECATED: Use 'navig tunnel update'] Restart tunnel."""
        deprecation_warning("navig tunnel restart", "navig tunnel update")
        from navig.commands.tunnel import restart_tunnel

        restart_tunnel(ctx.obj)

    @tunnel_app.command("show")
    def tunnel_show(
        ctx: typer.Context,
        plain: bool = typer.Option(
            False, "--plain", help="Output plain text for scripting"
        ),
        json: bool = typer.Option(False, "--json", help="Output JSON"),
    ):
        """Show tunnel status (canonical command)."""
        from navig.commands.tunnel import show_tunnel_status

        ctx.obj["plain"] = plain
        if json:
            ctx.obj["json"] = True
        show_tunnel_status(ctx.obj)

    @tunnel_app.command("status", hidden=True)
    def tunnel_status(
        ctx: typer.Context,
        plain: bool = typer.Option(
            False, "--plain", help="Output plain text (running/stopped) for scripting"
        ),
    ):
        """[DEPRECATED: Use 'navig tunnel show'] Show tunnel status."""
        deprecation_warning("navig tunnel status", "navig tunnel show")
        from navig.commands.tunnel import show_tunnel_status

        ctx.obj["plain"] = plain
        show_tunnel_status(ctx.obj)

    @tunnel_app.command("auto")
    def tunnel_auto(ctx: typer.Context):
        """Auto-start tunnel if needed, auto-stop when done."""
        from navig.commands.tunnel import auto_tunnel

        auto_tunnel(ctx.obj)

    monitor_app = typer.Typer(
        help="[DEPRECATED: Use 'navig host monitor'] Server monitoring",
        invoke_without_command=True,
        no_args_is_help=False,
        deprecated=True,
    )
    app.add_typer(monitor_app, name="monitor", hidden=True)

    @monitor_app.callback()
    def monitor_callback(ctx: typer.Context):
        """[DEPRECATED] Use 'navig host monitor' instead."""
        deprecation_warning("navig monitor", "navig host monitor")
        if ctx.invoked_subcommand is None:
            from navig.commands.interactive import launch_monitoring_menu

            launch_monitoring_menu()
            raise typer.Exit()

    @monitor_app.command("show")
    def monitor_show(
        ctx: typer.Context,
        resources: bool = typer.Option(
            False, "--resources", "-r", help="Show resource usage"
        ),
        disk: bool = typer.Option(False, "--disk", "-d", help="Show disk space"),
        services: bool = typer.Option(
            False, "--services", "-s", help="Show service status"
        ),
        network: bool = typer.Option(
            False, "--network", "-n", help="Show network stats"
        ),
        threshold: int = typer.Option(
            80, "--threshold", "-t", help="Alert threshold percentage"
        ),
    ):
        """Show monitoring information (canonical command)."""
        if resources:
            from navig.commands.monitoring import monitor_resources

            monitor_resources(ctx.obj)
        elif disk:
            from navig.commands.monitoring import monitor_disk

            monitor_disk(threshold, ctx.obj)
        elif services:
            from navig.commands.monitoring import monitor_services

            monitor_services(ctx.obj)
        elif network:
            from navig.commands.monitoring import monitor_network

            monitor_network(ctx.obj)
        else:
            from navig.commands.monitoring import health_check

            health_check(ctx.obj)

    @monitor_app.command("run")
    def monitor_run(
        ctx: typer.Context,
        report: bool = typer.Option(False, "--report", help="Generate and save report"),
    ):
        """Run monitoring checks (canonical command)."""
        if report:
            from navig.commands.monitoring import generate_report

            generate_report(ctx.obj)
        else:
            from navig.commands.monitoring import health_check

            health_check(ctx.obj)

    @monitor_app.command("resources", hidden=True)
    def monitor_resources_new(ctx: typer.Context):
        """[DEPRECATED: Use 'navig monitor show --resources'] Monitor resources."""
        deprecation_warning("navig monitor resources", "navig monitor show --resources")
        from navig.commands.monitoring import monitor_resources

        monitor_resources(ctx.obj)

    @monitor_app.command("disk", hidden=True)
    def monitor_disk_new(
        ctx: typer.Context,
        threshold: int = typer.Option(
            80, "--threshold", "-t", help="Alert threshold percentage"
        ),
    ):
        """[DEPRECATED: Use 'navig monitor show --disk'] Monitor disk space."""
        deprecation_warning("navig monitor disk", "navig monitor show --disk")
        from navig.commands.monitoring import monitor_disk

        monitor_disk(threshold, ctx.obj)

    @monitor_app.command("services", hidden=True)
    def monitor_services_new(ctx: typer.Context):
        """[DEPRECATED: Use 'navig monitor show --services'] Check service status."""
        deprecation_warning("navig monitor services", "navig monitor show --services")
        from navig.commands.monitoring import monitor_services

        monitor_services(ctx.obj)

    @monitor_app.command("network", hidden=True)
    def monitor_network_new(ctx: typer.Context):
        """[DEPRECATED: Use 'navig monitor show --network'] Monitor network."""
        deprecation_warning("navig monitor network", "navig monitor show --network")
        from navig.commands.monitoring import monitor_network

        monitor_network(ctx.obj)

    @monitor_app.command("health", hidden=True)
    def monitor_health_new(ctx: typer.Context):
        """[DEPRECATED: Use 'navig monitor show'] Health check."""
        deprecation_warning("navig monitor health", "navig monitor show")
        from navig.commands.monitoring import health_check

        health_check(ctx.obj)

    @monitor_app.command("report")
    def monitor_report_new(ctx: typer.Context):
        """Generate comprehensive monitoring report and save to file."""
        from navig.commands.monitoring import generate_report

        generate_report(ctx.obj)

    @app.command("monitor-resources", hidden=True)
    def monitor_resources_cmd(ctx: typer.Context):
        """[DEPRECATED: Use 'navig monitor resources']"""
        ch.warning(
            "'navig monitor-resources' is deprecated. Use 'navig monitor resources' instead."
        )
        from navig.commands.monitoring import monitor_resources

        monitor_resources(ctx.obj)

    @app.command("monitor-disk", hidden=True)
    def monitor_disk_cmd(
        ctx: typer.Context,
        threshold: int = typer.Option(
            80, "--threshold", "-t", help="Alert threshold percentage"
        ),
    ):
        """[DEPRECATED: Use 'navig monitor disk']"""
        ch.warning(
            "'navig monitor-disk' is deprecated. Use 'navig monitor disk' instead."
        )
        from navig.commands.monitoring import monitor_disk

        monitor_disk(threshold, ctx.obj)

    @app.command("monitor-services", hidden=True)
    def monitor_services_cmd(ctx: typer.Context):
        """[DEPRECATED: Use 'navig monitor services']"""
        ch.warning(
            "'navig monitor-services' is deprecated. Use 'navig monitor services' instead."
        )
        from navig.commands.monitoring import monitor_services

        monitor_services(ctx.obj)

    @app.command("monitor-network", hidden=True)
    def monitor_network_cmd(ctx: typer.Context):
        """[DEPRECATED: Use 'navig monitor network']"""
        ch.warning(
            "'navig monitor-network' is deprecated. Use 'navig monitor network' instead."
        )
        from navig.commands.monitoring import monitor_network

        monitor_network(ctx.obj)

    @app.command("health-check", hidden=True)
    def health_check_cmd(ctx: typer.Context):
        """[DEPRECATED: Use 'navig monitor health']"""
        ch.warning(
            "'navig health-check' is deprecated. Use 'navig monitor health' instead."
        )
        from navig.commands.monitoring import health_check

        health_check(ctx.obj)

    @app.command("monitoring-report", hidden=True)
    def monitoring_report_cmd(ctx: typer.Context):
        """[DEPRECATED: Use 'navig monitor report']"""
        ch.warning(
            "'navig monitoring-report' is deprecated. Use 'navig monitor report' instead."
        )
        from navig.commands.monitoring import generate_report

        generate_report(ctx.obj)

    security_app = typer.Typer(
        help="[DEPRECATED: Use 'navig host security'] Security management",
        invoke_without_command=True,
        no_args_is_help=False,
    )
    app.add_typer(security_app, name="security", hidden=True)

    @security_app.callback()
    def security_callback(ctx: typer.Context):
        """Security management - DEPRECATED, use 'navig host security'."""
        deprecation_warning("navig security", "navig host security")
        if ctx.invoked_subcommand is None:
            from navig.commands.interactive import launch_security_menu

            launch_security_menu()
            raise typer.Exit()

    @security_app.command("show")
    def security_show(
        ctx: typer.Context,
        firewall: bool = typer.Option(
            False, "--firewall", "-f", help="Show firewall status"
        ),
        fail2ban: bool = typer.Option(
            False, "--fail2ban", "-b", help="Show fail2ban status"
        ),
        ssh: bool = typer.Option(False, "--ssh", "-s", help="Show SSH audit"),
        updates: bool = typer.Option(
            False, "--updates", "-u", help="Show security updates"
        ),
        connections: bool = typer.Option(
            False, "--connections", "-c", help="Show network connections"
        ),
    ):
        """Show security information (canonical command)."""
        if firewall:
            from navig.commands.security import firewall_status

            firewall_status(ctx.obj)
        elif fail2ban:
            from navig.commands.security import fail2ban_status

            fail2ban_status(ctx.obj)
        elif ssh:
            from navig.commands.security import ssh_audit

            ssh_audit(ctx.obj)
        elif updates:
            from navig.commands.security import check_security_updates

            check_security_updates(ctx.obj)
        elif connections:
            from navig.commands.security import audit_connections

            audit_connections(ctx.obj)
        else:
            from navig.commands.security import security_scan

            security_scan(ctx.obj)

    @security_app.command("run")
    def security_run(ctx: typer.Context):
        """Run comprehensive security scan (canonical command)."""
        from navig.commands.security import security_scan

        security_scan(ctx.obj)

    @security_app.command("firewall", hidden=True)
    def security_firewall_new(ctx: typer.Context):
        """Display UFW firewall status and rules."""
        from navig.commands.security import firewall_status

        firewall_status(ctx.obj)

    @security_app.command("firewall-add")
    def security_firewall_add_new(
        ctx: typer.Context,
        port: int = typer.Argument(..., help="Port number"),
        protocol: str = typer.Option(
            "tcp", "--protocol", "-p", help="Protocol (tcp/udp)"
        ),
        allow_from: str = typer.Option(
            "any", "--from", help="IP address or subnet (default: any)"
        ),
    ):
        """Add UFW firewall rule."""
        from navig.commands.security import firewall_add_rule

        firewall_add_rule(port, protocol, allow_from, ctx.obj)

    @security_app.command("edit")
    def security_edit(
        ctx: typer.Context,
        firewall: bool = typer.Option(
            False, "--firewall", "-f", help="Edit firewall rules"
        ),
        port: int | None = typer.Option(None, "--port", "-p", help="Port number"),
        protocol: str = typer.Option("tcp", "--protocol", help="Protocol (tcp/udp)"),
        allow_from: str = typer.Option("any", "--from", help="IP address or subnet"),
        add: bool = typer.Option(False, "--add", help="Add a rule"),
        remove: bool = typer.Option(False, "--remove", "-r", help="Remove a rule"),
        enable: bool = typer.Option(False, "--enable", help="Enable firewall"),
        disable: bool = typer.Option(False, "--disable", help="Disable firewall"),
        unban: str | None = typer.Option(
            None, "--unban", help="Unban IP address from fail2ban"
        ),
        jail: str | None = typer.Option(
            None, "--jail", "-j", help="Jail name for fail2ban"
        ),
    ):
        """Edit security settings (canonical command)."""
        if firewall:
            if enable:
                from navig.commands.security import firewall_enable

                firewall_enable(ctx.obj)
            elif disable:
                from navig.commands.security import firewall_disable

                firewall_disable(ctx.obj)
            elif add and port:
                from navig.commands.security import firewall_add_rule

                firewall_add_rule(port, protocol, allow_from, ctx.obj)
            elif remove and port:
                from navig.commands.security import firewall_remove_rule

                firewall_remove_rule(port, protocol, ctx.obj)
        elif unban:
            from navig.commands.security import fail2ban_unban

            fail2ban_unban(unban, jail, ctx.obj)
        else:
            from navig.console_helper import ch as console_ch

            console_ch.error("Specify what to edit: --firewall or --unban")

    @security_app.command("firewall-remove", hidden=True)
    def security_firewall_remove_new(
        ctx: typer.Context,
        port: int = typer.Argument(..., help="Port number"),
        protocol: str = typer.Option(
            "tcp", "--protocol", "-p", help="Protocol (tcp/udp)"
        ),
    ):
        """Remove UFW firewall rule."""
        from navig.commands.security import firewall_remove_rule

        firewall_remove_rule(port, protocol, ctx.obj)

    @security_app.command("firewall-enable")
    def security_firewall_enable_new(ctx: typer.Context):
        """Enable UFW firewall."""
        from navig.commands.security import firewall_enable

        firewall_enable(ctx.obj)

    @security_app.command("firewall-disable")
    def security_firewall_disable_new(ctx: typer.Context):
        """Disable UFW firewall."""
        from navig.commands.security import firewall_disable

        firewall_disable(ctx.obj)

    @security_app.command("fail2ban", hidden=True)
    def security_fail2ban_new(ctx: typer.Context):
        """[DEPRECATED: Use 'navig security show --fail2ban'] Show Fail2Ban status."""
        deprecation_warning("navig security fail2ban", "navig security show --fail2ban")
        from navig.commands.security import fail2ban_status

        fail2ban_status(ctx.obj)

    @security_app.command("unban", hidden=True)
    def security_unban_new(
        ctx: typer.Context,
        ip_address: str = typer.Argument(..., help="IP address to unban"),
        jail: str = typer.Option(
            None, "--jail", "-j", help="Jail name (default: all jails)"
        ),
    ):
        """[DEPRECATED: Use 'navig security edit --unban <ip>'] Unban IP."""
        deprecation_warning("navig security unban", "navig security edit --unban <ip>")
        from navig.commands.security import fail2ban_unban

        fail2ban_unban(ip_address, jail, ctx.obj)

    @security_app.command("ssh", hidden=True)
    def security_ssh_new(ctx: typer.Context):
        """[DEPRECATED: Use 'navig security show --ssh'] SSH audit."""
        deprecation_warning("navig security ssh", "navig security show --ssh")
        from navig.commands.security import ssh_audit

        ssh_audit(ctx.obj)

    @security_app.command("updates")
    def security_updates_new(ctx: typer.Context):
        """Check for available security updates."""
        from navig.commands.security import check_security_updates

        check_security_updates(ctx.obj)

    @security_app.command("connections")
    def security_connections_new(ctx: typer.Context):
        """Audit active network connections."""
        from navig.commands.security import audit_connections

        audit_connections(ctx.obj)

    @security_app.command("scan")
    def security_scan_new(ctx: typer.Context):
        """Run comprehensive security scan."""
        from navig.commands.security import security_scan

        security_scan(ctx.obj)

    @app.command("firewall-status", hidden=True)
    def firewall_status_cmd(ctx: typer.Context):
        """[DEPRECATED: Use 'navig security firewall']"""
        ch.warning(
            "'navig firewall-status' is deprecated. Use 'navig security firewall' instead."
        )
        from navig.commands.security import firewall_status

        firewall_status(ctx.obj)

    @app.command("firewall-add", hidden=True)
    def firewall_add_cmd(
        port: int = typer.Argument(..., help="Port number"),
        protocol: str = typer.Option(
            "tcp", "--protocol", "-p", help="Protocol (tcp/udp)"
        ),
        allow_from: str = typer.Option(
            "any", "--from", help="IP address or subnet (default: any)"
        ),
        ctx: typer.Context = typer.Context,
    ):
        """[DEPRECATED: Use 'navig security firewall-add']"""
        ch.warning(
            "'navig firewall-add' is deprecated. Use 'navig security firewall-add' instead."
        )
        from navig.commands.security import firewall_add_rule

        firewall_add_rule(port, protocol, allow_from, ctx.obj)

    @app.command("firewall-remove", hidden=True)
    def firewall_remove_cmd(
        port: int = typer.Argument(..., help="Port number"),
        protocol: str = typer.Option(
            "tcp", "--protocol", "-p", help="Protocol (tcp/udp)"
        ),
        ctx: typer.Context = typer.Context,
    ):
        """[DEPRECATED: Use 'navig security firewall-remove']"""
        ch.warning(
            "'navig firewall-remove' is deprecated. Use 'navig security firewall-remove' instead."
        )
        from navig.commands.security import firewall_remove_rule

        firewall_remove_rule(port, protocol, ctx.obj)

    @app.command("fail2ban-status", hidden=True)
    def fail2ban_status_cmd(ctx: typer.Context):
        """[DEPRECATED: Use 'navig security fail2ban']"""
        ch.warning(
            "'navig fail2ban-status' is deprecated. Use 'navig security fail2ban' instead."
        )
        from navig.commands.security import fail2ban_status

        fail2ban_status(ctx.obj)

    @app.command("security-scan", hidden=True)
    def security_scan_cmd(ctx: typer.Context):
        """[DEPRECATED: Use 'navig security scan']"""
        ch.warning(
            "'navig security-scan' is deprecated. Use 'navig security scan' instead."
        )
        from navig.commands.security import security_scan

        security_scan(ctx.obj)
