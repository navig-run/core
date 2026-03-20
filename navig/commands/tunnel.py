"""
Tunnel Management Commands

Encrypted channels. The Schema's preferred method.
"""

from typing import Any, Dict

from navig import console_helper as ch
from navig.config import get_config_manager
from navig.tunnel import TunnelManager

config_manager = get_config_manager()
tunnel_manager = TunnelManager(config_manager)


def start_tunnel(options: Dict[str, Any]):
    """Start SSH tunnel for active server."""
    server_name = options.get('app') or config_manager.get_active_server()

    if not server_name:
        ch.error("No active server. Use 'navig server use <name>' first.")
        return

    if options.get('verbose'):
        ch.dim(f"Starting tunnel for: {server_name}")

    try:
        tunnel_info = tunnel_manager.start_tunnel(server_name)

        if not options.get('quiet'):
            ch.success("✓ Encrypted channel established")
            ch.info(f"  Server: {tunnel_info['server']}")
            ch.info(f"  Local Port: 127.0.0.1:{tunnel_info['local_port']}")
            ch.info(f"  PID: {tunnel_info['pid']}")
            ch.dim("The void sees nothing.")

    except Exception as e:
        ch.error(f"✗ Tunnel collapsed: {e}")
        ch.info("")
        ch.info("Recovery steps:")
        ch.info("  1. Check tunnel status: navig tunnel status")
        ch.info("  2. Restart tunnel: navig tunnel restart")
        ch.info("  3. Check for zombie processes: ps aux | grep ssh")
        ch.info("  4. Verify SSH connection: ssh user@host 'echo test'")
        ch.info("  5. Check server logs: navig logs ssh")
        if options.get('verbose'):
            import traceback
            ch.raw_print(traceback.format_exc())


def stop_tunnel(options: Dict[str, Any]):
    """Stop SSH tunnel."""
    server_name = options.get('app') or config_manager.get_active_server()

    if not server_name:
        ch.error("No active server.")
        return

    try:
        success = tunnel_manager.stop_tunnel(server_name)

        if success:
            if not options.get('quiet'):
                ch.success("Connection severed")
        else:
            ch.warning("No tunnel was running")

    except Exception as e:
        ch.error(f"Error: {e}")


def restart_tunnel(options: Dict[str, Any]):
    """Restart tunnel."""
    server_name = options.get('app') or config_manager.get_active_server()

    if not server_name:
        ch.error("No active server.")
        return

    try:
        ch.info("Restarting tunnel...")
        tunnel_info = tunnel_manager.restart_tunnel(server_name)

        if not options.get('quiet'):
            ch.success("Tunnel reestablished")
            ch.info(f"  Local Port: 127.0.0.1:{tunnel_info['local_port']}")

    except Exception as e:
        ch.error(f"Error: {e}")


def show_tunnel_status(options: Dict[str, Any]):
    """Show tunnel status."""
    server_name = options.get('app') or config_manager.get_active_server()

    if not server_name:
        ch.error("No active server.")
        return

    tunnel_info = tunnel_manager.get_tunnel_status(server_name)

    if options.get('json'):
        import json
        ch.raw_print(
            json.dumps(
                {
                    "schema_version": "1.0.0",
                    "command": "tunnel.show",
                    "success": True,
                    "server": server_name,
                    "running": bool(tunnel_info),
                    "tunnel": tunnel_info,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return

    if options.get('raw') or options.get('plain'):
        # Plain text output for scripting
        ch.raw_print("running" if tunnel_info else "stopped")
        return

    if not tunnel_info:
        ch.warning(f"No tunnel running for: {server_name}")
        return

    # Use console_helper's print_tunnel_status function
    ch.print_tunnel_status(tunnel_info, server_name)

    # Test connection
    if tunnel_manager._test_port(tunnel_info['local_port']):
        ch.success("Port is accessible")
    else:
        ch.error("Port test failed")


def auto_tunnel(options: Dict[str, Any]):
    """
    Check tunnel health and auto-recover if needed.
    
    This command verifies the tunnel is healthy and attempts recovery if not.
    For programmatic auto tunnel management, use TunnelManager.auto_tunnel() context manager.
    """
    server_name = options.get('app') or config_manager.get_active_server()

    if not server_name:
        ch.error("No active server.")
        return

    # Check health
    health = tunnel_manager.check_tunnel_health(server_name)

    if health['is_healthy']:
        ch.success("Tunnel is healthy")
        if not options.get('quiet'):
            ch.info(f"  Server: {server_name}")
            ch.info(f"  Port: 127.0.0.1:{health['tunnel_info']['local_port']}")
            ch.info(f"  PID: {health['tunnel_info']['pid']}")
        return

    # Unhealthy - attempt recovery
    ch.warning(f"Tunnel health issues detected: {', '.join(health['issues'])}")
    ch.info("Attempting automatic recovery...")

    result = tunnel_manager.recover_tunnel(server_name)

    if result['recovered']:
        ch.success("✓ Tunnel recovered successfully")
        if not options.get('quiet'):
            info = result['tunnel_info']
            ch.info(f"  Port: 127.0.0.1:{info['local_port']}")
            ch.info(f"  PID: {info['pid']}")
    else:
        ch.error(f"✗ Recovery failed: {result['message']}")
        ch.info("Try manually restarting: navig tunnel restart")


