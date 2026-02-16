"""
NAVIG Hello Plugin - Commands

This file contains the actual CLI commands for the plugin.
Commands are registered on the app instance from plugin.py.
"""

import typer
from typing import Optional
from navig.plugins.hello.plugin import app
from navig import console_helper as ch


@app.command("greet")
def greet(
    name: str = typer.Option("World", "--name", "-n", help="Name to greet"),
    shout: bool = typer.Option(False, "--shout", "-s", help="SHOUT the greeting"),
):
    """
    Say hello to someone.
    
    Example:
        navig hello greet
        navig hello greet --name "Developer"
        navig hello greet -n "Developer" --shout
    """
    greeting = f"Hello, {name}!"
    
    if shout:
        greeting = greeting.upper()
    
    ch.success(greeting)
    ch.dim("This is an example NAVIG plugin command.")


@app.command("info")
def info():
    """
    Show plugin information and NAVIG context.
    
    Demonstrates how to access NAVIG's configuration and active host/app.
    """
    from navig.plugins.base import PluginAPI
    from navig.plugins.hello.plugin import name, version, description
    
    api = PluginAPI()
    
    ch.header(f"Plugin: {name} v{version}")
    ch.info(description)
    
    ch.dim("")
    ch.dim("Current NAVIG Context:")
    
    active_host = api.get_active_host()
    active_app = api.get_active_app()
    
    if active_host:
        ch.dim(f"  • Active Host: {active_host}")
    else:
        ch.dim("  • Active Host: (none)")
    
    if active_app:
        ch.dim(f"  • Active App: {active_app}")
    else:
        ch.dim("  • Active App: (none)")


@app.command("remote-test")
def remote_test(
    command: str = typer.Argument("echo 'Hello from remote!'", help="Command to run"),
):
    """
    Run a command on the active remote host.
    
    Demonstrates how to use the PluginAPI for remote execution.
    
    Example:
        navig hello remote-test
        navig hello remote-test "hostname"
        navig hello remote-test "uname -a"
    """
    from navig.plugins.base import PluginAPI
    
    api = PluginAPI()
    
    host = api.get_active_host()
    if not host:
        ch.error("No active host", "Use 'navig host use <name>' to set an active host.")
        raise typer.Exit(1)
    
    ch.dim(f"Running on {host}: {command}")
    
    success, stdout, stderr = api.run_remote(command)
    
    if success:
        if stdout.strip():
            ch.info(stdout.strip())
        ch.success("Command executed successfully")
    else:
        ch.error("Command failed", stderr or "Unknown error")
        raise typer.Exit(1)


@app.command("config-demo")
def config_demo(
    key: Optional[str] = typer.Argument(None, help="Config key to read"),
    value: Optional[str] = typer.Option(None, "--set", "-s", help="Value to set"),
):
    """
    Demonstrate plugin configuration.
    
    Shows how to read/write plugin-specific configuration.
    
    Example:
        navig hello config-demo                    # Show all plugin config
        navig hello config-demo favorite_color    # Get specific key
        navig hello config-demo favorite_color --set blue  # Set key
    """
    from navig.core import Config
    
    config = Config()
    
    if key and value:
        # Set configuration
        config.set_plugin_config("hello", key, value)
        config.save()
        ch.success(f"Set hello.{key} = {value}")
    elif key:
        # Get specific key
        val = config.get_plugin_config("hello", key)
        if val is not None:
            ch.info(f"{key} = {val}")
        else:
            ch.warning(f"Key '{key}' not set")
    else:
        # Show all plugin config
        plugin_config = config.get_plugin_config("hello")
        if plugin_config:
            ch.header("Hello Plugin Configuration")
            for k, v in plugin_config.items():
                ch.dim(f"  {k}: {v}")
        else:
            ch.info("No configuration set for hello plugin")
            ch.dim("Try: navig hello config-demo my_key --set my_value")
