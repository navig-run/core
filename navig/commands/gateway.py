"""
NAVIG Gateway CLI Commands

Commands for managing the autonomous agent gateway server.
"""

import typer
from typing import Dict, Any

from navig.lazy_loader import lazy_import

ch = lazy_import("navig.console_helper")

gateway_app = typer.Typer(
    name="gateway",
    help="Manage the autonomous agent gateway",
    no_args_is_help=True,
)


@gateway_app.command("start")
def gateway_start(
    port: int = typer.Option(8789, "--port", "-p", help="Port to run gateway on"),
    host: str = typer.Option("0.0.0.0", "--host", help="Host to bind to"),
    background: bool = typer.Option(False, "--background", "-b", help="Run in background"),
):
    """
    Start the autonomous agent gateway server.
    
    The gateway provides:
    - HTTP/WebSocket API for agent communication
    - Session persistence across restarts
    - Heartbeat-based health monitoring
    - Cron job scheduling
    - Multi-channel message routing
    
    Examples:
        navig gateway start
        navig gateway start --port 9000
        navig gateway start --background
    """
    import asyncio
    
    ch.info(f"Starting NAVIG Gateway on {host}:{port}...")
    
    try:
        from navig.gateway import NavigGateway, GatewayConfig
        
        # Build config dict for GatewayConfig
        raw_config = {
            'gateway': {
                'enabled': True,
                'port': port,
                'host': host,
            }
        }
        
        gateway_config = GatewayConfig(raw_config)
        gateway = NavigGateway(config=gateway_config)
        
        if background:
            ch.warning("Background mode not yet implemented. Running in foreground.")
        
        asyncio.run(gateway.start())
        
    except KeyboardInterrupt:
        ch.info("Gateway stopped by user")
    except ImportError as e:
        ch.error(f"Missing dependency: {e}")
        ch.info("Install with: pip install aiohttp")
    except Exception as e:
        ch.error(f"Gateway error: {e}")


@gateway_app.command("stop")
def gateway_stop():
    """
    Stop the running gateway server.
    
    Sends a shutdown signal to the running gateway via its API.
    If the gateway is running in the foreground, use Ctrl+C instead.
    
    Examples:
        navig gateway stop
    """
    try:
        import requests
        
        # First check if gateway is running
        try:
            health_response = requests.get("http://localhost:8789/health", timeout=2)
            if health_response.status_code != 200:
                ch.warning("Gateway does not appear to be running")
                return
        except Exception:
            ch.warning("Gateway is not running")
            return
        
        # Try to stop via API
        try:
            response = requests.post("http://localhost:8789/shutdown", timeout=5)
            if response.status_code == 200:
                ch.success("Gateway shutdown signal sent")
            else:
                ch.warning(f"Shutdown request returned status {response.status_code}")
                ch.info("If running in foreground, use Ctrl+C to stop")
        except requests.exceptions.ConnectionError:
            # Connection closed - gateway probably stopped
            ch.success("Gateway stopped")
        except Exception as e:
            ch.warning(f"Could not send shutdown signal: {e}")
            ch.info("If running in foreground, use Ctrl+C to stop")
            ch.info("Or kill the process manually: pkill -f 'navig gateway'")
            
    except ImportError:
        ch.error("Missing dependency: requests")
        ch.info("Install with: pip install requests")


@gateway_app.command("status")
def gateway_status():
    """Show gateway status."""
    try:
        import requests
        response = requests.get("http://localhost:8789/health", timeout=2)
        if response.status_code == 200:
            data = response.json()
            ch.success("Gateway is running")
            ch.info(f"  Status: {data.get('status', 'unknown')}")
            ch.info(f"  Uptime: {data.get('uptime', 'unknown')}")
        else:
            ch.warning(f"Gateway returned status {response.status_code}")
    except ImportError:
        ch.error("Missing dependency: requests")
        ch.info("Install with: pip install requests")
    except Exception as e:
        if "ConnectionError" in str(type(e).__name__) or "Connection refused" in str(e):
            ch.warning("Gateway is not running")
            ch.info("Start with: navig gateway start")
        else:
            ch.error(f"Error checking gateway: {e}")


@gateway_app.command("session")
def gateway_session(
    action: str = typer.Argument("list", help="Action: list, show, clear"),
    session_key: str = typer.Argument(None, help="Session key (for show/clear)"),
):
    """
    Manage gateway sessions.
    
    Examples:
        navig gateway session list
        navig gateway session show agent:default:telegram:123
        navig gateway session clear agent:default:telegram:123
    """
    try:
        import requests
        
        if action == "list":
            response = requests.get("http://localhost:8789/sessions", timeout=5)
            if response.status_code == 200:
                sessions = response.json().get("sessions", [])
                if sessions:
                    ch.info(f"Active sessions ({len(sessions)}):")
                    for s in sessions:
                        ch.info(f"  • {s.get('key', 'unknown')}")
                else:
                    ch.info("No active sessions")
            else:
                ch.error(f"Failed to list sessions: {response.status_code}")
                
        elif action == "show" and session_key:
            response = requests.get(
                f"http://localhost:8789/sessions/{session_key}", 
                timeout=5
            )
            if response.status_code == 200:
                session = response.json()
                ch.info(f"Session: {session_key}")
                ch.console.print_json(data=session)
            else:
                ch.error(f"Session not found: {session_key}")
                
        elif action == "clear" and session_key:
            response = requests.delete(
                f"http://localhost:8789/sessions/{session_key}",
                timeout=5
            )
            if response.status_code == 200:
                ch.success(f"Session cleared: {session_key}")
            else:
                ch.error(f"Failed to clear session: {response.status_code}")
        else:
            ch.error("Invalid action or missing session_key")
            ch.info("Usage: navig gateway session list|show|clear [session_key]")
            
    except ImportError:
        ch.error("Missing dependency: requests")
        ch.info("Install with: pip install requests")
    except Exception as e:
        if "ConnectionError" in str(type(e).__name__) or "Connection refused" in str(e):
            ch.error("Gateway is not running. Start with: navig gateway start")
        else:
            ch.error(f"Error: {e}")


# ============================================================================
# Interactive Menu Wrapper Functions
# ============================================================================
# These functions provide a consistent interface for the interactive menu system.
# Each wrapper calls the underlying Typer command with appropriate defaults.

def status_cmd(ctx: Dict[str, Any]) -> None:
    """Wrapper for gateway status command (interactive menu)."""
    gateway_status()


def start_cmd(ctx: Dict[str, Any]) -> None:
    """Wrapper for gateway start command (interactive menu)."""
    # Start in foreground mode for interactive use
    gateway_start(port=8789, host="0.0.0.0", background=False)


def stop_cmd(ctx: Dict[str, Any]) -> None:
    """Wrapper for gateway stop command (interactive menu)."""
    gateway_stop()


def session_cmd(ctx: Dict[str, Any]) -> None:
    """Wrapper for gateway session list command (interactive menu)."""
    gateway_session(action="list", session_key=None)
