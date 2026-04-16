"""
NAVIG Bridge CLI Commands

One-button connection between VS Code (Bridge) extension and NAVIG daemon.
Generates auth token, configures gateway, and outputs connection details.
"""

import secrets

import typer

from navig._daemon_defaults import _DAEMON_PORT
from navig.core.yaml_io import safe_load_yaml
from navig.lazy_loader import lazy_import
from navig.platform.paths import config_dir

ch = lazy_import("navig.console_helper")

bridge_app = typer.Typer(
    name="bridge",
    help="VS Code extension ↔ daemon connection management",
    no_args_is_help=True,
)


@bridge_app.command("connect")
def bridge_connect(
    port: int = typer.Option(_DAEMON_PORT, "--port", "-p", help="Gateway port to forward"),
    bind: str = typer.Option(
        "0.0.0.0",
        "--bind",
        help="Gateway bind address (0.0.0.0 = all interfaces, 127.0.0.1 = localhost only)",
    ),
    generate_token: bool = typer.Option(
        True, "--token/--no-token", help="Generate a new auth token"
    ),
    json_output: bool = typer.Option(False, "--json", help="Output JSON for programmatic use"),
):
    """
    Set up Bridge ↔ Daemon connection in one step.

    This command:
    1. Generates a secure auth token
    2. Writes it to the gateway config (~/.navig/config.yaml)
    3. Binds gateway to 127.0.0.1 (secure, SSH-tunnel-only access)
    4. Outputs the VS Code settings you need

    Examples:
        navig bridge connect
        navig bridge connect --port 8789
        navig bridge connect --json
    """

    config_path = config_dir() / "config.yaml"

    # Load or create config
    if config_path.exists():
        cfg = safe_load_yaml(config_path) or {}
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        cfg = {}

    # Generate token
    token = ""
    if generate_token:
        token = secrets.token_urlsafe(32)
    else:
        # Read existing token
        token = cfg.get("gateway", {}).get("auth", {}).get("token", "")

    # Update config
    gw = cfg.setdefault("gateway", {})
    gw["enabled"] = True
    gw["port"] = port
    gw["host"] = bind
    gw.setdefault("auth", {})["token"] = token

    # Write config
    from navig.core.yaml_io import atomic_write_yaml

    atomic_write_yaml(cfg, config_path)

    if json_output:
        import json

        print(
            json.dumps(
                {
                    "token": token,
                    "port": port,
                    "bind": bind,
                    "gateway_url": f"http://127.0.0.1:{port}",
                    "config_path": str(config_path),
                    "vscode_settings": {
                        "navig-bridge.chat.remoteDaemonUrl": f"http://127.0.0.1:{port}",
                        "navig-bridge.chat.defaultBackend": "ubuntu-navig",
                    },
                }
            )
        )
    else:
        ch.success("Bridge connection configured!")
        ch.info(f"  Config:  {config_path}")
        ch.info(f"  Gateway: {bind}:{port}")
        ch.info(f"  Token:   {token[:8]}...{token[-4:]}")
        ch.info("")
        ch.info("Next steps:")
        ch.info("  1. Restart the daemon to pick up the new token")
        ch.info("  2. On your Windows machine, start an SSH tunnel:")
        ch.info(f"     ssh -L {port}:127.0.0.1:{port} <user>@<host>")
        ch.info("  3. In VS Code, run: NAVIG Bridge: Connect to Daemon")
        ch.info("     Or manually set these VS Code settings:")
        ch.info(f"       navig-bridge.chat.remoteDaemonUrl = http://127.0.0.1:{port}")
        ch.info("       navig-bridge.chat.defaultBackend  = ubuntu-navig")
        ch.info("  4. Store the token securely in VS Code SecretStorage")
        ch.info("     (the Connect to Daemon command does this automatically)")
        ch.info("")
        ch.info("  Full token (copy for manual setup):")
        ch.info(f"    {token}")


@bridge_app.command("status")
def bridge_status(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """
    Show current Bridge connection configuration.

    Examples:
        navig bridge status
        navig bridge status --json
    """

    config_path = config_dir() / "config.yaml"

    if not config_path.exists():
        ch.warning("No NAVIG config found. Run 'navig bridge connect' first.")
        return

    cfg = safe_load_yaml(config_path) or {}
    gw = cfg.get("gateway", {})

    enabled = gw.get("enabled", False)
    port = gw.get("port", 8789)
    host = gw.get("host", "127.0.0.1")
    token = gw.get("auth", {}).get("token", "")

    if json_output:
        import json

        print(
            json.dumps(
                {
                    "gateway_enabled": enabled,
                    "port": port,
                    "bind": host,
                    "auth_configured": bool(token),
                    "secure_bind": host == "127.0.0.1",
                }
            )
        )
    else:
        status_icon = "🟢" if enabled else "🔴"
        secure_icon = "🔒" if host == "127.0.0.1" else "⚠️"
        auth_icon = "🔑" if token else "🔓"

        ch.info(f"  {status_icon} Gateway: {'enabled' if enabled else 'disabled'}")
        ch.info(f"  {secure_icon} Bind:    {host}:{port}")
        ch.info(f"  {auth_icon} Auth:    {'configured' if token else 'NONE (open access!)'}")

        if host != "127.0.0.1":
            ch.warning("  Gateway is bound to a non-localhost address!")
            ch.warning("  Run 'navig bridge connect' to fix this (binds to 127.0.0.1).")


@bridge_app.command("rotate-token")
def bridge_rotate_token(
    json_output: bool = typer.Option(False, "--json", help="Output JSON"),
):
    """
    Rotate the gateway auth token.

    Generates a new token and updates the config. You'll need to
    update the VS Code extension with the new token afterward.

    Examples:
        navig bridge rotate-token
        navig bridge rotate-token --json
    """

    config_path = config_dir() / "config.yaml"

    if not config_path.exists():
        ch.error("No NAVIG config found. Run 'navig bridge connect' first.")
        raise typer.Exit(1)

    cfg = safe_load_yaml(config_path) or {}

    new_token = secrets.token_urlsafe(32)
    cfg.setdefault("gateway", {}).setdefault("auth", {})["token"] = new_token

    from navig.core.yaml_io import atomic_write_yaml

    atomic_write_yaml(cfg, config_path)

    if json_output:
        import json

        print(json.dumps({"token": new_token}))
    else:
        ch.success("Token rotated!")
        ch.info(f"  New token: {new_token}")
        ch.info("")
        ch.info("  Restart the daemon and reconnect VS Code:")
        ch.info("    navig daemon restart")
        ch.info("    In VS Code → NAVIG Bridge: Connect to Daemon")
